import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys

from .security import Bridge, CodexQueue, endpoint_stdio
from .security_store import Store


def configure(subparsers):
    parser = subparsers.add_parser('security', help='SSH mode: private SSH mailbox and MCP tools')
    parser.add_argument('--profile', default='default')
    parser.add_argument('--state-dir', type=Path, help='Trusted local state parent directory')
    commands = parser.add_subparsers(dest='security_command', required=True)
    commands.add_parser('init').add_argument('--name', required=True)
    commands.add_parser('pair-code')
    info = commands.add_parser('connection-info', help='List LAN and public address candidates; queries api.ipify.org over HTTPS')
    info.add_argument('--public-host', help='Approved public inbound SSH hostname/IP, if known')
    connect = commands.add_parser('connect')
    connect.add_argument('alias')
    connect.add_argument('--ssh-host', required=True, help='An already verified OpenSSH config alias')
    connect.add_argument('--remote-python', required=True, help='Absolute remote venv Python executable')
    connect.add_argument('--remote-platform', choices=['posix'], default='posix', help='Ubuntu/POSIX only; Windows support is deferred')
    connect.add_argument('--remote-profile', default='default')
    connect.add_argument('--ssh-config', type=Path)
    commands.add_parser('peers')
    commands.add_parser('endpoint', help='Internal single-request SSH mailbox endpoint')
    sync = commands.add_parser('sync')
    sync.add_argument('--peer')
    sync.add_argument('--watch', action='store_true', help='Same as the watch command')
    sync.add_argument('--interval', type=float, default=5)
    sync.add_argument('--codex', default='codex', help='Codex executable used to wake sessions')
    watch = commands.add_parser('watch', help='Sync peers and wake registered Codex sessions with codex queue')
    watch.add_argument('--interval', type=float, default=5)
    watch.add_argument('--once', action='store_true')
    watch.add_argument('--codex', default='codex', help='Codex executable used to wake sessions')
    session = commands.add_parser('session', help='Local Codex sessions (by /rename name) that accept messages')
    session_commands = session.add_subparsers(dest='session_command', required=True)
    session_commands.add_parser('add').add_argument('session')
    session_commands.add_parser('remove').add_argument('session')
    session_commands.add_parser('list')
    session_commands.add_parser('status', help='Messages waiting to wake a session')
    send = commands.add_parser('send')
    send.add_argument('peer')
    send.add_argument('text')
    send.add_argument('--to-session')
    send.add_argument('--from-session')
    message = commands.add_parser('message', help='Send a conversation message to a named remote session')
    message.add_argument('peer')
    message.add_argument('text')
    message.add_argument('--to-session', required=True)
    message.add_argument('--from-session')
    message.add_argument('--conversation-id')
    receive = commands.add_parser('receive')
    receive.add_argument('--wait', type=float, default=0)
    reply = commands.add_parser('reply')
    reply.add_argument('task_id')
    reply.add_argument('text')
    reply.add_argument('--failed', action='store_true')
    commands.add_parser('task').add_argument('task_id')
    commands.add_parser('ack').add_argument('message_id')
    commands.add_parser('revoke').add_argument('peer')
    commands.add_parser('prune')
    commands.add_parser('mcp')
    commands.add_parser('mcp-install').add_argument('--name', default='agent_link_security')


def run(args):
    store = Store(args.state_dir, args.profile)
    bridge = Bridge(store, queue=CodexQueue(getattr(args, 'codex', 'codex')))
    command = args.security_command
    if command == 'endpoint':
        endpoint_stdio(store)
        return 0
    if command == 'init':
        result = store.initialize(args.name)
    elif command == 'connection-info':
        from .connection_info import connection_info
        result = connection_info(args.public_host)
    elif command == 'pair-code':
        print('One-use pairing code (expires in 5 minutes): ' + store.pair_code(), flush=True)
        return 0
    elif command == 'connect':
        route = {'host': args.ssh_host, 'python': args.remote_python, 'platform': args.remote_platform,
                 'profile': args.remote_profile, 'config': str(args.ssh_config.resolve()) if args.ssh_config else None}
        result = asyncio.run(bridge.connect(args.alias, route, getpass.getpass('Remote pairing code: ')))
    elif command == 'peers':
        result = store.peers()
    elif command in ('sync', 'watch'):
        if not 1 <= args.interval <= 300:
            raise ValueError('Sync interval must be between 1 and 300 seconds')
        if command == 'sync' and not args.watch:
            value = asyncio.run(bridge.sync(args.peer))
            print(json.dumps(value), flush=True)
            return 1 if any(v['status'] == 'failed' for v in value) else 0
        once = command == 'watch' and args.once
        try:
            synced, _ = asyncio.run(bridge.watch(args.interval, once, lambda line: print(line, flush=True)))
        except KeyboardInterrupt:
            return 0
        return 1 if any(v['status'] == 'failed' for v in synced) else 0
    elif command == 'session':
        if args.session_command == 'add':
            result = {'sessions': store.add_session(args.session)}
        elif args.session_command == 'remove':
            result = {'sessions': store.remove_session(args.session)}
        elif args.session_command == 'status':
            result = {'pending': store.wake_status()}
        else:
            result = {'sessions': store.sessions()}
    elif command == 'send':
        result = asyncio.run(bridge.send(args.peer, args.text, args.to_session, args.from_session))
    elif command == 'message':
        result = asyncio.run(bridge.send_message(args.peer, args.to_session, args.text,
                                                 args.from_session, args.conversation_id))
    elif command == 'receive':
        result = asyncio.run(bridge.receive(args.wait))
    elif command == 'reply':
        result = asyncio.run(bridge.reply(args.task_id, args.text, args.failed))
    elif command == 'task':
        outcomes = asyncio.run(bridge.sync())
        result = {'task': store.task(args.task_id), 'trust': 'untrusted_remote_content', 'sync': outcomes}
    elif command == 'ack':
        store.ack(args.message_id)
        result = {'acknowledged_locally': args.message_id}
    elif command == 'revoke':
        store.revoke(args.peer)
        result = {'revoked_locally': args.peer, 'note': 'Also revoke on the other endpoint for bilateral revocation'}
    elif command == 'prune':
        result = store.prune()
    elif command == 'mcp':
        from .security_mcp import serve
        serve(bridge)
        return 0
    elif command == 'mcp-install':
        from .security_store import name
        name(args.name)
        store.identity()
        existing = subprocess.run(['codex', 'mcp', 'get', args.name], capture_output=True, timeout=15)
        if existing.returncode == 0:
            raise RuntimeError('MCP server name already exists; choose --name or remove it explicitly')
        argv = ['codex', 'mcp', 'add', args.name, '--', os.path.abspath(sys.executable), '-m', 'codex_bridge',
                'security', '--profile', args.profile, '--state-dir', str(store.root.parent.resolve()), 'mcp']
        subprocess.run(argv, check=True, timeout=15)
        return 0
    else:
        raise ValueError('Unknown security command')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0
