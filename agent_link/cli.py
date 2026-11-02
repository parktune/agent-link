"""The `agent-link` command: `agent-link hub ...` on the local PC, the other subcommands on each server."""
import argparse
import asyncio
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time

from .box import REPO_ROOT, Box, Stopped, default_box_root, wait_for_mail
from .client import SpokeClient
from .endpoint import relay
from .hub import CONNECT_TIMEOUT, Hub, HubStore, SSHTransport, close_process, handshake, hub_identity
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


# ---- local PC (hub) ---------------------------------------------------------------------------------
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
        return 'remote path not found. Check --remote-path (absolute path of agent-link on the server).'
    return 'SSH failed: ' + stderr.strip()[-300:]


# setup.sh links agent-link into ~/.local/bin wherever the clone lives; a non-interactive SSH shell
# may not have that on PATH, so check it explicitly. The first executable match wins.
SEARCH = ('"$(command -v agent-link 2>/dev/null)"', '"$HOME/.local/bin/agent-link"')


def remote_up_command(remote_path, host_name):
    up = ['up', '--json', '--invite'] + (['--host-name', host_name] if host_name else [])
    if remote_path:
        return shlex.join([remote_path] + up)
    args = shlex.join(up)
    return (f'for p in {" ".join(SEARCH)}; do if [ -n "$p" ] && [ -x "$p" ]; then '
            f'exec "$(readlink -f "$p")" {args}; fi; done; echo agent-link-not-found >&2; exit 3')


def remote_up(alias, remote_path, host_name, ssh_config=None):
    """One SSH call: find agent-link, start its daemon if needed, issue a one-use invitation.

    The code arrives on this pipe and stays in memory: never printed, logged or put in argv/env.
    """
    argv = SSHTransport(alias, '/', ssh_config=ssh_config).argv()
    argv[-1] = remote_up_command(remote_path, host_name)
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if 'agent-link-not-found' in result.stderr:
        raise SystemExit(f'agent-link is not installed on {alias} (no agent-link on PATH or in ~/.local/bin). '
                         'On the server, in any folder, run:\n'
                         '  git clone https://github.com/parktune/agent-link.git && cd agent-link && ./setup.sh\n'
                         'or, if it is already cloned, run ./setup.sh in that clone (it links agent-link), '
                         'or pass --remote-path.')
    if result.returncode:
        raise SystemExit(f'{alias}: ' + explain_ssh_failure(result.stderr))
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise SystemExit(f'{alias}: unexpected output from agent-link up') from None


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
        raise SystemExit(f'{args.alias} is already registered (agent-link hub hosts); remove-host first to redo it')
    host_name = args.name or (args.alias if NAME.fullmatch(args.alias) else None)
    steps, options = preflight(args.alias, args.ssh_config)
    say(f'[1/4] {args.alias}: DNS {steps["dns"]}, TCP {steps["tcp"]}')
    remote = remote_up(args.admin_alias or args.alias, args.remote_path, host_name, args.ssh_config)
    remote_path = args.remote_path or remote['path']
    started = {None: 'already running', 'systemd': 'started (systemd user service)',
               'background': 'started (background process; add a systemd user service to survive reboots)'}
    say(f'[2/4] server daemon {started.get(remote["started"], remote["started"])} at {remote_path}')
    token = secrets.token_urlsafe(32)
    transport = SSHTransport(args.alias, remote_path, args.remote_socket, args.ssh_config)
    enrolled = await one_shot(transport, {'op': 'enroll', 'code': remote['code'], 'token': token, 'hub': hub_identity()})
    name = check_name(enrolled.get('host', ''), 'host')
    if store.host(name):
        raise SystemExit(f'host name {name} is already registered; remove-host first')
    store.add_host(name, args.alias, remote_path, token, args.remote_socket, ssh_config=args.ssh_config)
    hello = await one_shot(transport, {'op': 'hello', 'v': PROTOCOL_VERSION, 'token': token, 'hub': hub_identity(),
                                       'host': name})
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
                              ssh_config=ssh_config)


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


