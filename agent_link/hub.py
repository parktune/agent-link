"""Local hub: global registry, routing between hosts and one connection loop per host (4, 8)."""
import asyncio
import getpass
import os
from dataclasses import replace
from pathlib import Path
import random
import socket
import time

from .protocol import (FRAME_LIMIT, LOCAL_HOST, PROTOCOL_VERSION, ProtocolError, encode, parse_target, read_frame,
                       validate_message)
from .box import Box
from .spoke import Spoke, SpokeStore, directory_entries, matches
from .state import open_db

CONNECT_TIMEOUT = 5
HELLO_TIMEOUT = 5
PING_INTERVAL = 15
IDLE_LIMIT = 45
BACKOFF_MAX = 60


def hub_identity():
    """What this hub tells servers about itself, shown in `agent-link status` on the server."""
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = ''
    return {'name': socket.gethostname(), 'user': user}


class HubStore:
    def __init__(self, path):
        self.db = open_db(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS hosts(name TEXT PRIMARY KEY, alias TEXT NOT NULL, remote_path TEXT NOT NULL,
                socket TEXT, token TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'stream', ssh_config TEXT,
                registered_at REAL NOT NULL, last_seq INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, sender TEXT NOT NULL, recipient TEXT NOT NULL,
                type TEXT NOT NULL, in_reply_to TEXT, hops INTEGER NOT NULL, size INTEGER NOT NULL, at REAL NOT NULL,
                route TEXT NOT NULL, status TEXT NOT NULL);
        ''')
        # Before 0.6.0 the server command was `agent-hub`; it is `agent-link` in the same folder now.
        self.db.execute("UPDATE hosts SET remote_path = substr(remote_path, 1, length(remote_path) - 9) || 'agent-link' "
                        "WHERE remote_path LIKE '%/agent-hub'")

    def hosts(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM hosts ORDER BY name')]

    def host(self, name):
        row = self.db.execute('SELECT * FROM hosts WHERE name=?', (name,)).fetchone()
        return dict(row) if row else None

    def add_host(self, name, alias, remote_path, token, socket=None, mode='stream', ssh_config=None):
        self.db.execute('INSERT INTO hosts(name, alias, remote_path, socket, token, mode, ssh_config, registered_at) '
                        'VALUES (?,?,?,?,?,?,?,?)', (name, alias, remote_path, socket, token, mode, ssh_config, time.time()))

    def remove_host(self, name):
        return self.db.execute('DELETE FROM hosts WHERE name=?', (name,)).rowcount

    def last_seq(self, name):
        row = self.db.execute('SELECT last_seq FROM hosts WHERE name=?', (name,)).fetchone()
        return row[0] if row else 0

    def set_last_seq(self, name, seq):
        self.db.execute('UPDATE hosts SET last_seq=MAX(last_seq, ?) WHERE name=?', (seq, name))

    def message(self, message_id):
        row = self.db.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone()
        return dict(row) if row else None

    def record(self, meta, route, status):
        """Metadata only: the hub log never stores message bodies."""
        self.db.execute('INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (meta['id'], meta['from'], meta['to'], meta['type'], meta.get('in_reply_to'), meta.get('hops', 0),
                         meta['size'], time.time(), route, status))
        self.db.execute('DELETE FROM messages WHERE rowid <= (SELECT MAX(rowid) - 10000 FROM messages)')

    def recent(self, limit=20):
        rows = self.db.execute('SELECT * FROM messages ORDER BY at DESC LIMIT ?', (max(1, min(int(limit), 500)),))
        return [dict(row) for row in rows]


class Transport:
    """Opens one bidirectional JSON Lines stream to a host's endpoint (8.2)."""

    def argv(self):
        raise NotImplementedError

    async def open(self):
        process = await asyncio.create_subprocess_exec(
            *self.argv(), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=FRAME_LIMIT)
        return process.stdout, process.stdin, process


class CommandTransport(Transport):
    def __init__(self, argv):
        self._argv = list(argv)

    def argv(self):
        return self._argv


class SSHTransport(Transport):
    """System OpenSSH: known_hosts, ProxyJump and keys come from the user's SSH config (ADR 0002)."""

    def __init__(self, alias, remote_path, socket=None, ssh_config=None):
        self.alias, self.remote_path, self.socket, self.ssh_config = alias, remote_path, socket, ssh_config

    def argv(self):
        argv = ['ssh'] + (['-F', self.ssh_config] if self.ssh_config else [])
        argv += ['-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ForwardAgent=no',
                 '-o', 'ClearAllForwardings=yes', '-o', 'PermitLocalCommand=no', '-o', 'RemoteCommand=none',
                 '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', f'ConnectTimeout={CONNECT_TIMEOUT}',
                 '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '--', self.alias]
        command = [self.remote_path, 'endpoint'] + (['--socket', self.socket] if self.socket else [])
        import shlex
        return argv + [shlex.join(command)]


def transport_for(record):
    return SSHTransport(record['alias'], record['remote_path'], record.get('socket'), record.get('ssh_config'))


async def close_process(writer, process):
    try:
        writer.close()
    except (ConnectionError, RuntimeError):
        pass
    if process and process.returncode is None:
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), 3)
        except (ProcessLookupError, TimeoutError):
            try:
                process.kill()
            except ProcessLookupError:
                pass


async def handshake(reader, writer, frame):
    writer.write(encode(frame))
    await writer.drain()
    try:
        reply = await asyncio.wait_for(read_frame(reader), HELLO_TIMEOUT)
    except TimeoutError:
        raise ProtocolError('timeout', 'no hello response') from None
    if reply is None:
        raise ProtocolError('offline', 'endpoint closed the connection')
    if reply['op'] == 'error':
        raise ProtocolError(reply.get('code') or 'unauthorized', reply.get('detail', ''))
    return reply


class HostLink:
    """Long-lived stream to one host. Independent task per host; exponential backoff with jitter."""

    def __init__(self, hub, record, transport):
        self.hub, self.name, self.transport = hub, record['name'], transport
        self.token = record['token']
        self.caps, self.directory_sent = set(), None
        self.connected = False
        self.reconnects = 0
        self.last_error = self.last_error_detail = None
        self.pending = {}
        self.writer = None
        self.lock = asyncio.Lock()
        self.task = None

    def status(self):
        return {'host': self.name, 'connected': self.connected, 'reconnects': self.reconnects,
                'last_error': self.last_error, 'last_error_detail': self.last_error_detail}

    def hello(self):
        # `host` tells the server the name it was paired under, so it can keep it or explain a mismatch.
        return {'op': 'hello', 'v': PROTOCOL_VERSION, 'token': self.token, 'hub': hub_identity(), 'host': self.name}

    def check_hello(self, hello):
        self.caps = set(hello.get('caps') or []) if isinstance(hello.get('caps'), list) else set()
        if hello['op'] != 'hello_ok':
            raise ProtocolError('unauthorized', f'unexpected hello from {self.name}')
        if hello.get('host') != self.name:  # a server older than 0.5.7 does not check this itself
            raise ProtocolError('host_mismatch', f'{self.name} answers as host "{hello.get("host")}" but was paired as '
                                f'"{self.name}"; restart its daemon with the paired name, or pair again')

    def start(self):
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def run(self):
        delay = 1
        while True:
            started = time.monotonic()
            try:
                await self.session()
            except asyncio.CancelledError:
                raise
            except (ProtocolError, OSError, ConnectionError, TimeoutError, ValueError) as error:
                self.last_error = getattr(error, 'code', type(error).__name__)
                self.last_error_detail = getattr(error, 'detail', None) or str(error) or None
            self.disconnected()
            if time.monotonic() - started > BACKOFF_MAX:
                delay = 1
            wait = delay * random.uniform(0.8, 1.2)
            self.hub.log('host_backoff', host=self.name, seconds=round(wait, 2), error=self.last_error,
                         **({'detail': self.last_error_detail} if self.last_error == 'host_mismatch' else {}))
            await asyncio.sleep(wait)
            delay = min(delay * 2, BACKOFF_MAX)

    def disconnected(self):
        if self.connected:
            self.hub.log('host_disconnected', host=self.name)
        self.connected, self.writer = False, None
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ProtocolError('offline', f'{self.name} disconnected'))
        self.pending.clear()
        self.hub.host_offline(self.name)

    async def send(self, frame):
        if not self.writer:
            raise ProtocolError('offline', f'{self.name} is not connected')
        async with self.lock:
            self.writer.write(encode(frame))
            await self.writer.drain()

    async def session(self):
        reader, writer, process = await asyncio.wait_for(self.transport.open(), CONNECT_TIMEOUT)
        stderr_task = asyncio.create_task(drain(process))
        try:
            hello = await handshake(reader, writer, self.hello())
            self.check_hello(hello)
            self.writer, self.connected = writer, True
            self.reconnects += 1
            self.last_error = self.last_error_detail = None
            self.directory_sent = None
            self.hub.publish_directory()
            self.hub.log('host_connected', host=self.name, connections=self.reconnects)
            queue = asyncio.Queue()
            worker = asyncio.create_task(self.process_outbox(queue))
            pinger = asyncio.create_task(self.keepalive())
            self.last_rx = time.monotonic()
            try:
                while (frame := await read_frame(reader)) is not None:
                    self.last_rx = time.monotonic()
                    await self.dispatch(frame, queue)
            finally:
                worker.cancel()
                pinger.cancel()
                await asyncio.gather(worker, pinger, return_exceptions=True)
        finally:
            await close_process(writer, process)
            stderr_task.cancel()

    async def dispatch(self, frame, queue):
        op = frame['op']
        if op == 'outbox':
            queue.put_nowait(frame)
        elif op == 'presence':
            self.hub.update_presence(self.name, frame.get('agents') or [])
        elif op in ('delivered', 'error'):
            future = self.pending.get(frame.get('id'))
            if future and not future.done():
                future.set_result(frame)

    async def process_outbox(self, queue):
        # Sequential per host so the `seq` cursor stays monotonic; the reader keeps running meanwhile.
        while True:
            frame = await queue.get()
            ack = await self.hub.handle_outbox(self.name, frame)
            await self.send(ack)

    async def keepalive(self):
        while True:
            await asyncio.sleep(PING_INTERVAL)
            if time.monotonic() - self.last_rx > IDLE_LIMIT:
                self.hub.log('host_keepalive_timeout', host=self.name)
                self.writer.close()
                return
            await self.send({'op': 'ping'})

    async def deliver(self, message, timeout):
        if not self.connected:
            raise ProtocolError('offline', f'{self.name} is not connected')
        future = asyncio.get_running_loop().create_future()
        self.pending[message['id']] = future
        try:
            await self.send({'op': 'deliver', 'msg': message})
            reply = await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise ProtocolError('timeout', f'{self.name} did not confirm delivery') from None
        finally:
            self.pending.pop(message['id'], None)
        return reply


async def drain(process):
    # Endpoint stderr is discarded: it may echo remote details we do not want in hub logs.
    if process and process.stderr:
        while await process.stderr.read(4096):
            pass


class HubUplink:
    def __init__(self, hub):
        self.hub = hub

    async def route(self, message):
        return await self.hub.route(message, LOCAL_HOST)


class Hub:
    def __init__(self, config, root, log, box_root, transport_factory=transport_for):
        self.config, self.log = config, log
        self.root = Path(root)
        self.store = HubStore(self.root / 'hub.sqlite3')
        # The local PC drives the system: its sessions may always originate requests.
        local_config = replace(config, host_name=LOCAL_HOST, allow_origination='all')
        self.local = Spoke(local_config, SpokeStore(self.root / 'local.sqlite3'), log, Box(box_root),
                           uplink=HubUplink(self), accept_links=False)
        self.local.list_agents = self.list_agents
        self.local.on_presence = self.publish_directory
        self.local.remove = self.remove  # the PC's `agent-link remove` reaches every host
        self.local.others = lambda: [a for host in self.presence.values() for a in host]
        self.local.extra_ops = {'hosts': self._hosts_op, 'recent': self._recent_op, 'reload': self._reload_op,
                                'status': self._status_op}
        self.transport_factory = transport_factory
        self.links = {}
        self.presence = {}

    async def start(self, socket_path):
        await self.local.start(socket_path)
        for record in self.store.hosts():
            self.add_link(record)

    async def close(self):
        for link in self.links.values():
            await link.stop()
        await self.local.close()
        self.store.db.close()

    def add_link(self, record):
        link = HostLink(self, record, self.transport_factory(record))
        self.links[record['name']] = link
        link.start()
        return link

    # ---- registry ---------------------------------------------------------------------------
    def update_presence(self, host, agents):
        clean = []
        for agent in agents:
            if isinstance(agent, dict) and agent.get('host') == host:
                clean.append({k: agent.get(k) for k in ('id', 'host', 'tool', 'name', 'cwd', 'session_id',
                                                           'started_at', 'last_seen', 'status', 'delivery')})
        self.presence[host] = clean
        self.publish_directory()

    def host_offline(self, host):
        for agent in self.presence.get(host, []):
            agent['status'] = 'offline'
        self.publish_directory()

    def publish_directory(self):
        """Tell each connected server about the agents on every other host, when that list changed."""
        agents = self.all_agents()
        for name, link in self.links.items():
            if not link.connected or 'directory' not in link.caps:
                continue  # servers before 0.6.5 would drop the connection on an unknown frame
            entries = directory_entries(agents, exclude_host=name)
            if entries != link.directory_sent:
                link.directory_sent = entries
                asyncio.get_running_loop().create_task(self._send_directory(link, entries))

    async def _send_directory(self, link, entries):
        try:
            await link.send({'op': 'directory', 'agents': entries})
        except (ProtocolError, ConnectionError, OSError):
            link.directory_sent = None  # resent on the next change or reconnect

    def all_agents(self):
        agents = [a.public() for a in self.local.agents.values()]
        for host in sorted(self.presence):
            agents.extend(self.presence[host])
        return agents

    def list_agents(self, filter_text=''):
        return [a for a in self.all_agents() if matches(a, filter_text)]

    async def _hosts_op(self, frame):
        hosts = []
        for record in self.store.hosts():
            link = self.links.get(record['name'])
            hosts.append({'name': record['name'], 'alias': record['alias'],
                          'registered_at': record['registered_at'], 'last_seq': record['last_seq'],
                          **(link.status() if link else {'connected': False})})
        return {'op': 'hosts', 'hosts': hosts, 'pid': os.getpid()}

    async def _status_op(self, frame):
        hosts = (await self._hosts_op(frame))['hosts']
        return {'op': 'status', 'role': 'hub', 'host': LOCAL_HOST, 'pid': os.getpid(),
                'hosts_connected': sum(1 for h in hosts if h.get('connected')), 'hosts': hosts,
                'agents': self.all_agents()}

    async def _reload_op(self, frame):
        records = {record['name']: record for record in self.store.hosts()}
        for name in list(self.links):
            if name not in records:
                await self.links.pop(name).stop()
                self.presence.pop(name, None)
        for name, record in records.items():
            if name not in self.links:
                self.add_link(record)
        return {'op': 'ok', 'hosts': sorted(records)}

    async def _recent_op(self, frame):
        return {'op': 'recent', 'messages': self.store.recent(frame.get('limit') or 20)}

    # ---- routing ----------------------------------------------------------------------------
    async def route(self, message, origin):
        prior = self.store.message(message['id'])
        if prior and prior['status'] in ('delivered', 'stored', 'stored_offline'):
            return {'id': message['id'], 'status': prior['status']}
        validate_message(message, self.config.max_content_bytes)
        if message['hops'] > self.config.max_hops:
            raise ProtocolError('hops_exceeded', f'more than {self.config.max_hops} hops')
        parent = self.store.message(message['in_reply_to']) if message['in_reply_to'] else None
        if parent and parent['type'] == 'reply':
            raise ProtocolError('reply_to_reply', 'do not answer a reply')
        host, tool, name = parse_target(message['to'])
        if host is None:
            found = [a for a in self.all_agents() if a['name'] == name and (tool is None or a['tool'] == tool)
                     and a['host'] != origin]
            if len(found) > 1:
                raise ProtocolError('ambiguous', f'{", ".join(a["id"] for a in found)} are all named {name}; '
                                                 f'use claude:{name} or codex:{name}')
            if not found:
                raise ProtocolError('not_found', f'no session named {message["to"]}')
            host, message = found[0]['host'], {**message, 'to': found[0]['id']}
        elif host != LOCAL_HOST and not any(a['id'] == message['to'] for a in self.presence.get(host, [])):
            raise ProtocolError('not_found', f'no session {message["to"]}')
        meta = {**{k: message[k] for k in ('id', 'from', 'to', 'type', 'in_reply_to', 'hops')},
                'size': len(message['content'].encode())}
        try:
            if host == LOCAL_HOST:
                result = await self.local.deliver_local(message)
            else:
                link = self.links.get(host)
                if link is None:
                    raise ProtocolError('not_found', f'unknown host {host}')
                if not link.connected:
                    raise ProtocolError('offline', f'host {host} is not connected')
                reply = await link.deliver(message, self.config.deliver_timeout)
                if reply['op'] == 'error':
                    raise ProtocolError(reply.get('code') or 'offline', reply.get('detail', ''))
                result = {'id': message['id'], 'status': reply.get('status', 'delivered')}
        except ProtocolError as error:
            self.store.record(meta, f'{origin}->{host}', error.code)
            self.log('route_failed', id=message['id'], to=message['to'], code=error.code)
            raise
        self.store.record(meta, f'{origin}->{host}', result['status'])
        self.log('routed', id=message['id'], origin=origin, to=message['to'], size=meta['size'])
        return result

    async def remove(self, target):
        """Remove a session by name, tool:name or full ID, wherever it is registered."""
        host, tool, name = parse_target(target)
        found = [a for a in self.all_agents() if a['name'] == name and (tool is None or a['tool'] == tool)
                 and (host is None or a['host'] == host)]
        if len(found) != 1:
            raise ProtocolError('ambiguous' if found else 'not_found', f'{len(found)} sessions match {target}')
        agent = found[0]
        if agent['host'] == LOCAL_HOST:
            return self.local.remove_agent(agent['id'])
        link = self.links.get(agent['host'])
        if link is None or not link.connected:
            raise ProtocolError('offline', f'{agent["host"]} is not connected; try again when it is')
        if 'forget' not in link.caps:
            raise ProtocolError('unsupported_version', f'{agent["host"]} runs an older agent-link; run agent-link update')
        await link.send({'op': 'forget', 'id': agent['id']})
        self.presence[agent['host']] = [a for a in self.presence.get(agent['host'], []) if a['id'] != agent['id']]
        self.publish_directory()
        self.log('agent_removed', agent=agent['id'], via='hub')
        return {'removed': agent['id'], 'was': 'open' if agent.get('status') == 'online' else 'closed'}

    async def handle_outbox(self, host, frame):
        seq = int(frame.get('seq', 0))
        if seq <= self.store.last_seq(host):
            return {'op': 'ack', 'seq': seq}  # already processed before a reconnect or hub restart
        if 'remove' in frame:  # `agent-link remove` on a server for a session on another host
            try:
                result = await self.remove(str((frame['remove'] or {}).get('target') or ''))
            except ProtocolError as error:
                result = {'error': error.code, 'detail': error.detail}
            self.store.set_last_seq(host, seq)
            return {'op': 'ack', 'seq': seq, 'result': result}
        result = None
        message = frame.get('msg')
        try:
            validate_message(message, self.config.max_content_bytes)
            if parse_target(message['from'])[0] != host:
                raise ProtocolError('unauthorized', 'sender does not belong to this host')
            result = await self.route(message, host)
        except ProtocolError as error:
            result = {'id': message.get('id') if isinstance(message, dict) else None,
                      'error': error.code, 'detail': error.detail}
        self.store.set_last_seq(host, seq)
        return {'op': 'ack', 'seq': seq, 'result': result}

