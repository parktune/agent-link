import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest

from agent_link.discover import Picker, ssh_hosts

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
    def test_space_toggles_and_registered_hosts_cannot_be_picked(self):
        picker = Picker([{'alias': a, 'hostname': ''} for a in ('a', 'b', 'c')], registered={'b'})
        picker.toggle()
        picker.move(1)
        picker.toggle()  # registered: ignored
        picker.move(1)
        picker.toggle()
        picker.move(1)  # wraps to the top
        picker.toggle()  # unpick a
        self.assertEqual(picker.chosen(), ['c'])
        self.assertEqual([text for _, text in picker.lines()], ['[ ] a', '[=] b  (등록됨)', '[x] c'])


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
        self.assertIn(b"RESULT ['gpu-server-2']", output)
