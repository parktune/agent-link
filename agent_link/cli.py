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

from .box import Box, default_box_root, wait_for_mail
from .client import SpokeClient
from .endpoint import relay
from .hub import CONNECT_TIMEOUT, Hub, HubStore, SSHTransport, close_process, handshake
from .protocol import NAME, PROTOCOL_VERSION, ProtocolError, check_name
from . import service
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


# Where `agent-hub` usually lives on a server. The first executable match wins.
SEARCH = ('"$(command -v agent-hub 2>/dev/null)"', '"$HOME/work/agent-link/.venv/bin/agent-hub"',
          '"$HOME/agent-link/.venv/bin/agent-hub"', '"$HOME/.local/bin/agent-hub"')


def remote_up_command(remote_path, host_name):
    up = ['up', '--json', '--invite'] + (['--host-name', host_name] if host_name else [])
    if remote_path:
        return shlex.join([remote_path] + up)
    args = shlex.join(up)
    return (f'for p in {" ".join(SEARCH)}; do if [ -n "$p" ] && [ -x "$p" ]; then '
            f'exec "$(readlink -f "$p")" {args}; fi; done; echo agent-hub-not-found >&2; exit 3')


def remote_up(alias, remote_path, host_name, ssh_config=None):
    """One SSH call: find agent-hub, start its daemon if needed, issue a one-use invitation.

    The code arrives on this pipe and stays in memory: never printed, logged or put in argv/env.
    """
    argv = SSHTransport(alias, '/', ssh_config=ssh_config).argv()
    argv[-1] = remote_up_command(remote_path, host_name)
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if 'agent-hub-not-found' in result.stderr:
        raise SystemExit(f'agent-link is not installed on {alias}. On the server run:\n'
                         '  git clone https://github.com/parktune/agent-link.git ~/work/agent-link && '
                         '~/work/agent-link/setup.sh\n'
                         'or pass --remote-path if it lives elsewhere.')
    if result.returncode:
        raise SystemExit(f'{alias}: ' + explain_ssh_failure(result.stderr))
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise SystemExit(f'{alias}: unexpected output from agent-hub up') from None


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


def say(text):
    print(text, flush=True)


async def add_host(args, store):
    if args.remote_path and not args.remote_path.startswith('/'):
        raise SystemExit('--remote-path must be an absolute path on the server')
    if any(record['alias'] == args.alias for record in store.hosts()):
        raise SystemExit(f'{args.alias} is already registered (hubctl hosts); remove-host first to redo it')
    host_name = args.name or (args.alias if NAME.fullmatch(args.alias) else None)
    steps, options = preflight(args.alias, args.ssh_config)
    say(f'[1/4] {args.alias}: DNS {steps["dns"]}, TCP {steps["tcp"]}')
    if args.code:
        if not args.remote_path:
            raise SystemExit('--code needs --remote-path (no SSH shell is used to look it up)')
        remote = {'path': args.remote_path, 'host': None, 'started': None,
                  'code': getpass.getpass('Invitation code from `agent-hub invite`: ')}
    else:
        remote = remote_up(args.admin_alias or args.alias, args.remote_path, host_name, args.ssh_config)
    remote_path = args.remote_path or remote['path']
    started = {None: 'already running', 'systemd': 'started (systemd user service)',
               'background': 'started (background process; add a systemd user service to survive reboots)'}
    say(f'[2/4] server daemon {started.get(remote["started"], remote["started"])} at {remote_path}')
    token = secrets.token_urlsafe(32)
    transport = SSHTransport(args.alias, remote_path, args.remote_socket, args.ssh_config)
    enrolled = await one_shot(transport, {'op': 'enroll', 'code': remote['code'], 'token': token})
    name = check_name(enrolled.get('host', ''), 'host')
    if store.host(name):
        raise SystemExit(f'host name {name} is already registered; remove-host first')
    store.add_host(name, args.alias, remote_path, token, args.remote_socket, args.mode, args.ssh_config)
    hello = await one_shot(transport, {'op': 'hello', 'v': PROTOCOL_VERSION, 'token': token})
    if hello.get('op') != 'hello_ok':
        raise SystemExit('Token check after enrollment failed')
    say(f'[3/4] enrolled as host "{name}" (token verified)')
    how = None if service.alive() else service.start('hub')
    await ask(default_socket(), 'reload')
    connected = False
    for _ in range(50):
        hosts = (await ask(default_socket(), 'hosts'))['hosts']
        if any(h['name'] == name and h.get('connected') for h in hosts):
            connected = True
            break
        await asyncio.sleep(0.2)
    say(f'[4/4] hub {"started and " if how else ""}{"connected to" if connected else "connecting to"} {name}')
    say(f'\nDone. Sessions on that server have IDs like {name}:claude:<name>.')
    say('Optional hardening: on the server, give a dedicated account this authorized_keys line:\n  '
        + authorized_keys_line(options, remote_path, args.remote_socket))


