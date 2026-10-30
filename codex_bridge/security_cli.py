import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys

from .security import Bridge, endpoint_stdio
from .security_store import Store


def configure(subparsers):
    parser = subparsers.add_parser('security', help='SSH mode: private SSH mailbox and MCP tools')
    parser.add_argument('--profile', default='default')
    parser.add_argument('--state-dir', type=Path, help='Trusted local state parent directory')
    commands = parser.add_subparsers(dest='security_command', required=True)
    commands.add_parser('init').add_argument('--name', required=True)
    commands.add_parser('pair-code')
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
    sync.add_argument('--watch', action='store_true')
    sync.add_argument('--interval', type=float, default=5)
    send = commands.add_parser('send')
    send.add_argument('peer')
    send.add_argument('text')
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
    commands.add_parser('mcp-install').add_argument('--name', default='codex_bridge_security')


def run(args):
    store = Store(args.state_dir, args.profile)
    bridge = Bridge(store)
    command = args.security_command
    if command == 'endpoint':
        endpoint_stdio(store)
        return 0
    if command == 'init':
        result = store.initialize(args.name)
    elif command == 'pair-code':
        print('One-use pairing code (expires in 5 minutes): ' + store.pair_code(), flush=True)
        return 0
    elif command == 'connect':
        route = {'host': args.ssh_host, 'python': args.remote_python, 'platform': args.remote_platform,
                 'profile': args.remote_profile, 'config': str(args.ssh_config.resolve()) if args.ssh_config else None}
        result = asyncio.run(bridge.connect(args.alias, route, getpass.getpass('Remote pairing code: ')))
    elif command == 'peers':
        result = store.peers()
    elif command == 'sync':
        if not 1 <= args.interval <= 300:
            raise ValueError('Sync interval must be between 1 and 300 seconds')
        async def synchronize():
            while True:
                value = await bridge.sync(args.peer)
                print(json.dumps(value), flush=True)
                if not args.watch:
                    return 1 if any(v['status'] == 'failed' for v in value) else 0
                await asyncio.sleep(args.interval)
        try:
            return asyncio.run(synchronize())
        except KeyboardInterrupt:
            return 0
    elif command == 'send':
        result = asyncio.run(bridge.send(args.peer, args.text))
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
