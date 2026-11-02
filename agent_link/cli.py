"""Command lines: `hubctl` (local PC) and `agent-hub` (server)."""
import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import secrets
import shlex
import socket
import subprocess
import sys

from .box import Box, default_box_root
from .client import SpokeClient
from .endpoint import relay
from .hub import CONNECT_TIMEOUT, Hub, HubStore, SSHTransport, close_process, handshake
from .protocol import PROTOCOL_VERSION, ProtocolError, check_name
from .spoke import Spoke, SpokeStore
from .state import Config, default_socket, private_directory, state_root, stderr_log


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


async def ask(socket_path, op, **fields):
    client = SpokeClient(socket_path)
    try:
        await client.connect()
    except OSError:
        raise SystemExit(f'agent-link daemon is not running at {socket_path}') from None
    try:
        return await client.request(op, **fields)
    finally:
        await client.close()


async def run_forever(start, close):
    await start()
    try:
        await asyncio.Event().wait()
    finally:
        await close()


# ---- hubctl ---------------------------------------------------------------------------------
def ssh_options(alias, ssh_config=None):
    argv = ['ssh'] + (['-F', ssh_config] if ssh_config else []) + ['-G', alias]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise SystemExit(f'ssh -G {alias} failed: {result.stderr.strip()}')
    options = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(' ')
        options.setdefault(key, []).append(value)
    return options


def preflight(alias, ssh_config=None):
    """DNS -> TCP before any SSH (5). Through ProxyJump/ProxyCommand only SSH itself can tell."""
    options = ssh_options(alias, ssh_config)
    host, port = options['hostname'][0], int(options['port'][0])
    jump = [v for k in ('proxyjump', 'proxycommand') for v in options.get(k, []) if v != 'none']
    steps = {'hostname': host, 'port': port}
    if jump:
        steps['dns'] = steps['tcp'] = 'skipped (proxy)'
        return steps, options
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        steps['dns'] = 'ok'
    except OSError as error:
        raise SystemExit(f'DNS failed for {host}: {error}. Nothing was changed.') from None
    try:
        socket.create_connection((host, port), timeout=CONNECT_TIMEOUT).close()
        steps['tcp'] = 'ok'
    except OSError as error:
        raise SystemExit(f'TCP {host}:{port} failed: {error}. Nothing was changed.') from None
    return steps, options


def explain_ssh_failure(stderr):
    if 'Host key verification failed' in stderr or 'REMOTE HOST IDENTIFICATION HAS CHANGED' in stderr:
        return 'host key is not trusted. Verify the fingerprint out of band and add it to known_hosts; it is never accepted automatically.'
    if 'Permission denied' in stderr:
        return 'SSH key authentication failed (BatchMode, no passwords).'
    if 'No such file' in stderr or 'not found' in stderr:
        return 'remote path not found. Check --remote-path (absolute path of agent-hub on the server).'
    return 'SSH failed: ' + stderr.strip()[-300:]


def remote_invite(alias, remote_path, ssh_config=None):
    argv = SSHTransport(alias, remote_path, ssh_config=ssh_config).argv()
    argv[-1] = shlex.join([remote_path, 'invite', '--json'])
    # The code arrives on this pipe and stays in memory: never printed, logged or put in argv/env.
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise SystemExit('Invitation failed: ' + explain_ssh_failure(result.stderr))
    try:
        return json.loads(result.stdout)['code']
    except (ValueError, KeyError):
        raise SystemExit('Invitation failed: unexpected output from agent-hub invite') from None


async def one_shot(transport, frame):
    reader, writer, process = await asyncio.wait_for(transport.open(), CONNECT_TIMEOUT)
    try:
        return await handshake(reader, writer, frame)
    except ProtocolError as error:
        stderr = (await process.stderr.read()).decode(errors='replace') if process.stderr else ''
        raise SystemExit(f'{error.code}: {error.detail or explain_ssh_failure(stderr)}') from None
    finally:
        await close_process(writer, process)


def authorized_keys_line(options, remote_path, remote_socket):
    command = shlex.join([remote_path, 'endpoint'] + (['--socket', remote_socket] if remote_socket else []))
    for identity in options.get('identityfile', []):
        public = Path(os.path.expanduser(identity) + '.pub')
        if public.exists():
            return f'restrict,command="{command}" {public.read_text().strip()}'
    return f'restrict,command="{command}" <hub public key>'


async def add_host(args, store):
    if not args.remote_path.startswith('/'):
        raise SystemExit('--remote-path must be the absolute path of agent-hub on the server')
    steps, options = preflight(args.alias, args.ssh_config)
    print(json.dumps({'preflight': steps}), flush=True)
    if args.code:
        code = getpass.getpass('Invitation code from `agent-hub invite`: ')
    else:
        code = remote_invite(args.admin_alias or args.alias, args.remote_path, args.ssh_config)
    token = secrets.token_urlsafe(32)
    transport = SSHTransport(args.alias, args.remote_path, args.remote_socket, args.ssh_config)
    enrolled = await one_shot(transport, {'op': 'enroll', 'code': code, 'token': token})
    name = check_name(enrolled.get('host', ''), 'host')
    if store.host(name):
        raise SystemExit(f'{name} is already registered; remove-host first')
    store.add_host(name, args.alias, args.remote_path, token, args.remote_socket, args.mode, args.ssh_config)
    hello = await one_shot(transport, {'op': 'hello', 'v': PROTOCOL_VERSION, 'token': token})
    if hello.get('op') != 'hello_ok':
        raise SystemExit('Token check after enrollment failed')
    try:
        await ask(default_socket(), 'reload')
        reloaded = True
    except SystemExit:
        reloaded = False
    emit({'host': name, 'alias': args.alias, 'verified': True, 'hub_reloaded': reloaded,
          'authorized_keys': authorized_keys_line(options, args.remote_path, args.remote_socket),
          'note': 'Give the authorized_keys line to the server administrator for the dedicated account. '
                  'agent-link never edits SSH configuration.'})