def run_hub(args):
    if args.command in ('up', 'serve', 'add-host', 'discover'):
        require_role('hub', getattr(args, 'force', False))
    root = private_directory(state_root() / 'hub')
    if args.command == 'up':
        if args.restart and service.alive():
            stop_running()
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
        emit({'removed': args.host, 'note': f'Also run `agent-link revoke` on {args.host}; SSH keys are managed separately.'})
    elif args.command == 'hosts':
        reply = asyncio.run(ask(default_socket(), 'hosts'))
        print(reply['pid']) if args.pid else emit(reply['hosts'])
    elif args.command == 'agents':
        emit(asyncio.run(ask(default_socket(), 'list', filter=args.filter))['agents'])
    elif args.command == 'log':
        emit(asyncio.run(ask(default_socket(), 'recent', limit=args.limit))['messages'])
    return 0


# ---- server (spoke daemon) ------------------------------------------------------------------------------
def configured_host_name():
    path = state_root() / 'config.toml'
    if not path.exists():
        return None
    import tomllib
    with path.open('rb') as handle:
        return tomllib.load(handle).get('host_name') or None


def daemon_host_name(store, flag=None):
    """Host name for this server's daemon: config.toml `host_name`, else the name it was paired under, else
    --host-name (the hub's suggestion at first pairing), else None (the short hostname)."""
    return configured_host_name() or store.paired_host() or flag or None


def stop_running(wait=15):
    """Stop what runs on this machine's socket (hub or daemon, systemd service or background process)."""
    try:
        pid = asyncio.run(ask(default_socket(), 'status'))['pid']
    except ProtocolError:  # hubs before 0.5.6 do not answer status, but report their pid with hosts
        pid = asyncio.run(ask(default_socket(), 'hosts'))['pid']
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait
    while service.alive():
        if time.monotonic() > deadline:
            raise SystemExit(f'agent-link (pid {pid}) did not stop')
        time.sleep(0.1)


def running_role():
    """'hub' or 'spoke' for what answers on this machine's socket, else None."""
    if not service.alive():
        return None
    try:
        info = asyncio.run(ask(default_socket(), 'status'))
    except ProtocolError:
        return 'hub'  # hubs before 0.5.6 do not answer status
    return info.get('role') or ('spoke' if 'hub_connected' in info else 'hub')


def _count(path, query):
    if not path.exists():
        return 0
    import sqlite3
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return db.execute(query).fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        db.close()


def machine_role():
    """What this machine is set up as: running, else from its state (paired servers, or a hub token)."""
    role = running_role()
    if role:
        return role
    if _count(state_root() / 'hub' / 'hub.sqlite3', 'SELECT COUNT(*) FROM hosts'):
        return 'hub'
    if _count(state_root() / 'spoke' / 'spoke.sqlite3', 'SELECT COUNT(*) FROM tokens'):
        return 'spoke'
    return None


def require_role(wanted, force=False):
    """Keep `agent-link up` off the hub's PC and `agent-link hub ...` off a server: one socket, one role."""
    role = None if force else machine_role()
    if role == 'hub' and wanted == 'spoke':
        raise SystemExit('This machine is the agent-link hub (the local PC). `agent-link up` and `daemon` are for '
                         'servers; use `agent-link hub up` here. Pass --force to override.')
    if role == 'spoke' and wanted == 'hub':
        raise SystemExit('This machine is an agent-link server (its daemon is paired with a hub). The hub runs on the '
                         'local PC; use `agent-link up` here. Pass --force to override.')


def spoke_store():
    return SpokeStore(private_directory(state_root() / 'spoke') / 'spoke.sqlite3')


