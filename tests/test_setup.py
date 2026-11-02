"""Alias-only add-host: finding agent-hub on the server and starting daemons on demand."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from agent_link.cli import remote_up_command


class DiscoveryTests(unittest.TestCase):
    def run_remote(self, home, command):
        env = {'HOME': str(home), 'PATH': '/usr/bin:/bin'}
        return subprocess.run(['sh', '-c', command], env=env, capture_output=True, text=True, timeout=10)

    def clone(self, folder):
        target = Path(folder, '.venv/bin/agent-hub')
        target.parent.mkdir(parents=True)
        target.write_text('#!/bin/sh\necho "$0" "$@"\n')
        target.chmod(0o755)
        return target

    def test_finds_a_clone_anywhere_through_the_setup_link(self):
        with tempfile.TemporaryDirectory() as home:
            target = self.clone(Path(home, 'some/place/agent-link'))
            link = Path(home, '.local/bin/agent-hub')
            link.parent.mkdir(parents=True)
            link.symlink_to(target)
            result = self.run_remote(home, remote_up_command(None, 'gpu-server-1'))
            # The link is resolved, so the daemon and forced command use the clone's real path.
            self.assertEqual(result.stdout.split(), [str(target), 'up', '--json', '--invite', '--host-name', 'gpu-server-1'])

    def test_no_fixed_clone_locations_are_assumed(self):
        with tempfile.TemporaryDirectory() as home:
            self.clone(Path(home, 'work/agent-link'))  # cloned but setup.sh never ran
            result = self.run_remote(home, remote_up_command(None, None))
            self.assertEqual(result.returncode, 3)

    def test_reports_when_agent_link_is_missing(self):
        with tempfile.TemporaryDirectory() as home:
            result = self.run_remote(home, remote_up_command(None, None))
            self.assertEqual(result.returncode, 3)
            self.assertIn('agent-hub-not-found', result.stderr)

    def test_explicit_path_is_quoted(self):
        self.assertEqual(remote_up_command('/opt/a b/agent-hub', 'x'),
                         "'/opt/a b/agent-hub' up --json --invite --host-name x")


class UpTests(unittest.TestCase):
    def test_up_starts_a_background_daemon_once_and_issues_an_invitation(self):
        with tempfile.TemporaryDirectory(prefix='al-') as state:
            env = {**os.environ, 'AGENT_HUB_STATE': state, 'AGENT_HUB_BOX': state + '/box'}
            command = [sys.executable, '-m', 'agent_link', 'agent-hub', 'up', '--json', '--invite', '--host-name', 'gpu-server-1']
            first = json.loads(subprocess.run(command, env=env, capture_output=True, text=True, timeout=30, check=True).stdout)
            try:
                self.assertEqual((first['host'], first['started']), ('gpu-server-1', 'background'))
                self.assertRegex(first['code'], r'^[A-Z2-7]{4}(-[A-Z2-7]{4}){3}$')
                second = json.loads(subprocess.run(command, env=env, capture_output=True, text=True, timeout=30,
                                                   check=True).stdout)
                self.assertIsNone(second['started'])
                self.assertEqual(second['host'], 'gpu-server-1')
                status = json.loads(subprocess.run([sys.executable, '-m', 'agent_link', 'agent-link', 'status'], env=env,
                                                   capture_output=True, text=True, timeout=30, check=True).stdout)
                self.assertEqual((status['role'], status['host'], status['hub_connected']), ('spoke', 'gpu-server-1', False))
            finally:
                status = subprocess.run([sys.executable, '-m', 'agent_link', 'agent-hub', 'status'], env=env,
                                        capture_output=True, text=True, timeout=30)
                os.kill(json.loads(status.stdout)['pid'], signal.SIGTERM)
                time.sleep(0.2)


class StatusCommandTests(unittest.TestCase):
    def test_agent_link_status_explains_when_nothing_runs(self):
        with tempfile.TemporaryDirectory(prefix='al-') as state:
            env = {**os.environ, 'AGENT_HUB_STATE': state}
            result = subprocess.run([sys.executable, '-m', 'agent_link', 'agent-link', 'status'], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 1)
            self.assertIn('not running on this machine', result.stderr)