def hubctl(argv=None):
    parser = argparse.ArgumentParser(prog='hubctl', description='agent-link hub on the local PC')
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve', help='Run the hub (keep it running, e.g. systemd --user)')
    serve.add_argument('--socket', type=Path)
    add = commands.add_parser('add-host', help='Register a server reachable by an existing SSH alias')
    add.add_argument('alias')
    add.add_argument('--remote-path', required=True, help='Absolute path of agent-hub on the server')
    add.add_argument('--remote-socket', help='Daemon socket on the server, if not the default')
    add.add_argument('--admin-alias', help='SSH alias with shell access used only to issue the invitation')
    add.add_argument('--code', action='store_true', help='Enter an invitation code by hand instead')
    add.add_argument('--mode', choices=['stream', 'poll'], default='stream')
    add.add_argument('--ssh-config')
    commands.add_parser('remove-host').add_argument('host')
    commands.add_parser('hosts')
    agents = commands.add_parser('agents')
    agents.add_argument('filter', nargs='?', default='')
    log = commands.add_parser('log', help='Recent routed message metadata')
    log.add_argument('--limit', type=int, default=20)
    args = parser.parse_args(argv)

    root = private_directory(state_root() / 'hub')
    if args.command == 'serve':
        config = Config.load()
        hub = Hub(config, root, stderr_log(root / 'hub.log'), default_box_root('hub'))
        socket_path = args.socket or default_socket()
        try:
            asyncio.run(run_forever(lambda: hub.start(socket_path), hub.close))
        except KeyboardInterrupt:
            pass
        return 0
    store = HubStore(root / 'hub.sqlite3')
    if args.command == 'add-host':
        asyncio.run(add_host(args, store))
    elif args.command == 'remove-host':
        if not store.remove_host(args.host):
            raise SystemExit(f'unknown host {args.host}')
        try:
            asyncio.run(ask(default_socket(), 'reload'))
        except SystemExit:
            pass
        emit({'removed': args.host, 'note': f'Also run `agent-hub revoke` on {args.host}; SSH keys are managed separately.'})
    elif args.command == 'hosts':
        emit(asyncio.run(ask(default_socket(), 'hosts'))['hosts'])
    elif args.command == 'agents':
        emit(asyncio.run(ask(default_socket(), 'list', filter=args.filter))['agents'])
    elif args.command == 'log':
        emit(asyncio.run(ask(default_socket(), 'recent', limit=args.limit))['messages'])
    return 0


# ---- agent-hub ------------------------------------------------------------------------------
def spoke_store():
    return SpokeStore(private_directory(state_root() / 'spoke') / 'spoke.sqlite3')


def agent_hub(argv=None):
    parser = argparse.ArgumentParser(prog='agent-hub', description='agent-link spoke on a server')
    commands = parser.add_subparsers(dest='command', required=True)
    daemon = commands.add_parser('daemon', help='Run the spoke daemon (one per host)')
    daemon.add_argument('--socket', type=Path)
    daemon.add_argument('--host-name', help='Host name in agent IDs (default: config or short hostname)')
    commands.add_parser('endpoint', help='SSH forced command').add_argument('--socket', type=Path)
    invite = commands.add_parser('invite', help='Issue a one-use invitation code (5 minutes)')
    invite.add_argument('--json', action='store_true', help='Machine output for hubctl add-host')
    commands.add_parser('revoke', help='Delete the hub peer token and drop the hub connection')
    commands.add_parser('status').add_argument('--socket', type=Path)
    args = parser.parse_args(argv)

    if args.command == 'endpoint':
        return asyncio.run(relay(args.socket or default_socket()))
    store = spoke_store()
    if args.command == 'daemon':
        config = Config.load(host_name=args.host_name)
        spoke = Spoke(config, store, stderr_log(state_root() / 'spoke' / 'spoke.log'), Box(default_box_root('spoke')))
        spoke.extra_ops = {'drop_link': lambda frame: drop_link(spoke), 'status': lambda frame: status(spoke)}
        try:
            asyncio.run(run_forever(lambda: spoke.start(args.socket or default_socket()), spoke.close))
        except KeyboardInterrupt:
            pass
        return 0
    if args.command == 'invite':
        code, expires = store.invite()
        if args.json:
            print(json.dumps({'code': code, 'expires': expires}), flush=True)
        else:
            print(f'Invitation code (one use, 5 minutes): {code}', flush=True)
    elif args.command == 'revoke':
        removed = store.revoke()
        try:
            asyncio.run(ask(default_socket(), 'drop_link'))
        except SystemExit:
            pass
        emit({'revoked_tokens': removed, 'note': 'Also run `hubctl remove-host` on the hub.'})
    elif args.command == 'status':
        emit(asyncio.run(ask(args.socket or default_socket(), 'status')))
    return 0


async def drop_link(spoke):
    if spoke.link:
        spoke.link.close()
    return {'op': 'ok'}


async def status(spoke):
    return {'op': 'status', 'host': spoke.host, 'hub_connected': spoke.link is not None,
            'outbox': spoke.store.outbox_stats(), 'agents': spoke.list_agents()}


def hubctl_main():
    sys.exit(hubctl())


def agent_hub_main():
    sys.exit(agent_hub())