def run_spoke(args):
    if args.command == 'wait-box':
        try:
            count, other = wait_for_mail(args.box, args.interval)
        except Stopped:
            print(f'agent-link: watch on {args.box} stopped', flush=True)
            return 0
        if other:
            print(f'agent-link: {args.box} is already watched (pid {other}); nothing to do', flush=True)
        else:
            print(f'agent-link: {count} unread message(s) in {args.box}; read them with /agent-link', flush=True)
        return 0
    if args.command == 'endpoint':
        return asyncio.run(relay(args.socket or default_socket()))
    if args.command in ('up', 'daemon'):
        require_role('spoke', args.force)
    store = spoke_store()
    if args.command == 'up':
        how = None
        if args.restart and service.alive():
            stop_running()
        if not service.alive():
            how = service.start('spoke', host_name=daemon_host_name(store, args.host_name))
        info = asyncio.run(ask(default_socket(), 'status'))
        result = {'host': info['host'], 'path': service.executable('agent-link'), 'started': how}
        if args.invite:
            result['code'], result['expires'] = store.invite()
        if args.json:
            print(json.dumps(result), flush=True)
        else:
            print(f'agent-link daemon for host "{info["host"]}" is {"running" if not how else "started (" + how + ")"}.')
        return 0
    if args.command == 'daemon':
        config = Config.load(host_name=daemon_host_name(store, args.host_name))
        spoke = Spoke(config, store, stderr_log(state_root() / 'spoke' / 'spoke.log'), Box(default_box_root('spoke')))
        spoke.extra_ops = {'drop_link': lambda frame: drop_link(spoke), 'status': lambda frame: status(spoke)}
        try:
            asyncio.run(run_forever(lambda: spoke.start(args.socket or default_socket()), spoke.close))
        except KeyboardInterrupt:
            pass
        return 0
    if args.command == 'revoke':
        removed = store.revoke()
        try:
            asyncio.run(ask(default_socket(), 'drop_link'))
        except SystemExit:
            pass
        emit({'revoked_tokens': removed, 'note': 'Also run `agent-link hub remove-host` on the hub.'})
    return 0


async def drop_link(spoke):
    if spoke.link:
        spoke.link.close()
    return {'op': 'ok'}


async def status(spoke):
    hub = spoke.link.info if spoke.link else None
    return {'op': 'status', 'role': 'spoke', 'host': spoke.host, 'pid': os.getpid(), 'hub_connected': spoke.link is not None,
            'hub': readable(hub) if hub else None, 'enrolled_hubs': [readable(h) for h in spoke.store.enrolled_hubs()],
            'outbox': spoke.store.outbox_stats(), 'agents': [a.public() for a in spoke.agents.values()],
            'others': spoke.directory}


def readable(info):
    """Timestamps as local time for people reading `agent-link status`."""
    return {k: time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(v)) if k in ('enrolled_at', 'connected_since') else v
            for k, v in info.items()}


