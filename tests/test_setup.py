"""Alias-only add-host: finding agent-link on the server and starting daemons on demand."""
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
        target = Path(folder, '.venv/bin/agent-link')
        target.parent.mkdir(parents=True)
        target.write_text('#!/bin/sh\necho "$0" "$@"\n')
        target.chmod(0o755)
        return target

    def test_finds_a_clone_anywhere_through_the_setup_link(self):
        with tempfile.TemporaryDirectory() as home:
            target = self.clone(Path(home, 'some/place/agent-link'))
            link = Path(home, '.local/bin/agent-link')
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
            self.assertIn('agent-link-not-found', result.stderr)

    def test_explicit_path_is_quoted(self):
        self.assertEqual(remote_up_command('/opt/a b/agent-link', 'x'),
                         "'/opt/a b/agent-link' up --json --invite --host-name x")


class UpTests(unittest.TestCase):
    def test_up_starts_a_background_daemon_once_and_issues_an_invitation(self):
        with tempfile.TemporaryDirectory(prefix='al-') as state:
            env = {**os.environ, 'AGENT_HUB_STATE': state, 'AGENT_HUB_BOX': state + '/box'}
            command = [sys.executable, '-m', 'agent_link', 'up', '--json', '--invite', '--host-name', 'gpu-server-1']
            first = json.loads(subprocess.run(command, env=env, capture_output=True, text=True, timeout=30, check=True).stdout)
            try:
                self.assertEqual((first['host'], first['started']), ('gpu-server-1', 'background'))
                self.assertRegex(first['code'], r'^[A-Z2-7]{4}(-[A-Z2-7]{4}){3}$')
                second = json.loads(subprocess.run(command, env=env, capture_output=True, text=True, timeout=30,
                                                   check=True).stdout)
                self.assertIsNone(second['started'])
                self.assertEqual(second['host'], 'gpu-server-1')
                status = json.loads(subprocess.run([sys.executable, '-m', 'agent_link', 'status', '--json'], env=env,
                                                   capture_output=True, text=True, timeout=30, check=True).stdout)
                self.assertEqual((status['role'], status['host'], status['hub_connected']), ('spoke', 'gpu-server-1', False))
                refused = subprocess.run([sys.executable, '-m', 'agent_link', 'hub', 'up'], env=env,
                                         capture_output=True, text=True, timeout=30)
                self.assertEqual(refused.returncode, 1)
                self.assertIn('is an agent-link server', refused.stderr)
            finally:
                status = subprocess.run([sys.executable, '-m', 'agent_link', 'status', '--json'], env=env,
                                        capture_output=True, text=True, timeout=30)
                os.kill(json.loads(status.stdout)['pid'], signal.SIGTERM)
                time.sleep(0.2)


class StatusCommandTests(unittest.TestCase):
    def test_agent_link_status_explains_when_nothing_runs(self):
        with tempfile.TemporaryDirectory(prefix='al-') as state:
            env = {**os.environ, 'AGENT_HUB_STATE': state}
            result = subprocess.run([sys.executable, '-m', 'agent_link', 'status'], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 1)
            self.assertIn('not running on this machine', result.stderr)


class RenameMigrationTests(unittest.TestCase):
    def test_hosts_paired_with_agent_hub_now_run_agent_link(self):
        from agent_link.hub import HubStore
        with tempfile.TemporaryDirectory() as root:
            store = HubStore(Path(root, 'hub.sqlite3'))
            store.add_host('gpu-server-1', 'gpu-server-1', '/srv/x/agent-link/.venv/bin/agent-hub', 't' * 43)
            store.add_host('gpu-server-2', 'gpu-server-2', '/opt/custom/agent-link', 't' * 43)
            store.db.close()
            store = HubStore(Path(root, 'hub.sqlite3'))
            self.assertEqual({h['name']: h['remote_path'] for h in store.hosts()},
                             {'gpu-server-1': '/srv/x/agent-link/.venv/bin/agent-link',
                              'gpu-server-2': '/opt/custom/agent-link'})
            store.db.close()