def add_host_args(alias, ssh_config=None):
    return argparse.Namespace(alias=alias, name=None, remote_path=None, remote_socket=None, admin_alias=None,
                              code=False, mode='stream', ssh_config=ssh_config)


def discover_hosts(args, store):
    from .discover import pick, ssh_hosts
    hosts = ssh_hosts(args.ssh_config)
    registered = {record['alias'] for record in store.hosts()}
    if args.hosts:
        known = {h['alias'] for h in hosts}
        chosen = [a.strip() for a in args.hosts.split(',') if a.strip()]
        unknown = [a for a in chosen if a not in known]
        if unknown:
            raise SystemExit(f'not in SSH config: {", ".join(unknown)}')
        chosen = [a for a in chosen if a not in registered]
    else:
        if not hosts:
            raise SystemExit('No Host entries found in ' + (args.ssh_config or '~/.ssh/config'))
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise SystemExit('discover needs a terminal; use --hosts alias1,alias2 instead')
        chosen = pick(hosts, registered)
        if chosen is None:
            say('Cancelled. Nothing was changed.')
            return 0
    if not chosen:
        say('No new servers selected.')
        return 0
    results = {}
    for alias in chosen:
        say(f'\n=== {alias} ===')
        try:
            asyncio.run(add_host(add_host_args(alias, args.ssh_config), store))
            results[alias] = 'connected'
        except SystemExit as error:
            say(f'failed: {error}')
            results[alias] = f'failed: {str(error).splitlines()[0]}'
    say('\nSummary:')
    for alias, outcome in results.items():
        say(f'  {alias}: {outcome}')
    return 0 if all(v == 'connected' for v in results.values()) else 1


def hubctl(argv=None):
    parser = argparse.ArgumentParser(prog='hubctl', description='agent-link hub on the local PC')
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve', help='Run the hub (keep it running, e.g. systemd --user)')
    serve.add_argument('--socket', type=Path)
    commands.add_parser('up', help='Start the hub in the background (systemd user service if possible)')
    add = commands.add_parser('add-host', help='Connect a server by its SSH alias (from ~/.ssh/config)')
    add.add_argument('alias')
    add.add_argument('--name', help='Host name in agent IDs (default: the alias)')
    add.add_argument('--remote-path', help='agent-hub on the server, if not found automatically')
    add.add_argument('--remote-socket', help='Daemon socket on the server, if not the default')
    add.add_argument('--admin-alias', help='SSH alias with shell access used only to issue the invitation')
    add.add_argument('--code', action='store_true', help='Enter an invitation code by hand instead')
    add.add_argument('--mode', choices=['stream', 'poll'], default='stream')
    add.add_argument('--ssh-config')
    discover = commands.add_parser('discover', help='Pick servers from ~/.ssh/config (Space to pick, Enter to apply)')
    discover.add_argument('--ssh-config', help='SSH config to read (default ~/.ssh/config)')
    discover.add_argument('--hosts', help='Comma-separated aliases instead of the interactive picker')
    commands.add_parser('remove-host').add_argument('host')
    commands.add_parser('hosts').add_argument('--pid', action='store_true', help='Print only the hub process ID')
    agents = commands.add_parser('agents')
    agents.add_argument('filter', nargs='?', default='')
    log = commands.add_parser('log', help='Recent routed message metadata')
    log.add_argument('--limit', type=int, default=20)
    args = parser.parse_args(argv)

    root = private_directory(state_root() / 'hub')
    if args.command == 'up':
        emit({'hub': 'already running' if service.alive() else service.start('hub')})
        return 0
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
    elif args.command == 'discover':
        return discover_hosts(args, store)
    elif args.command == 'remove-host':
        if not store.remove_host(args.host):
            raise SystemExit(f'unknown host {args.host}')
        try:
            asyncio.run(ask(default_socket(), 'reload'))
        except SystemExit:
            pass
        emit({'removed': args.host, 'note': f'Also run `agent-hub revoke` on {args.host}; SSH keys are managed separately.'})
    elif args.command == 'hosts':
        reply = asyncio.run(ask(default_socket(), 'hosts'))
        print(reply['pid']) if args.pid else emit(reply['hosts'])
    elif args.command == 'agents':
        emit(asyncio.run(ask(default_socket(), 'list', filter=args.filter))['agents'])
    elif args.command == 'log':
        emit(asyncio.run(ask(default_socket(), 'recent', limit=args.limit))['messages'])
    return 0