def build_parser():
    parser = argparse.ArgumentParser(prog='agent-link', description='agent-link: the hub on the local PC (agent-link hub ...) '
                                     'and the daemon on each server')
    opening = parser.add_mutually_exclusive_group()
    opening.add_argument('--resume', nargs='?', const='', metavar='SESSION', help='Same as `agent-link claude --resume`')
    opening.add_argument('--continue', dest='continue_', action='store_true', help='Same as `agent-link claude --continue`')
    commands = parser.add_subparsers(dest='command')
    for tool, latest in (('claude', 'continue'), ('codex', 'last')):
        session = commands.add_parser(tool, help=f'Open {tool} registered with agent-link: resume [SESSION] | {latest}. '
                                                 f'Arguments after -- go to {tool}.')
        session.add_argument('words', nargs='*', metavar='resume [SESSION] | ' + latest,
                             help='SESSION is the /rename name or the session ID; resume without one opens the picker')
        session.add_argument('--resume', nargs='?', const='', metavar='SESSION')
        session.add_argument('--continue', '--last', dest='continue_', action='store_true',
                             help="This folder's most recent conversation")
    status = commands.add_parser('status', help='Connection status: the hub and its servers on the PC, the daemon on a server')
    status.add_argument('--socket', type=Path)
    status.add_argument('--json', action='store_true', help='Everything the hub or daemon reports, as JSON')

    # Server side: the spoke daemon.
    up = commands.add_parser('up', help='Server: start the daemon in the background if it is not running')
    up.add_argument('--host-name', help='Host name in agent IDs at first pairing (config host_name and the '
                    'paired name win)')
    up.add_argument('--restart', action='store_true', help='Restart a running daemon (e.g. to take the paired name)')
    up.add_argument('--invite', action='store_true', help='Also issue a one-use invitation (for agent-link hub add-host)')
    up.add_argument('--json', action='store_true')
    up.add_argument('--force', action='store_true', help='Start even though this machine is set up as the hub')
    daemon = commands.add_parser('daemon', help='Server: run the daemon in the foreground (what `up` starts)')
    daemon.add_argument('--socket', type=Path)
    daemon.add_argument('--host-name', help='Host name in agent IDs when config.toml has none and the server is '
                        'not paired yet (default: short hostname)')
    daemon.add_argument('--force', action='store_true', help='Run even though this machine is set up as the hub')
    commands.add_parser('endpoint', help='Server: SSH forced command the hub runs').add_argument('--socket', type=Path)
    commands.add_parser('revoke', help='Server: delete the hub peer token and drop the hub connection')
    update = commands.add_parser('update', help='git pull + setup.sh + restart here; on the hub PC also every server')
    update.add_argument('--hosts', help='On the hub PC: only these servers (comma-separated names or aliases)')
    update.add_argument('--local-only', action='store_true', help='Only this machine, not the servers')
    update.add_argument('--dry-run', action='store_true', help='Print the plan only')
    wait = commands.add_parser('wait-box', help='Wait until a mailbox folder has unread mail, print one line, exit')
    wait.add_argument('--box', type=Path, required=True, help='The received/ folder shown by whoami')
    wait.add_argument('--interval', type=float, default=2.0, help='Seconds between checks')

    # Local PC: the hub.
    hub = commands.add_parser('hub', help='Local PC: the hub and the servers it connects to').add_subparsers(
        dest='hub_command', required=True)
    hub_up = hub.add_parser('up', help='Start the hub in the background (systemd user service if possible)')
    hub_up.add_argument('--restart', action='store_true', help='Restart a running hub (e.g. after an update)')
    hub_up.add_argument('--force', action='store_true', help='Start even though this machine is set up as a server')
    serve = hub.add_parser('serve', help='Run the hub in the foreground (what `hub up` starts)')
    serve.add_argument('--socket', type=Path)
    serve.add_argument('--force', action='store_true', help='Run even though this machine is set up as a server')
    add = hub.add_parser('add-host', help='Connect a server by its SSH alias (from ~/.ssh/config)')
    add.add_argument('alias')
    add.add_argument('--name', help='Host name in agent IDs (default: the alias)')
    add.add_argument('--remote-path', help='agent-link on the server, if not found automatically')
    add.add_argument('--remote-socket', help='Daemon socket on the server, if not the default')
    add.add_argument('--admin-alias', help='SSH alias with shell access used only to issue the invitation')
    add.add_argument('--ssh-config')
    discover = hub.add_parser('discover', help='Pick servers from ~/.ssh/config (Space to pick, Enter to apply)')
    discover.add_argument('--ssh-config', help='SSH config to read (default ~/.ssh/config)')
    discover.add_argument('--hosts', help='Comma-separated aliases instead of the interactive picker')
    hub.add_parser('remove-host').add_argument('host')
    hub.add_parser('hosts').add_argument('--pid', action='store_true', help='Print only the hub process ID')
    agents = hub.add_parser('agents')
    agents.add_argument('filter', nargs='?', default='')
    log = hub.add_parser('log', help='Recent routed message metadata')
    log.add_argument('--limit', type=int, default=20)
    return parser


