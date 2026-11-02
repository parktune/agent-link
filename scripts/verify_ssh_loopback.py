#!/usr/bin/env python3
"""Real OpenSSH loopback check of add-host and a hub <-> spoke round trip. No models are called.

Starts a temporary sshd on 127.0.0.1 with throwaway keys, a spoke daemon and the hub, all under one
temporary directory. The system SSH configuration, ~/.ssh and existing daemons are not touched.
Needs Linux with OpenSSH server installed and /run/sshd present.
"""
import asyncio
import getpass
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.support import FakeSession, until  # noqa: E402


def wait_for(path, timeout=10):
    deadline = time.time() + timeout
    while not Path(path).exists():
        if time.time() > deadline:
            raise RuntimeError(f'{path} did not appear')
        time.sleep(0.05)


async def round_trip(root):
    main = await FakeSession(root / 'hub-state/agent-link.sock', 'claude', 'main').start()
    head = await FakeSession(root / 'server-state/agent-link.sock', 'claude', 'project-head').start()
    try:
        deadline = time.time() + 20
        while True:
            try:
                sent = await main.send('loopback-srv:claude:project-head', '현황 보고 요청')
                break
            except Exception:
                if time.time() > deadline:
                    raise
                await asyncio.sleep(0.5)
        await until(lambda: head.received)
        reply = await head.send('local:claude:main', '정상 동작 중', 'reply', head.received[0]['id'])
        await until(lambda: main.received)
        return {'request': sent['status'], 'reply': reply['status'], 'reply_content': main.received[0]['content']}
    finally:
        await main.stop()
        await head.stop()


def main():
    sshd = shutil.which('sshd') or '/usr/sbin/sshd'
    if not Path(sshd).exists() or not Path('/run/sshd').exists():
        raise SystemExit('Needs OpenSSH server and /run/sshd. Nothing is installed by this script.')
    processes = []
    with tempfile.TemporaryDirectory(prefix='.agent-link-ssh-', dir=Path.home()) as temp:
        root = Path(temp)
        for key in ('host', 'client'):
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(root / key)], check=True)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        public = (root / 'host.pub').read_text().split()
        (root / 'known_hosts').write_text(f'[127.0.0.1]:{port} {public[0]} {public[1]}\n')
        (root / 'sshd.conf').write_text(
            f'ListenAddress 127.0.0.1\nPort {port}\nHostKey {root / "host"}\nPidFile {root / "pid"}\n'
            f'AuthorizedKeysFile {root / "client.pub"}\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n'
            f'UsePAM no\nStrictModes yes\nPermitRootLogin no\nAllowUsers {getpass.getuser()}\n')
        (root / 'ssh.conf').write_text(
            f'Host loop\n  HostName 127.0.0.1\n  Port {port}\n  User {getpass.getuser()}\n'
            f'  IdentityFile {root / "client"}\n  IdentitiesOnly yes\n  UserKnownHostsFile {root / "known_hosts"}\n')
        server_env = {'AGENT_HUB_STATE': str(root / 'server-state'), 'AGENT_HUB_BOX': str(root / 'server-box')}
        wrapper = root / 'agent-link'
        wrapper.write_text('#!/bin/sh\n' + ''.join(f'export {k}={shlex.quote(v)}\n' for k, v in server_env.items())
                           + f'cd {shlex.quote(str(Path(__file__).resolve().parents[1]))}\n'
                           + f'exec {shlex.quote(sys.executable)} -m agent_link "$@"\n')
        wrapper.chmod(0o700)
        hub_env = {**os.environ, 'AGENT_HUB_STATE': str(root / 'hub-state'), 'AGENT_HUB_BOX': str(root / 'hub-box')}
        hub_cli = [sys.executable, '-m', 'agent_link', 'hub']
        try:
            processes.append(subprocess.Popen([sshd, '-D', '-e', '-f', str(root / 'sshd.conf')],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            processes.append(subprocess.Popen([str(wrapper), 'daemon', '--host-name', 'loopback-srv'],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for(root / 'server-state/agent-link.sock')
            for _ in range(50):
                try:
                    socket.create_connection(('127.0.0.1', port), timeout=0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)
            added = subprocess.run(hub_cli + ['add-host', 'loop', '--remote-path', str(wrapper), '--ssh-config',
                                             str(root / 'ssh.conf')], env=hub_env, capture_output=True, text=True, timeout=60)
            hosts_output = subprocess.run(hub_cli + ['hosts'], env=hub_env, capture_output=True, text=True, timeout=20)
            if added.returncode:
                raise SystemExit('add-host failed: ' + added.stderr[-1500:])
            assert '[4/4] hub started and connected to loopback-srv' in added.stdout, added.stdout
            assert 'restrict,command=' in added.stdout
            hosts = json.loads(hosts_output.stdout)
            result = asyncio.run(round_trip(root))
            server_status = json.loads(subprocess.run([str(wrapper), 'status'], capture_output=True, text=True,
                                                      timeout=20).stdout)
            result['server_sees_hub'] = {k: server_status['hub'].get(k) for k in ('name', 'user', 'from')}
            print(json.dumps({'transport': 'real OpenSSH loopback', 'add_host': 'alias only, hub auto-started',
                              'host_connected': hosts[0]['connected'], **result, 'models_called': False,
                              'system_ssh_modified': False}, ensure_ascii=False))
        finally:
            # add-host started the hub detached; stop it through the pid it reports.
            reply = subprocess.run(hub_cli + ['hosts', '--pid'], env=hub_env, capture_output=True, text=True, timeout=20)
            if reply.returncode == 0 and reply.stdout.strip().isdigit():
                os.kill(int(reply.stdout.strip()), 15)
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == '__main__':
    main()
