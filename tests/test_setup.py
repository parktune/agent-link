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


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for tool in ('claude', 'codex'):
            fake = self.bin / tool
            fake.write_text(f'#!{sys.executable}\nimport json, os, sys\n'
                            f'json.dump({{"tool": "{tool}", "argv": sys.argv[1:], "cwd": os.getcwd(), '
                            f'"register": os.environ.get("AGENT_LINK_REGISTER")}}, open({str(self.root / "ran.json")!r}, "w"))\n')
            fake.chmod(0o755)
        self.work = self.root / 'project'
        self.work.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def transcript(self, session_id, title, cwd):
        path = self.root / 'claude/projects/-p' / f'{session_id}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [{'type': 'user', 'cwd': str(cwd), 'message': 'hi'}]
        if title:
            lines.append({'type': 'custom-title', 'customTitle': title, 'sessionId': session_id})
        path.write_text(''.join(json.dumps(line) + '\n' for line in lines))
        return path

    def run_cli(self, *argv):
        env = {**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}', 'CLAUDE_CONFIG_DIR': str(self.root / 'claude'),
               'CODEX_HOME': str(self.root / 'codex')}
        (self.root / 'ran.json').unlink(missing_ok=True)
        result = subprocess.run([sys.executable, '-m', 'agent_link', *argv], env=env, capture_output=True, text=True,
                                timeout=30, cwd=self.root)
        ran = json.loads((self.root / 'ran.json').read_text()) if (self.root / 'ran.json').exists() else None
        return result, ran

    def test_resume_by_name_opens_the_session_in_its_folder_with_the_channel(self):
        sid = '11111111-2222-3333-4444-555555555555'
        self.transcript(sid, 'project-head', self.work)
        result, ran = self.run_cli('--resume', 'project-head', '--', '--model', 'opus')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ran['argv'], ['--dangerously-load-development-channels', 'server:agent-link',
                                       '--resume', sid, '--model', 'opus'])
        self.assertEqual(ran['cwd'], str(self.work))

    def test_resume_by_id_without_a_name_and_the_picker(self):
        sid = '66666666-7777-8888-9999-000000000000'
        self.transcript(sid, None, self.work)
        _, ran = self.run_cli('--resume', sid)
        self.assertEqual(ran['argv'][-2:], ['--resume', sid])
        _, ran = self.run_cli('--resume')
        self.assertEqual(ran['argv'], ['--dangerously-load-development-channels', 'server:agent-link', '--resume'])
        _, ran = self.run_cli('--continue')
        self.assertEqual(ran['argv'][-1], '--continue')

    def test_team_login_without_channels_opens_without_the_flag_or_a_prompt(self):
        from agent_link import claude_hooks
        sid = '11111111-2222-3333-4444-555555555555'
        self.transcript(sid, 'project-head', self.work)
        (self.root / 'claude' / '.claude.json').write_text(json.dumps({'oauthAccount': {'organizationType': 'claude_team'}}))
        result, ran = self.run_cli('--resume', 'project-head')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ran['argv'], ['--resume', sid])  # no first prompt: the hook does the waking
        self.assertIn('hook that wakes the session is missing', result.stderr)
        claude_hooks.install(self.root / 'claude' / 'settings.json')
        result, ran = self.run_cli('--continue')
        self.assertEqual(ran['argv'], ['--continue'])
        self.assertIn('the agent-link hook wakes this session', result.stderr)
        (self.root / 'claude' / 'remote-settings.json').write_text(json.dumps({'channelsEnabled': True}))
        result, ran = self.run_cli('--resume', 'project-head')
        self.assertEqual(ran['argv'][:2], ['--dangerously-load-development-channels', 'server:agent-link'])
        self.assertIn('push', result.stderr)

    def test_claude_subcommand_forms_register_on_launch(self):
        sid = '11111111-2222-3333-4444-555555555555'
        self.transcript(sid, 'project-head', self.work)
        for argv in (('claude', 'resume', 'project-head'), ('claude', '--resume', 'project-head')):
            result, ran = self.run_cli(*argv)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((ran['tool'], ran['argv'][-2:], ran['cwd'], ran['register']),
                             ('claude', ['--resume', sid], str(self.work), '1'))
        _, ran = self.run_cli('claude', 'continue')
        self.assertEqual(ran['argv'][-1], '--continue')

    def codex_thread(self, thread, title, cwd):
        rollout = self.root / 'codex/sessions/2026/11/02' / f'rollout-2026-11-02T10-00-00-{thread}.jsonl'
        rollout.parent.mkdir(parents=True, exist_ok=True)
        rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': thread, 'cwd': str(cwd)}}) + '\n')
        with open(self.root / 'codex/session_index.jsonl', 'a') as index:
            index.write(json.dumps({'id': thread, 'thread_name': title}) + '\n')

    def test_codex_resume_by_name_passes_the_thread_to_the_mcp_server(self):
        thread = '01a19a66-0c39-7fc2-a63e-6d7604dcebb9'
        self.codex_thread(thread, 'trainer', self.work)
        result, ran = self.run_cli('codex', 'resume', 'trainer', '--', '--model', 'gpt-5')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ran['argv'], ['-c', 'mcp_servers.agent-link.env.AGENT_LINK_REGISTER="1"',
                                       '-c', f'mcp_servers.agent-link.env.AGENT_LINK_THREAD="{thread}"',
                                       'resume', thread, '--model', 'gpt-5'])
        self.assertEqual(ran['cwd'], str(self.work))
        _, ran = self.run_cli('codex', '--resume', thread)
        self.assertEqual(ran['argv'][-2:], ['resume', thread])

    def test_codex_last_picks_this_folders_newest_thread_and_picker_has_none(self):
        self.codex_thread('22222222-0000-0000-0000-000000000000', 'elsewhere', self.root)
        result, ran = self.run_cli('codex', 'last')
        self.assertEqual(ran['argv'][-2:], ['resume', '22222222-0000-0000-0000-000000000000'])
        _, ran = self.run_cli('codex', 'resume')
        self.assertEqual(ran['argv'], ['-c', 'mcp_servers.agent-link.env.AGENT_LINK_REGISTER="1"', 'resume'])
        result, ran = self.run_cli('codex', 'resume', 'nobody')
        self.assertEqual((result.returncode, ran), (1, None))

    def test_unknown_name_and_mixing_with_subcommands_are_errors(self):
        result, ran = self.run_cli('--resume', 'nobody')
        self.assertEqual((result.returncode, ran), (1, None))
        self.assertIn('No Claude session named "nobody"', result.stderr)
        result, _ = self.run_cli('--resume', 'x', 'status')
        self.assertNotEqual(result.returncode, 0)