def format_status(info):
    """A few lines for people: what runs here, what it is connected to, and the sessions."""
    lines = []
    if info.get('role') == 'hub':
        hosts = info.get('hosts') or []
        lines.append(f'hub      running (pid {info["pid"]})')
        lines.append(f'servers  {sum(1 for h in hosts if h.get("connected"))}/{len(hosts)} connected')
        width = max((len(h['name']) for h in hosts), default=0)
        for host in hosts:
            if host.get('connected'):
                lines.append(f'  {host["name"]:<{width}}  connected')
            else:
                why = host.get('last_error_detail') or host.get('last_error') or 'connecting'
                lines.append(f'  {host["name"]:<{width}}  disconnected ({why})')
    else:
        lines.append(f'daemon   {info["host"]} (pid {info["pid"]})')
        hub = info.get('hub')
        if info.get('hub_connected') and hub:
            who = ' '.join(filter(None, [hub.get('name'), f'({hub["user"]})' if hub.get('user') else None]))
            where = f' from {hub["from"]}' if hub.get('from') else ''
            since = f' since {hub["connected_since"]}' if hub.get('connected_since') else ''
            lines.append(f'hub      connected{": " + who if who else ""}{where}{since}')
        else:
            lines.append('hub      not connected' + (' (replies wait in the outbox)' if (info.get('outbox') or {}).get('items') else ''))
        waiting = (info.get('outbox') or {}).get('items', 0)
        if waiting:
            lines.append(f'outbox   {waiting} message(s) waiting for the hub')
    groups = [('sessions', info.get('agents') or [])]
    if info.get('role') != 'hub':
        groups.append(('others', info.get('others') or []))  # other hosts, as the hub reported them
    width = max((len(a['id']) for _, agents in groups for a in agents), default=0)
    for label, agents in groups:
        online = sum(1 for a in agents if a.get('status') == 'online')
        where = ' on other hosts' if label == 'others' else ''
        lines.append(f'{label:<8} {len(agents)}{where} ({online} online)')
        for agent in sorted(agents, key=lambda a: (a.get('status') != 'online', a['id'])):
            lines.append(f'  {agent["id"]:<{width}}  {agent.get("status", "?"):<7}  {agent.get("delivery", "")}')
    return '\n'.join(lines)


def show_status(socket_path, as_json=False):
    if not service.alive(socket_path):
        raise SystemExit('agent-link is not running on this machine. '
                         'Local PC: `agent-link hub up` (or `agent-link hub add-host <alias>`); server: `agent-link up`.')
    try:
        info = asyncio.run(ask(socket_path, 'status'))
    except ProtocolError as error:
        raise SystemExit(f'The running hub/daemon does not answer status ({error.code}); it is an older version. '
                         'Restart it: `agent-link hub up --restart` (PC) or `agent-link up --restart` (server).') from None
    info.pop('rid', None)
    info.pop('op', None)
    if as_json:
        emit(info)
    else:
        print(format_status(info), flush=True)
    return 0


# ---- update ---------------------------------------------------------------------------------
def tail(text, lines=6):
    return '\n'.join((text or '').strip().splitlines()[-lines:])


def installed_version(output):
    for line in (output or '').splitlines():
        if 'installed agent-link ' in line:
            return line.rsplit(' ', 1)[-1]
    return None


def update_local(dry_run=False):
    """git pull + setup.sh in this clone. Returns the installed version."""
    repo = REPO_ROOT
    steps = [['git', '-C', str(repo), 'pull', '--ff-only'], [str(repo / 'setup.sh')]]
    if dry_run:
        for step in steps:
            say('  would run: ' + shlex.join(step))
        return None
    version = None
    for step in steps:
        result = subprocess.run(step, capture_output=True, text=True, timeout=900, cwd=repo)
        if result.returncode:
            raise SystemExit(f'{shlex.join(step)} failed:\n{tail(result.stderr or result.stdout)}')
        version = installed_version(result.stdout) or version
    return version


def remote_update_command(remote_path):
    """Shell for a server: its clone is two levels above the agent-link it was paired with (.venv/bin/)."""
    repo = str(PurePosixPath(remote_path).parents[2])
    return (f'cd {shlex.quote(repo)} && git pull --ff-only && ./setup.sh && .venv/bin/agent-link up --restart >/dev/null '
            f'&& .venv/bin/python -c "import agent_link; print(\'version\', agent_link.__version__)"')


def update_server(record, dry_run=False):
    command = remote_update_command(record['remote_path'])
    if dry_run:
        say(f'  would run on {record["alias"]}: {command}')
        return None
    argv = SSHTransport(record['alias'], '/', ssh_config=record.get('ssh_config')).argv()
    argv[-1] = command
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return 'failed: timed out'
    if result.returncode:
        return 'failed: ' + (explain_ssh_failure(result.stderr) if result.returncode == 255 else
                             tail(result.stderr or result.stdout, 3).replace('\n', ' | '))
    versions = [line.split()[1] for line in result.stdout.splitlines() if line.startswith('version ')]
    return f'updated to {versions[-1]}' if versions else 'updated'


