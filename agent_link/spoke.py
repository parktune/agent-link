"""Spoke daemon: per-host agent registry, same-host routing (4.2), outbox to the hub and the hub link.

The hub embeds the same Spoke for host `local`; there the uplink is an in-process call instead of
the outbox, and hub links are not accepted.
"""
import asyncio
import base64
from dataclasses import dataclass, field
import grp
import hashlib
import hmac
import itertools
import json
import os
import secrets
import time

from .protocol import (HUB_TO_HOST, PROTOCOL_VERSION, TYPES, FRAME_LIMIT, ProtocolError, agent_id, check_name,
                       check_tool, make_message, metadata, parse_target, read_frame, validate_message, write_frame)
from .state import open_db

INVITE_SECONDS = 300
INVITE_ATTEMPTS = 5
PRESENCE_INTERVAL = 30


def hub_identity(value):
    """Who the hub says it is. Self-reported, so shown for information only, never used for auth."""
    value = value if isinstance(value, dict) else {}
    clean = {}
    for key in ('name', 'user'):
        text = value.get(key)
        if isinstance(text, str) and text and len(text) <= 64 and text.isprintable():
            clean[key] = text
    return clean


def digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


class SpokeStore:
    """Daemon state: token hashes, invitation, known agents, received IDs and the outbox cursor."""

    def __init__(self, path):
        self.db = open_db(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tokens(hash TEXT PRIMARY KEY, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS received(id TEXT PRIMARY KEY, recipient TEXT NOT NULL, sender TEXT NOT NULL,
                type TEXT NOT NULL, hops INTEGER NOT NULL, status TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY, tool TEXT NOT NULL, name TEXT NOT NULL,
                cwd TEXT NOT NULL, session_id TEXT NOT NULL, delivery TEXT NOT NULL,
                started_at REAL NOT NULL, last_seen REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                body TEXT NOT NULL, size INTEGER NOT NULL, at REAL NOT NULL);
        ''')
        # Hub identity recorded at enrollment (self-reported name/user); absent before 0.5.5.
        if 'hub' not in {row[1] for row in self.db.execute('PRAGMA table_info(tokens)')}:
            self.db.execute("ALTER TABLE tokens ADD COLUMN hub TEXT NOT NULL DEFAULT '{}'")
        # 0.4.0 stored an account label per agent; it is no longer used.
        if 'account' in {row[1] for row in self.db.execute('PRAGMA table_info(agents)')}:
            self.db.execute('ALTER TABLE agents DROP COLUMN account')

    def _setting(self, key):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def invite(self, now=None):
        raw = base64.b32encode(secrets.token_bytes(10)).decode()
        expires = (time.time() if now is None else now) + INVITE_SECONDS
        value = json.dumps({'hash': digest(raw), 'expires': expires, 'attempts': 0})
        self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('invitation', value))
        return '-'.join(raw[i:i + 4] for i in range(0, 16, 4)), expires

    def enroll(self, code, token, hub=None):
        if not isinstance(code, str) or not isinstance(token, str) or not 40 <= len(token) <= 128:
            raise ProtocolError('unauthorized', 'invalid enrollment')
        normalized = code.upper().replace('-', '').replace(' ', '')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            invitation = self._setting('invitation')
            if not invitation or invitation['expires'] <= time.time() or invitation['attempts'] >= INVITE_ATTEMPTS:
                raise ProtocolError('unauthorized', 'invitation closed or expired')
            if not hmac.compare_digest(invitation['hash'], digest(normalized)):
                invitation['attempts'] += 1
                self.db.execute("UPDATE settings SET value=? WHERE key='invitation'", (json.dumps(invitation),))
                rejected = True
            else:
                self.db.execute('INSERT OR REPLACE INTO tokens(hash, created, hub) VALUES (?,?,?)',
                                (digest(token), time.time(), json.dumps(hub_identity(hub))))
                self.db.execute("DELETE FROM settings WHERE key='invitation'")
                rejected = False
        if rejected:
            raise ProtocolError('unauthorized', 'invitation code rejected')

    def check_token(self, token):
        """The enrollment record for a valid token (created, hub identity), else None."""
        if not isinstance(token, str) or len(token) > 128:
            return None
        hashed = digest(token)
        for row in self.db.execute('SELECT hash, created, hub FROM tokens'):
            if hmac.compare_digest(hashed, row[0]):
                return {'enrolled_at': row[1], **json.loads(row[2] or '{}')}
        return None

    def enrolled_hubs(self):
        return [{'enrolled_at': row[0], **json.loads(row[1] or '{}')}
                for row in self.db.execute('SELECT created, hub FROM tokens ORDER BY created')]

    def revoke(self):
        with self.db:
            return self.db.execute('DELETE FROM tokens').rowcount

    def received(self, message_id):
        row = self.db.execute('SELECT * FROM received WHERE id=?', (message_id,)).fetchone()
        return dict(row) if row else None

    def remember(self, message, status):
        self.db.execute('INSERT OR IGNORE INTO received VALUES (?,?,?,?,?,?,?)',
                        (message['id'], message['to'], message['from'], message['type'], message['hops'], status, time.time()))
        self.db.execute('DELETE FROM received WHERE at < ?', (time.time() - 30 * 86400,))

    def save_agent(self, agent):
        self.db.execute('INSERT OR REPLACE INTO agents VALUES (?,?,?,?,?,?,?,?)',
                        (agent.id, agent.tool, agent.name, agent.cwd, agent.session_id,
                         agent.delivery, agent.started_at, agent.last_seen))

    def known_agents(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM agents')]

    def agent_for_session(self, session_id):
        row = self.db.execute('SELECT * FROM agents WHERE session_id=? ORDER BY last_seen DESC', (session_id,)).fetchone()
        return dict(row) if row else None

    def forget_agent(self, identifier):
        return self.db.execute('DELETE FROM agents WHERE id=?', (identifier,)).rowcount

    def outbox_put(self, kind, body, max_items, max_bytes):
        data = json.dumps(body, ensure_ascii=False)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            count, size = self.db.execute('SELECT COUNT(*), COALESCE(SUM(size),0) FROM outbox').fetchone()
            if count >= max_items or size + len(data) > max_bytes:
                raise ProtocolError('outbox_full', 'hub unreachable and the outbox is full; nothing was dropped')
            return self.db.execute('INSERT INTO outbox(kind, body, size, at) VALUES (?,?,?,?)',
                                   (kind, data, len(data), time.time())).lastrowid

    def outbox_after(self, seq, limit=None):
        query = 'SELECT seq, kind, body FROM outbox WHERE seq > ? ORDER BY seq'
        rows = self.db.execute(query + (' LIMIT ?' if limit else ''), (seq, limit) if limit else (seq,))
        return [{'op': 'outbox', 'seq': row[0], row[1]: json.loads(row[2])} for row in rows]

    def outbox_ack(self, seq):
        self.db.execute('DELETE FROM outbox WHERE seq <= ?', (seq,))

    def outbox_stats(self):
        count, size = self.db.execute('SELECT COUNT(*), COALESCE(SUM(size),0) FROM outbox').fetchone()
        return {'items': count, 'bytes': size}


@dataclass
class Agent:
    id: str
    host: str
    tool: str
    name: str
    cwd: str = ''
    session_id: str = ''
    delivery: str = 'push'  # push into the open session, or 'box' (mailbox files only)
    started_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    conn: object = None

    @property
    def status(self):
        return 'online' if self.conn else 'offline'

    def public(self):
        return {'id': self.id, 'host': self.host, 'tool': self.tool, 'name': self.name,
                'cwd': self.cwd, 'session_id': self.session_id, 'started_at': self.started_at,
                'last_seen': self.last_seen, 'status': self.status, 'delivery': self.delivery}


class Conn:
    """One adapter (or hubctl) connection. Requests in both directions are correlated by `rid`."""

    _ids = itertools.count(1)

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.lock = asyncio.Lock()
        self.pending = {}
        self.agent = None

    async def send(self, frame):
        async with self.lock:
            await write_frame(self.writer, frame)

    async def request(self, frame, timeout):
        rid = f's{next(self._ids)}'
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        try:
            await self.send({**frame, 'rid': rid})
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise ProtocolError('timeout', 'session adapter did not confirm delivery') from None
        finally:
            self.pending.pop(rid, None)

    def resolve(self, frame):
        future = self.pending.get(frame.get('rid'))
        if future and not future.done():
            future.set_result(frame)

    def close(self):
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ProtocolError('offline', 'session disconnected'))
        self.writer.close()


class Spoke:
    def __init__(self, config, store, log, box, uplink=None, accept_links=True):
        self.config, self.store, self.log, self.box = config, store, log, box
        self.host = check_name(config.host_name, 'host')
        self.uplink = uplink or (OutboxUplink(self) if accept_links else None)
        self.accept_links = accept_links
        # Registered agents survive restarts as offline entries so their mailbox keeps receiving.
        fields = ('id', 'tool', 'name', 'cwd', 'session_id', 'delivery', 'started_at', 'last_seen')
        self.agents = {row['id']: Agent(host=self.host, **{k: row[k] for k in fields}) for row in store.known_agents()}
        self.link = None
        self.extra_ops = {}
        self.list_agents = lambda filter_text='': [a.public() for a in self.agents.values() if matches(a.public(), filter_text)]
        self.server = None
        self._tasks = set()

    # ---- IPC server -------------------------------------------------------------------------
    async def start(self, socket_path):
        socket_path = os.fspath(socket_path)
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        self.server = await asyncio.start_unix_server(self._client, socket_path, limit=FRAME_LIMIT)
        os.chmod(socket_path, self.config.socket_mode)
        if self.config.socket_group:
            os.chown(socket_path, -1, grp.getgrnam(self.config.socket_group).gr_gid)
        self.spawn(self._presence_loop())
        self.log('spoke_started', host=self.host, socket=socket_path)
        return self.server

    async def close(self):
        if self.server:
            self.server.close()
        for task in list(self._tasks):
            task.cancel()
        for agent in self.agents.values():
            if agent.conn:
                agent.conn.close()
        if self.link:
            self.link.close()
        self.store.db.close()

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _client(self, reader, writer):
        try:
            first = await read_frame(reader)
        except ProtocolError:
            writer.close()
            return
        if first is None:
            writer.close()
            return
        if first['op'] in ('hello', 'enroll'):
            if not self.accept_links:
                writer.close()
                return
            await LinkSession(self, reader, writer).run(first)
            return
        conn = Conn(reader, writer)
        try:
            frame = first
            while frame is not None:
                if frame['op'] in ('delivered', 'error') and str(frame.get('rid') or '').startswith('s'):
                    conn.resolve(frame)
                else:
                    self.spawn(self._request(conn, frame))
                frame = await read_frame(reader)
        except (ProtocolError, ConnectionError):
            pass
        finally:
            if conn.agent and conn.agent.conn is conn:
                conn.agent.conn = None
                conn.agent.last_seen = time.time()
                self.store.save_agent(conn.agent)
                self.log('agent_offline', agent=conn.agent.id)
                self.presence_changed()
            conn.close()

    async def _request(self, conn, frame):
        rid = frame.get('rid')
        try:
            result = await self._dispatch(conn, frame)
            await conn.send({**result, 'rid': rid})
        except ProtocolError as error:
            await conn.send(error.frame(rid=rid))
        except (ConnectionError, RuntimeError):
            pass

    async def _dispatch(self, conn, frame):
        op = frame['op']
        if op == 'register':
            return {'op': 'registered', **self.register(conn, frame)}
        if op == 'lookup':
            return {'op': 'lookup', 'agent': self.lookup(frame.get('session_id'))}
        if op == 'list':
            return {'op': 'agents', 'agents': self.list_agents(frame.get('filter') or '')}
        if op in self.extra_ops:
            return await self.extra_ops[op](frame)
        agent = conn.agent
        if agent is None:
            raise ProtocolError('unauthorized', 'register first')
        agent.last_seen = time.time()
        if op == 'heartbeat':
            return {'op': 'ok'}
        if op == 'send':
            return {'op': 'sent', **await self.send_from(agent, frame.get('to'), frame.get('content'),
                                                         frame.get('type') or 'request', frame.get('in_reply_to') or None)}
        if op == 'receive':
            return {'op': 'messages', 'messages': self.box.unread(agent.id, int(frame.get('limit') or 20)),
                    'box': str(self.box.folder(agent.id))}
        if op == 'unregister':
            self.agents.pop(agent.id, None)
            self.store.forget_agent(agent.id)
            conn.agent = None
            self.presence_changed()
            return {'op': 'ok'}
        raise ProtocolError('unauthorized', f'unknown operation {op}')

    def lookup(self, session_id):
        """A restarted MCP process of the same session gets its earlier registration back."""
        if not isinstance(session_id, str) or not session_id:
            return None
        row = self.store.agent_for_session(session_id)
        return {k: row[k] for k in ('name', 'tool', 'delivery')} if row else None

    def register(self, conn, frame):
        tool, name = check_tool(frame.get('tool')), check_name(frame.get('name'))
        identifier = agent_id(self.host, tool, name)
        delivery = frame.get('delivery') or 'push'
        if delivery not in ('push', 'box'):
            raise ProtocolError('invalid', 'delivery must be push or box')
        existing = self.agents.get(identifier)
        if existing and existing.conn and existing.conn is not conn:
            raise ProtocolError('invalid', f'{identifier} is already online in another session; choose another name')
        if conn.agent and conn.agent.id != identifier and conn.agent.conn is conn:
            conn.agent.conn = None  # renaming this session
        text = {k: str(frame.get(k) or '')[:256] for k in ('cwd', 'session_id')}
        agent = Agent(identifier, self.host, tool, name, delivery=delivery, conn=conn, **text)
        self.agents[identifier] = agent
        self.store.save_agent(agent)
        conn.agent = agent
        self.log('agent_online', agent=identifier, delivery=delivery)
        self.presence_changed()
        return {'id': identifier, 'host': self.host, 'box': str(self.box.folder(identifier))}

    # ---- routing ----------------------------------------------------------------------------
    def match(self, tool, name):
        return [a for a in self.agents.values() if a.name == name and (tool is None or a.tool == tool)]

    def authorize(self, is_reply, scope):
        policy = self.config.allow_origination
        if is_reply or policy == 'all' or (policy == 'host' and scope == 'host'):
            return
        raise ProtocolError('origination_denied',
                            f'allow_origination={policy}: this session may only reply to requests it received')

    async def send_from(self, agent, to, content, type, in_reply_to):
        if type not in TYPES:
            raise ProtocolError('invalid', 'type must be request or reply')
        parent = self.store.received(in_reply_to) if in_reply_to else None
        if parent and parent['recipient'] != agent.id:
            parent = None
        if type == 'reply' and parent is None:
            raise ProtocolError('unauthorized', 'in_reply_to must be a message delivered to this session')
        if parent and parent['type'] == 'reply':
            raise ProtocolError('reply_to_reply', 'do not answer a reply')
        hops = parent['hops'] + 1 if parent else 0
        if hops > self.config.max_hops:
            raise ProtocolError('hops_exceeded', f'more than {self.config.max_hops} hops')
        message = make_message(agent.id, to, content, type, in_reply_to, hops)
        validate_message(message, self.config.max_content_bytes)
        is_reply = type == 'reply' and parent is not None
        host, tool, name = parse_target(to)
        if host in (None, self.host):
            found = self.match(tool, name)
            if len(found) > 1:
                raise ProtocolError('ambiguous', f'{len(found)} local sessions are named {name}; use tool:name')
            if found:
                self.authorize(is_reply, 'host')
                message['to'] = found[0].id
                result = await self.deliver_local(message)
                if self.config.mirror_local_metadata and self.uplink:
                    await self.uplink.report(metadata(message))
                self.log('routed_local', id=message['id'], to=message['to'], size=len(content.encode()))
                return {**result, 'route': 'local'}
            if host == self.host or self.uplink is None:
                raise ProtocolError('not_found', f'no session {to} on {self.host}')
        self.authorize(is_reply, 'all')
        return {**await self.uplink.route(message), 'route': 'hub'}

    async def deliver_local(self, message):
        prior = self.store.received(message['id'])
        if prior:
            return {'id': message['id'], 'status': prior['status']}
        validate_message(message, self.config.max_content_bytes)
        if message['hops'] > self.config.max_hops:
            raise ProtocolError('hops_exceeded', f'more than {self.config.max_hops} hops')
        host, tool, name = parse_target(message['to'])
        agent = self.agents.get(message['to']) if host == self.host else None
        if agent is None:
            raise ProtocolError('not_found', f'no session {message["to"]}')
        status = None
        if agent.conn and agent.delivery == 'push':
            try:
                reply = await agent.conn.request({'op': 'deliver', 'msg': message}, self.config.deliver_timeout)
                if reply['op'] == 'error':
                    raise ProtocolError(reply.get('code') or 'inject_failed', reply.get('detail', ''))
                status = 'delivered'
            except ProtocolError as error:
                self.log('push_failed', id=message['id'], to=agent.id, code=error.code)
        if status is None:
            # Mailbox fallback: closed session, box-only session, or a failed push.
            self.box.put(message)
            status = 'stored' if agent.conn else 'stored_offline'
        self.store.remember(message, status)
        self.log('delivered', id=message['id'], to=agent.id, status=status)
        return {'id': message['id'], 'status': status}

    # ---- presence ---------------------------------------------------------------------------
    def presence(self):
        return {'op': 'presence', 'agents': [a.public() for a in self.agents.values()]}

    def presence_changed(self):
        if self.link:
            self.spawn(self.link.send(self.presence()))

    async def _presence_loop(self):
        while True:
            await asyncio.sleep(PRESENCE_INTERVAL)
            self.presence_changed()


def matches(agent, filter_text):
    needle = (filter_text or '').lower()
    return not needle or any(needle in str(agent.get(k, '')).lower() for k in ('id', 'status', 'cwd'))


class OutboxUplink:
    """Server spoke -> hub. Persisted first, pushed when the hub link is up (8.4)."""

    def __init__(self, spoke, wait=10.0):
        self.spoke, self.wait = spoke, wait
        self.waiters = {}

    def _put(self, kind, body):
        config = self.spoke.config
        return self.spoke.store.outbox_put(kind, body, config.outbox_max_items, config.outbox_max_bytes)

    async def route(self, message):
        seq = self._put('msg', message)
        link = self.spoke.link
        if link is None:
            return {'id': message['id'], 'status': 'queued'}
        future = asyncio.get_running_loop().create_future()
        self.waiters[seq] = future
        try:
            await link.send({'op': 'outbox', 'seq': seq, 'msg': message})
            result = await asyncio.wait_for(future, self.wait)
        except (TimeoutError, ConnectionError):
            return {'id': message['id'], 'status': 'queued'}
        finally:
            self.waiters.pop(seq, None)
        if 'error' in result:
            raise ProtocolError(result['error'], result.get('detail', ''))
        return {'id': message['id'], 'status': result.get('status', 'delivered')}

    async def report(self, meta):
        try:
            seq = self._put('meta', meta)
        except ProtocolError:
            self.spoke.log('mirror_dropped', id=meta['id'], reason='outbox_full')
            return
        if self.spoke.link:
            await self.spoke.link.send({'op': 'outbox', 'seq': seq, 'meta': meta})

    def acked(self, seq, result):
        future = self.waiters.get(seq)
        if future and not future.done():
            future.set_result(result or {})


def peer(frame):
    """SSH client address the endpoint attached (from SSH_CONNECTION), if any."""
    value = frame.get('peer')
    return value if isinstance(value, str) and len(value) <= 128 and value.isprintable() else None


class LinkSession:
    """The authenticated hub connection, relayed by the SSH endpoint (8.2, 8.3)."""

    def __init__(self, spoke, reader, writer):
        self.spoke, self.reader, self.writer = spoke, reader, writer
        self.lock = asyncio.Lock()
        self.info = {}

    async def send(self, frame):
        async with self.lock:
            await write_frame(self.writer, frame)

    def close(self):
        self.writer.close()

    async def run(self, first):
        spoke = self.spoke
        try:
            if first['op'] == 'enroll':
                spoke.store.enroll(first.get('code'), first.get('token'), first.get('hub'))
                spoke.log('hub_enrolled', host=spoke.host, hub=hub_identity(first.get('hub')), peer=peer(first))
                await self.send({'op': 'enroll_ok', 'host': spoke.host})
                return
            if first.get('v') != PROTOCOL_VERSION:
                raise ProtocolError('unsupported_version', f'expected protocol {PROTOCOL_VERSION}')
            record = spoke.store.check_token(first.get('token'))
            if record is None:
                raise ProtocolError('unauthorized', 'peer token rejected')
            # The name/user the hub reports now wins over the one recorded at enrollment.
            self.info = {**record, **hub_identity(first.get('hub')), 'from': peer(first),
                         'connected_since': time.time()}
            if spoke.link:
                spoke.link.close()  # one hub connection per host; the newest wins
            spoke.link = self
            await self.send({'op': 'hello_ok', 'v': PROTOCOL_VERSION, 'host': spoke.host,
                             'caps': ['stream', 'exchange', 'mirror']})
            spoke.log('hub_connected', host=spoke.host, hub=self.info, outbox=spoke.store.outbox_stats())
            for frame in spoke.store.outbox_after(0):
                await self.send(frame)
            await self.send(spoke.presence())
            while (frame := await read_frame(self.reader)) is not None:
                await self.handle(frame)
        except ProtocolError as error:
            spoke.log('hub_rejected', code=error.code)
            try:
                await self.send(error.frame())
            except ConnectionError:
                pass
        except ConnectionError:
            pass
        finally:
            if spoke.link is self:
                spoke.link = None
                spoke.log('hub_disconnected', host=spoke.host)
            self.close()

    async def handle(self, frame):
        op, spoke = frame['op'], self.spoke
        if op not in HUB_TO_HOST or op in ('hello', 'enroll'):
            raise ProtocolError('unauthorized', f'frame {op} not allowed')
        if op == 'ping':
            await self.send({'op': 'pong'})
        elif op == 'ack':
            seq = int(frame.get('seq', 0))
            spoke.store.outbox_ack(seq)
            if isinstance(spoke.uplink, OutboxUplink):
                spoke.uplink.acked(seq, frame.get('result'))
        elif op == 'deliver':
            spoke.spawn(self._deliver(frame.get('msg')))
        elif op == 'exchange':
            after = int(frame.get('after', 0))
            spoke.store.outbox_ack(after)
            results = [await self._result(message) for message in frame.get('deliver') or []]
            await self.send({'op': 'exchange_ok', 'results': results, 'outbox': spoke.store.outbox_after(after, 100),
                             'presence': spoke.presence()['agents']})

    async def _result(self, message):
        try:
            return {'op': 'delivered', **await self.spoke.deliver_local(message)}
        except ProtocolError as error:
            return error.frame(id=message.get('id') if isinstance(message, dict) else None)

    async def _deliver(self, message):
        try:
            await self.send(await self._result(message))
        except ConnectionError:
            pass