class UpdateTests(unittest.TestCase):
    def test_server_command_runs_in_the_clone_of_its_paired_path(self):
        from agent_link.cli import installed_version, remote_update_command
        command = remote_update_command('/srv/a b/agent-link/.venv/bin/agent-link')
        self.assertTrue(command.startswith("cd '/srv/a b/agent-link' && git pull --ff-only && ./setup.sh && "
                                           ".venv/bin/agent-link up --restart"))
        self.assertEqual(installed_version('[1/4] x\n[2/4] installed agent-link 0.7.0\n[3/4] y'), '0.7.0')
        self.assertIsNone(installed_version('nothing here'))

    def test_dry_run_only_prints_the_plan(self):
        with tempfile.TemporaryDirectory(prefix='al-') as state:
            env = {**os.environ, 'AGENT_HUB_STATE': state}
            result = subprocess.run([sys.executable, '-m', 'agent_link', 'update', '--dry-run'], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('would run: git -C', result.stdout)
            self.assertIn('setup.sh', result.stdout)
            self.assertNotIn('installed', result.stdout)

    def test_retired_config_keys_still_load(self):
        from agent_link.state import Config
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'config.toml').write_text('mirror_local_metadata = false\nmax_hops = 2\n')
            self.assertEqual(Config.load(root).max_hops, 2)