def run_update(args):
    """Bring this machine (and, from the hub's PC, every paired server) to the latest commit."""
    role = machine_role()
    say(f'agent-link update ({role or "not set up yet"}){" -- dry run" if args.dry_run else ""}')
    if role == 'hub' and not args.local_only:
        store = HubStore(private_directory(state_root() / 'hub') / 'hub.sqlite3')
        records = store.hosts()
        if args.hosts:
            wanted = {h.strip() for h in args.hosts.split(',') if h.strip()}
            unknown = wanted - {r['name'] for r in records} - {r['alias'] for r in records}
            if unknown:
                raise SystemExit(f'not paired: {", ".join(sorted(unknown))} (agent-link hub hosts)')
            records = [r for r in records if r['name'] in wanted or r['alias'] in wanted]
        # Servers first: they then serve `agent-link endpoint` before the new hub looks for it.
        results = {}
        for record in records:
            say(f'[server] {record["name"]} ...')
            results[record['name']] = update_server(record, args.dry_run)
            if results[record['name']]:
                say(f'  {results[record["name"]]}')
    say('[this machine] git pull + setup.sh ...')
    version = update_local(args.dry_run)
    if version:
        say(f'  installed {version}')
    if role in ('hub', 'spoke'):
        say(f'[this machine] restart the {"hub" if role == "hub" else "daemon"} ...')
        if not args.dry_run:
            if service.alive():
                stop_running()
            store = spoke_store() if role == 'spoke' else None
            how = service.start(role, host_name=daemon_host_name(store) if store else None)
            say(f'  started ({how})')
    if not args.dry_run and role and service.alive():
        say('')
        show_status(default_socket())
    failed = role == 'hub' and not args.local_only and any(str(v).startswith('failed') for v in results.values())
    return 1 if failed else 0


CHANNEL = ['--dangerously-load-development-channels', 'server:agent-link']


# First prompt for a session that cannot get pushes: only the session itself can start the background
# watch that wakes it when mail arrives.
ARM_WATCH = ('agent-link: this session receives agent-link messages through its mailbox, because the organization '
             'keeps Claude Channels off. Call the agent-link whoami tool. If it is not registered and not withdrawn, '
             'call register() with no arguments. Then, if watch.enabled is true and watch.running_pid is null, run '
             'watch.command exactly as given with Bash run_in_background: true. Answer in one line that the mailbox '
             'watch is on, then wait for the user.')


def say_err(text):
    print(f'agent-link: {text}', file=sys.stderr, flush=True)


def pick_session(found, what, target):
    if not found:
        raise SystemExit(f'No {what} session named "{target}" (the name given with /rename, or a session ID). '
                         f'Run `agent-link {what.lower()} resume` to pick one.')
    for other in found[1:]:
        say_err(f'also named "{target}": {other["id"]} in {other["cwd"] or "?"}; opening the most recent one')
    session = found[0]
    if session['cwd'] and os.path.isdir(session['cwd']):
        os.chdir(session['cwd'])  # resume finds (and runs) a session in the folder it belongs to
    return session


def launch(argv, shown=None):
    os.environ['AGENT_LINK_REGISTER'] = '1'  # the session's agent-link MCP server registers it on start
    say_err(f'{shlex.join(shown or argv)}  (in {os.getcwd()})')
    os.execvp(argv[0], argv)


