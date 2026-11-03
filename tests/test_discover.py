import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import argparse
import unittest
from unittest import mock

from agent_link import cli
from agent_link.discover import Picker, ssh_hosts
from agent_link.hub import HubStore

CONFIG = '''# comment
Host *
    ServerAliveInterval 30
Host gpu-server-1 gpu-server-1-alt
    HostName 10.0.0.230
    User me
Host gpu-server-2
  HostName=gpu-server-2.internal
Host bastion-*
    User jump
Match host foo
    HostName ignored
Include extra/*.conf
'''


class SshConfigTests(unittest.TestCase):
    def test_lists_concrete_hosts_with_includes_and_hostnames(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root, 'config')
            config.write_text(CONFIG.replace('extra/', root + '/extra/'))
            Path(root, 'extra').mkdir()
            Path(root, 'extra/a.conf').write_text('Host storage-1\n  HostName storage-1.lan\nHost gpu-server-2\n  HostName dup\n')
            hosts = ssh_hosts(config)
            self.assertEqual([(h['alias'], h['hostname']) for h in hosts],
                             [('gpu-server-1', '10.0.0.230'), ('gpu-server-1-alt', '10.0.0.230'),
                              ('gpu-server-2', 'gpu-server-2.internal'), ('storage-1', 'storage-1.lan')])

    def test_missing_config_is_empty(self):
        self.assertEqual(ssh_hosts('/nonexistent/config'), [])


class PickerTests(unittest.TestCase):
    def test_space_picks_new_hosts_and_marks_registered_ones_for_removal(self):
        picker = Picker([{'alias': a, 'hostname': ''} for a in ('a', 'b', 'c')], registered={'b'})
        picker.toggle()
        picker.move(1)
        picker.toggle()  # registered: marked for removal
        picker.move(1)
        picker.toggle()
        picker.move(1)  # wraps to the top
        picker.toggle()  # unpick a
        self.assertEqual((picker.chosen(), picker.removing()), (['c'], ['b']))
        self.assertEqual([text for _, text in picker.lines()], ['[ ] a', '[-] b  (해제 예정)', '[x] c'])

    def test_space_on_a_registered_host_marks_it_for_removal(self):
        picker = Picker([{'alias': a, 'hostname': ''} for a in ('a', 'b')], registered={'b'})
        picker.move(1)
        picker.toggle()
        self.assertEqual((picker.chosen(), picker.removing()), ([], ['b']))
        self.assertEqual(list(picker.lines())[1], (True, '[-] b  (해제 예정)'))
        self.assertEqual(picker.title(), '연결할 서버를 고르세요 (추가 0개, 해제 1개)')
        picker.toggle()  # undo
        self.assertEqual(picker.removing(), [])
        self.assertEqual(list(picker.lines())[1], (True, '[=] b  (등록됨)'))


class DiscoverRemovalTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        config = Path(self.root.name, 'config')
        config.write_text('Host gpu-server-1\nHost gpu-server-2\n')
        self.args = argparse.Namespace(ssh_config=str(config), hosts=None)
        self.store = HubStore(Path(self.root.name, 'hub.sqlite3'))
        self.store.add_host('gpu-server-1', 'gpu-server-1', '/opt/agent-link', 'token')
        patches = [mock.patch('sys.stdin.isatty', return_value=True), mock.patch('sys.stdout.isatty', return_value=True),
                   mock.patch.object(cli, 'ask', new=mock.AsyncMock(return_value={})),
                   mock.patch('agent_link.discover.pick', return_value=([], ['gpu-server-1'])),
                   mock.patch.object(cli, 'say')]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_discover(self, confirmed, revoke_failure=None):
        with mock.patch.object(cli, 'confirm', return_value=confirmed), \
                mock.patch.object(cli, 'remote_revoke', return_value=revoke_failure) as revoke:
            code = cli.discover_hosts(self.args, self.store)
        return code, revoke

    def test_confirmed_removal_forgets_the_host_and_revokes_on_the_server(self):
        code, revoke = self.run_discover(True)
        self.assertEqual(code, 0)
        self.assertIsNone(self.store.host('gpu-server-1'))
        self.assertEqual(revoke.call_args.args[0]['remote_path'], '/opt/agent-link')

    def test_declined_removal_changes_nothing(self):
        code, revoke = self.run_discover(False)
        self.assertEqual(code, 0)
        self.assertIsNotNone(self.store.host('gpu-server-1'))
        revoke.assert_not_called()

    def test_unreachable_server_is_still_removed_from_the_hub(self):
        code, _ = self.run_discover(True, revoke_failure='SSH failed')
        self.assertEqual(code, 1)
        self.assertIsNone(self.store.host('gpu-server-1'))

    def test_remote_revoke_runs_the_stored_path_over_ssh(self):
        record = {'alias': 'gpu-server-1', 'remote_path': '/opt/agent-link', 'socket': None, 'ssh_config': None}
        done = subprocess.CompletedProcess([], 0, stdout='{"revoked_tokens": 1}', stderr='')
        with mock.patch.object(cli.subprocess, 'run', return_value=done) as run:
            self.assertIsNone(cli.remote_revoke(record))
            argv = run.call_args.args[0]
            self.assertEqual(argv[-2:], ['gpu-server-1', '/opt/agent-link revoke'])
            self.assertIn('BatchMode=yes', argv)
            run.return_value = subprocess.CompletedProcess([], 0, stdout='', stderr='')
            self.assertIn('unexpected output', cli.remote_revoke(record))


class TerminalTests(unittest.TestCase):
    def test_space_and_enter_in_a_real_terminal(self):
        script = ('from agent_link.discover import pick\n'
                  "hosts = [{'alias': a, 'hostname': ''} for a in ('gpu-server-1', 'gpu-server-2', 'storage-1')]\n"
                  "print('RESULT', pick(hosts, {'storage-1'}), flush=True)\n")
        master, slave = pty.openpty()
        env = {**os.environ, 'TERM': 'xterm', 'LC_ALL': 'C.UTF-8', 'LINES': '24', 'COLUMNS': '80'}
        process = subprocess.Popen([sys.executable, '-c', script], stdin=slave, stdout=slave, stderr=slave, env=env,
                                   cwd=Path(__file__).resolve().parents[1])
        os.close(slave)
        output = b''

        def read(seconds):
            nonlocal output
            deadline = time.time() + seconds
            while time.time() < deadline:
                ready, _, _ = select.select([master], [], [], 0.05)
                if ready:
                    try:
                        output += os.read(master, 65536)
                    except OSError:
                        return

        read(1.0)
        for key in (b'\x1bOB', b' ', b'\x1bOA', b'\r'):  # down, pick gpu-server-2, up, enter (keypad mode)
            os.write(master, key)
            read(0.3)
        process.wait(timeout=10)
        read(0.3)
        os.close(master)
        self.assertIn(b"RESULT (['gpu-server-2'], [])", output)