class StatusFormatTests(unittest.TestCase):
    def test_summary_hides_counters_and_shows_why_a_server_is_down(self):
        from agent_link.cli import format_status
        text = format_status({'role': 'hub', 'pid': 7, 'hosts': [
            {'name': 'gpu-server-1', 'connected': True, 'reconnects': 2, 'last_error': None, 'last_error_detail': None},
            {'name': 'gpu-server-2', 'connected': False, 'last_error': 'host_mismatch', 'last_error_detail': None}],
            'agents': [{'id': 'local:claude:main', 'status': 'online', 'delivery': 'push'}]})
        self.assertIn('servers  1/2 connected', text)
        self.assertIn('gpu-server-2  disconnected (host_mismatch)', text)
        self.assertNotIn('reconnects', text)
        self.assertNotIn('last_error', text)
        self.assertIn('sessions 1 (1 online)', text)


class ChannelPolicyTests(unittest.TestCase):
    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def test_team_and_enterprise_logins_need_channels_enabled(self):
        from agent_link.inject import claude_channel_policy
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            account = home / '.claude.json'
            self.write(account, {'oauthAccount': {'organizationType': 'claude_max'}})
            self.assertIsNone(claude_channel_policy(home, managed=()))
            self.write(account, {'oauthAccount': {'organizationType': 'claude_team'}})
            self.assertIn('team organization has not enabled Channels', claude_channel_policy(home, managed=()))
            self.write(home / 'remote-settings.json', {'channelsEnabled': True})
            self.assertIsNone(claude_channel_policy(home, managed=()))
            (home / 'remote-settings.json').unlink()
            self.write(account, {'oauthAccount': {'organizationType': 'claude_enterprise'}})
            managed = home / 'managed-settings.json'
            self.write(managed, {'channelsEnabled': True})
            self.assertIsNone(claude_channel_policy(home, managed=(managed,)))
            self.assertIsNotNone(claude_channel_policy(home, managed=()))


class RoleGuardTests(unittest.TestCase):
    def run_cli(self, state, *argv):
        env = {**os.environ, 'AGENT_HUB_STATE': state, 'AGENT_HUB_BOX': state + '/box'}
        return subprocess.run([sys.executable, '-m', 'agent_link', *argv], env=env, capture_output=True, text=True,
                              timeout=30)

    def test_server_commands_refuse_on_the_hub_pc(self):
        from agent_link.hub import HubStore
        with tempfile.TemporaryDirectory(prefix='al-') as state:
            store = HubStore(Path(state, 'hub', 'hub.sqlite3'))
            store.add_host('gpu-server-1', 'gpu-server-1', '/srv/agent-link', 't' * 43)
            store.db.close()
            for argv in (('up',), ('up', '--restart'), ('daemon',)):
                result = self.run_cli(state, *argv)
                self.assertEqual(result.returncode, 1, argv)
                self.assertIn('is the agent-link hub', result.stderr)

    def test_hub_commands_refuse_on_a_paired_server(self):
        from agent_link.spoke import SpokeStore
        with tempfile.TemporaryDirectory(prefix='al-') as state:
            store = SpokeStore(Path(state, 'spoke', 'spoke.sqlite3'))
            code, _ = store.invite()
            store.enroll(code, 't' * 43)
            store.db.close()
            for argv in (('hub', 'up'), ('hub', 'serve'), ('hub', 'add-host', 'gpu-server-2')):
                result = self.run_cli(state, *argv)
                self.assertEqual(result.returncode, 1, argv)
                self.assertIn('is an agent-link server', result.stderr)


class StaleAgentTests(unittest.TestCase):
    def test_agents_registered_under_another_host_name_are_dropped(self):
        from agent_link.box import Box
        from agent_link.spoke import Agent, Spoke, SpokeStore
        from agent_link.state import Config, Log
        with tempfile.TemporaryDirectory() as root:
            store = SpokeStore(Path(root, 'spoke.sqlite3'))
            store.save_agent(Agent('old-hostname:claude:project-head', 'old-hostname', 'claude', 'project-head'))
            store.save_agent(Agent('gpu-server-1:claude:head', 'gpu-server-1', 'claude', 'head'))
            spoke = Spoke(Config(host_name='gpu-server-1'), store, Log(), Box(Path(root, 'box')))
            self.assertEqual(list(spoke.agents), ['gpu-server-1:claude:head'])
            self.assertEqual([a['id'] for a in store.known_agents()], ['gpu-server-1:claude:head'])
            store.db.close()