def open_claude(action=None, target=None, extra=()):
    """Run claude registered with agent-link and able to receive messages.

    Where the login may use Channels, add the agent-link channel flag (push). Where the organization keeps
    Channels off, the flag would only be ignored, so open without it and pass a first prompt that arms the
    mailbox watch. `action` is 'resume' (by name or ID, or the picker without one), 'continue' (this folder's
    latest conversation) or None (a new session).
    """
    from .inject import claude_channel_policy
    from .sessions import find_claude_sessions
    if not shutil.which('claude'):
        raise SystemExit('claude (Claude Code) is not on PATH')
    picker = action == 'resume' and not target
    if action == 'continue':
        mode = ['--continue']
    elif action == 'resume' and target:
        mode = ['--resume', pick_session(find_claude_sessions(target), 'Claude', target)['id']]
    else:
        mode = ['--resume'] if picker else []
    blocked = claude_channel_policy()
    if not blocked:
        say_err('messages arrive live in this session (push).')
        return launch(['claude', *CHANNEL, *mode, *extra])
    arm = action is not None and not picker  # a new or picked session has no known name to register yet
    say_err(f'{blocked}.')
    say_err('messages go to the mailbox; ' + ('the session starts the mailbox watch as its first step and wakes '
            'when mail arrives.' if arm else 'after the session opens (and has a /rename name), run /agent-link '
            'once to start the watch that wakes it.'))
    argv = ['claude', *mode, *extra] + ([ARM_WATCH] if arm else [])
    return launch(argv, [part if part != ARM_WATCH else '<arm the mailbox watch>' for part in argv])


def open_codex(action=None, target=None, extra=()):
    """Run codex registered with agent-link. The thread is known up front, so messages are queued into it.

    Codex passes only some environment to MCP servers, so the registration hints go in as config overrides.
    """
    from .sessions import find_codex_sessions, latest_codex_session
    if not shutil.which('codex'):
        raise SystemExit('codex is not on PATH')
    session = None
    if action == 'resume' and target:
        session = pick_session(find_codex_sessions(target), 'Codex', target)
    elif action == 'continue':
        session = latest_codex_session(os.getcwd())
        if not session:
            raise SystemExit(f'No Codex session ran in {os.getcwd()} yet. Use `agent-link codex resume <name>`.')
    env = [('AGENT_LINK_REGISTER', '1')] + ([('AGENT_LINK_THREAD', session['id'])] if session else [])
    overrides = [part for key, value in env for part in ('-c', f'mcp_servers.agent-link.env.{key}={json.dumps(value)}')]
    if session:
        mode = ['resume', session['id']]
        say_err('messages are queued into this session (codex queue).')
    else:
        mode = ['resume'] if action == 'resume' else []
        say_err('the thread is not known yet, so messages go to the mailbox until you run $agent-link once in the '
                'session (after /rename).')
    return launch(['codex', *overrides, *mode, *extra])


SESSION_ACTIONS = {'resume': 'resume', 'continue': 'continue', 'last': 'continue'}


def session_action(parser, args):
    """`resume [SESSION]`, `continue`/`last`, or the same as --resume/--continue/--last: (action, target)."""
    action = 'resume' if args.resume is not None else 'continue' if args.continue_ else None
    target = args.resume or None
    words = list(getattr(args, 'words', None) or [])
    if words:
        if words[0] not in SESSION_ACTIONS or len(words) > 2 or (len(words) == 2 and words[0] != 'resume'):
            parser.error('use: resume [SESSION] | continue (codex: last)')
        action, target = SESSION_ACTIONS[words[0]], words[1] if len(words) == 2 else target
    return action, target


def agent_link(argv=None):
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    passthrough = []
    if '--' in argv:  # everything after -- belongs to claude
        argv, passthrough = argv[:argv.index('--')], argv[argv.index('--') + 1:]
    args = parser.parse_args(argv)
    if args.command in ('claude', 'codex'):
        action, target = session_action(parser, args)
        return (open_claude if args.command == 'claude' else open_codex)(action, target, passthrough)
    if args.resume is not None or args.continue_:  # shorthand for `agent-link claude ...`
        if args.command:
            parser.error('--resume/--continue cannot be combined with a subcommand')
        return open_claude(*session_action(parser, args), passthrough)
    if passthrough:
        parser.error('arguments after -- are only for agent-link claude|codex')
    if not args.command:
        parser.error('choose a command (e.g. status, up, hub ...) or --resume SESSION')
    if args.command == 'status':
        return show_status(args.socket or default_socket(), args.json)
    if args.command == 'update':
        return run_update(args)
    if args.command == 'hub':
        args.command = args.hub_command
        return run_hub(args)
    return run_spoke(args)


def main():
    sys.exit(agent_link())