# ---- agent-hub ------------------------------------------------------------------------------
def configured_host_name():
    path = state_root() / 'config.toml'
    if not path.exists():
        return None
    import tomllib
    with path.open('rb') as handle:
        return tomllib.load(handle).get('host_name') or None


def spoke_store():
    return SpokeStore(private_directory(state_root() / 'spoke') / 'spoke.sqlite3')


def agent_hub(argv=None):
    parser = argparse.ArgumentParser(prog='agent-hub', description='agent-link spoke on a server')
    commands = parser.add_subparsers(dest='command', required=True)
    daemon = commands.add_parser('daemon', help='Run the spoke daemon (one per host)')
    daemon.add_argument('--socket', type=Path)
    daemon.add_argument('--host-name', help='Host name in agent IDs (default: config or short hostname)')
    up = commands.add_parser('up', help='Start the daemon in the background if it is not running')
    up.add_argument('--host-name', help='Host name in agent IDs when starting (config host_name wins)')
    up.add_argument('--invite', action='store_true', help='Also issue a one-use invitation (for hubctl add-host)')
    up.add_argument('--json', action='store_true')
    commands.add_parser('endpoint', help='SSH forced command').add_argument('--socket', type=Path)
    invite = commands.add_parser('invite', help='Issue a one-use invitation code (5 minutes)')
    invite.add_argument('--json', action='store_true', help='Machine output for hubctl add-host')
    commands.add_parser('revoke', help='Delete the hub peer token and drop the hub connection')
    commands.add_parser('status').add_argument('--socket', type=Path)
    wait = commands.add_parser('wait-box', help='Wait until a mailbox folder has unread mail, print one line, exit')
    wait.add_argument('--box', type=Path, required=True, help='The received/ folder shown by whoami')
    wait.add_argument('--interval', type=float, default=2.0, help='Seconds between checks')
    args = parser.parse_args(argv)

    if args.command == 'wait-box':
        count, other = wait_for_mail(args.box, args.interval)
        if other:
            print(f'agent-link: {args.box} is already watched (pid {other}); nothing to do', flush=True)
        else:
            print(f'agent-link: {count} unread message(s) in {args.box}; read them with /agent-link', flush=True)
        return 0
    if args.command == 'endpoint':
        return asyncio.run(relay(args.socket or default_socket()))
    store = spoke_store()
    if args.command == 'up':
        how = None
        if not service.alive():
            how = service.start('spoke', host_name=configured_host_name() or args.host_name)
        info = asyncio.run(ask(default_socket(), 'status'))
        result = {'host': info['host'], 'path': service.executable('agent-hub'), 'started': how}
        if args.invite:
            result['code'], result['expires'] = store.invite()
        if args.json:
            print(json.dumps(result), flush=True)
        else:
            print(f'agent-link daemon for host "{info["host"]}" is {"running" if not how else "started (" + how + ")"}.')
        return 0
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
    return {'op': 'status', 'host': spoke.host, 'pid': os.getpid(), 'hub_connected': spoke.link is not None,
            'outbox': spoke.store.outbox_stats(), 'agents': spoke.list_agents()}


def hubctl_main():
    sys.exit(hubctl())


def agent_hub_main():
    sys.exit(agent_hub())
