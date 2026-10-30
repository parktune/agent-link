import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

from codex_bridge.security import Bridge, endpoint
from codex_bridge.security_store import Store, default_root, windows_powershell_env
from codex_bridge.ssh_transport import SSHError, remote_command, run_json, ssh_argv


class LocalTransport:
    def __init__(self, stores):
        self.stores = stores
        self.drop_once = False

    async def request(self, route, payload):
        result = endpoint(self.stores[route['host']], json.loads(json.dumps(payload)))
        if self.drop_once and payload['op'] == 'exchange':
            self.drop_once = False
            raise TimeoutError('Simulated lost reply after commit')
        return result


class MailboxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.a, self.b = Store(Path(self.temp.name, 'a')), Store(Path(self.temp.name, 'b'))
        self.a.initialize('alpha')
        self.b.initialize('beta')
        self.transport = LocalTransport({'alpha': self.a})
        self.ba, self.bb = Bridge(self.a, self.transport), Bridge(self.b, self.transport)
        self.route = {'host': 'alpha', 'python': 'C:\\Python\\python.exe' if os.name == 'nt' else '/usr/bin/python3',
                      'platform': 'windows' if os.name == 'nt' else 'posix', 'profile': 'default'}
        await self.bb.connect('alpha', self.route, self.a.pair_code())

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_bidirectional_tasks_with_only_one_ssh_route(self):
        task = await self.bb.send('alpha', 'hello')
        incoming = self.a.inbox()[0]
        self.assertEqual(incoming['text'], 'hello')
        await self.ba.reply(task['task_id'], 'hello received')
        await self.bb.sync()
        self.assertEqual(self.b.task(task['task_id'])['status'], 'completed')
        reverse = await self.ba.send('beta', 'reverse task')
        self.assertEqual(reverse['sync'][0]['status'], 'awaiting_remote_poll')
        await self.bb.sync()
        await self.bb.reply(reverse['task_id'], 'reverse result')
        self.assertEqual(self.a.task(reverse['task_id'])['status'], 'completed')

    async def test_lost_response_retries_same_uuid_without_duplicate_execution(self):
        self.transport.drop_once = True
        task = await self.bb.send('alpha', 'retry')
        self.assertEqual(task['sync'][0]['status'], 'failed')
        self.assertEqual(len(self.a.inbox()), 1)
        await self.bb.sync()
        self.assertEqual(len(self.a.inbox()), 1)
        self.assertEqual(self.b.pending(self.a.identity()['agent_id'])[0], [])

    async def test_restart_retains_identity_routes_and_inbox(self):
        before = self.b.identity()
        await self.bb.send('alpha', 'persist')
        reopened = Store(Path(self.temp.name, 'b'))
        self.assertEqual(reopened.identity(), before)
        self.assertEqual(reopened.peers()[0]['alias'], 'alpha')
        self.assertEqual((await Bridge(reopened, self.transport).sync())[0]['status'], 'synced')

    async def test_revoked_inbound_peer_cannot_deliver(self):
        self.a.revoke('beta')
        task = await self.bb.send('alpha', 'forbidden')
        self.assertEqual(task['sync'][0]['status'], 'failed')
        self.assertEqual(self.a.inbox(), [])

    async def test_unrelated_or_spoofed_task_response_rejected(self):
        with self.assertRaises(ValueError):
            self.a.send('beta', 'invented completion', 'task_completed', str(uuid.uuid4()))
        peer = self.b.peer('alpha')
        malicious = {'id': str(uuid.uuid4()), 'task_id': str(uuid.uuid4()), 'type': 'agent_message',
                     'sender': str(uuid.uuid4()), 'recipient': self.a.identity()['agent_id'], 'text': 'spoof'}
        with self.assertRaises(PermissionError):
            self.a.exchange(self.b.identity()['agent_id'], peer['secret'], [malicious], [])
        self.assertEqual(self.a.inbox(), [])

    async def test_terminal_status_cannot_regress(self):
        task = await self.bb.send('alpha', 'work')
        await self.ba.reply(task['task_id'], 'finished')
        with self.assertRaises(ValueError):
            self.a.send('beta', 'late progress', 'task_progress', task['task_id'])
        await self.bb.sync()
        self.assertEqual(self.b.task(task['task_id'])['text'], 'finished')

    async def test_pruning_keeps_replay_protection(self):
        await self.bb.send('alpha', 'original')
        incoming = self.a.inbox()[0]
        self.a.ack(incoming['id'])
        self.assertEqual(self.a.prune()['deleted_messages'], 1)
        peer = self.b.peer('alpha')
        self.a.exchange(self.b.identity()['agent_id'], peer['secret'], [incoming], [])
        self.assertEqual(self.a.inbox(), [])
        altered = dict(incoming, text='changed')
        with self.assertRaises(ValueError):
            self.a.exchange(self.b.identity()['agent_id'], peer['secret'], [altered], [])

    async def test_remote_cannot_request_execution_or_file_reads(self):
        for op in ('exec', 'turn/start', 'read_file', 'command/exec'):
            with self.assertRaises(ValueError):
                endpoint(self.a, {'op': op, 'path': '/etc/passwd'})

    async def test_public_peer_listing_does_not_leak_tokens(self):
        secret = self.b.peer('alpha')['secret']
        self.assertNotIn(secret, json.dumps(self.b.peers()))
        self.assertNotIn('secret', json.dumps(self.b.peers()))

    async def test_in_flight_revocation_prevents_local_ingestion(self):
        self.a.send('beta', 'pending remote content', 'agent_message')
        original = self.transport.request

        async def revoke_before_return(route, payload):
            response = await original(route, payload)
            if payload['op'] == 'exchange':
                self.b.revoke('alpha')
            return response

        self.transport.request = revoke_before_return
        result = await self.bb.sync()
        self.assertEqual(result[0]['status'], 'failed')
        self.assertEqual(self.b.inbox(), [])


class EnrollmentTests(unittest.TestCase):
    def test_wrong_code_attempt_limit_is_persistent_and_one_use(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            store.initialize('alpha')
            code = store.pair_code()
            bid = str(uuid.uuid4())
            for _ in range(5):
                with self.assertRaises(PermissionError):
                    Store(root).enroll(bid, 'beta', 'WRONG')
            with self.assertRaises(PermissionError):
                store.enroll(bid, 'beta', code)
            code = store.pair_code()
            store.enroll(bid, 'beta', code)
            with self.assertRaises(PermissionError):
                store.enroll(str(uuid.uuid4()), 'gamma', code)


class CommandTests(unittest.TestCase):
    def test_ssh_enforces_host_keys_and_disables_forwarding(self):
        route = {'host': 'approved-alias', 'python': '/usr/bin/python3', 'platform': 'posix'}
        args = ssh_argv(route)
        for option in ('StrictHostKeyChecking=yes', 'BatchMode=yes', 'ForwardAgent=no', 'ClearAllForwardings=yes'):
            self.assertIn(option, args)
        self.assertEqual(args[-2], 'approved-alias')
        with self.assertRaises(ValueError):
            ssh_argv(dict(route, host='-oProxyCommand=evil'))

    def test_posix_and_windows_shell_quoting(self):
        import shlex
        import base64
        path = "/tmp/a '$(touch SHOULD_NOT_RUN)'/python"
        result = remote_command({'python': path, 'platform': 'posix'})
        self.assertEqual(shlex.split(result)[0], path)
        result = remote_command({'python': "C:\\Program Files\\a'; bad-command; 'python.exe", 'platform': 'windows'})
        script = base64.b64decode(result.split()[-1]).decode('utf-16-le')
        self.assertIn("a''; bad-command; ''python.exe'", script)
        self.assertNotIn('bad-command', result)
        with self.assertRaises(ValueError):
            remote_command({'python': '/tmp/python\ncommand', 'platform': 'posix'})


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_endpoint_subprocess_and_denied_operation(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            own = store.initialize('endpoint')
            argv = [sys.executable, '-m', 'codex_bridge', 'security', '--state-dir', root, 'endpoint']
            self.assertEqual(await run_json(argv, {'op': 'hello'}), own)
            with self.assertRaises(SSHError):
                await run_json(argv, {'op': 'exec', 'command': 'should never execute'})

    async def test_large_output_and_timeout_are_bounded(self):
        with self.assertRaises(SSHError):
            await run_json([sys.executable, '-c', 'import sys; sys.stdin.readline(); print("x"*300000)'], {})
        with self.assertRaisesRegex(SSHError, 'timed out after 0.1 seconds'):
            await run_json([sys.executable, '-c', 'import time; time.sleep(60)'], {}, timeout=.1)

    async def test_stderr_secrets_are_not_exposed(self):
        with self.assertRaises(SSHError) as caught:
            await run_json([sys.executable, '-c', 'import sys; sys.stderr.write("secret-token"); sys.exit(1)'], {})
        self.assertNotIn('secret-token', str(caught.exception))


class UnicodeProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_utf8_exchange_on_native_endpoint(self):
        with tempfile.TemporaryDirectory() as root:
            server = Store(root)
            own = server.initialize('unicode-server')
            peer_id = str(uuid.uuid4())
            argv = [sys.executable, '-m', 'codex_bridge', 'security', '--state-dir', root, 'endpoint']
            credential = await run_json(argv, {'op': 'enroll', 'agent_id': peer_id, 'name': 'unicode-client', 'code': server.pair_code()})
            message = {'id': str(uuid.uuid4()), 'task_id': str(uuid.uuid4()), 'sender': peer_id,
                       'recipient': own['agent_id'], 'type': 'agent_message', 'text': '사내 테스트 🚀'}
            await run_json(argv, {'op': 'exchange', 'agent_id': peer_id, 'token': credential['token'], 'messages': [message], 'acks': []})
            self.assertEqual(server.inbox()[0]['text'], '사내 테스트 🚀')
            server.send('unicode-client', '응답 ✅', 'agent_message')
            result = await run_json(argv, {'op': 'exchange', 'agent_id': peer_id, 'token': credential['token'], 'messages': [], 'acks': []})
            self.assertEqual(result['messages'][0]['text'], '응답 ✅')


class BoundaryTests(unittest.TestCase):
    def test_failed_batch_is_atomic(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            own = store.initialize('server')
            peer = str(uuid.uuid4())
            token = store.enroll(peer, 'peer', store.pair_code())['token']
            good = {'id': str(uuid.uuid4()), 'task_id': str(uuid.uuid4()), 'sender': peer,
                    'recipient': own['agent_id'], 'type': 'agent_message', 'text': 'valid'}
            bad = dict(good, id=str(uuid.uuid4()), sender=str(uuid.uuid4()))
            with self.assertRaises(PermissionError):
                store.exchange(peer, token, [good, bad], [])
            self.assertEqual(store.inbox(), [])

    def test_ack_cannot_cross_routes_even_after_pruning(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            own = store.initialize('server')
            p1, p2 = str(uuid.uuid4()), str(uuid.uuid4())
            t1 = store.enroll(p1, 'one', store.pair_code())['token']
            t2 = store.enroll(p2, 'two', store.pair_code())['token']
            message = store.send('one', 'only peer one', 'agent_message')
            with self.assertRaises(PermissionError):
                store.exchange(p2, t2, [], [message['id']])
            store.exchange(p1, t1, [], [message['id']])
            store.prune()
            with self.assertRaises(ValueError):
                store.exchange(p2, t2, [], [message['id']])

    def test_expired_code_is_not_accepted(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            store.initialize('server')
            with patch('codex_bridge.security_store.time.time', return_value=0):
                code = store.pair_code()
            with self.assertRaises(PermissionError):
                store.enroll(str(uuid.uuid4()), 'peer', code)


@unittest.skipUnless(os.name == 'nt', 'Windows DACL regression test')
class WindowsAclTests(unittest.TestCase):
    def test_existing_explicit_everyone_permissions_are_removed(self):
        import base64
        import subprocess
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root, 'default')
            folder.mkdir()
            db_path = folder / 'security.sqlite3'
            db_path.touch()
            for target in (folder, db_path):
                subprocess.run(['icacls', str(target), '/grant', '*S-1-1-0:F'], capture_output=True, check=True)
            store = Store(root)
            for target in (store.root, store.path):
                literal = str(target).replace("'", "''")
                script = (f"$acl=Get-Acl -LiteralPath '{literal}'; "
                          "$owner=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
                          "$rules=@($acl.Access | ForEach-Object { $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value }); "
                          "@{owner=$owner; rules=$rules; protected=$acl.AreAccessRulesProtected}|ConvertTo-Json -Compress")
                encoded = base64.b64encode(script.encode('utf-16-le')).decode()
                result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], capture_output=True, text=True, check=True, env=windows_powershell_env())
                acl = json.loads(result.stdout)
                self.assertTrue(acl['protected'])
                self.assertEqual(set(acl['rules']), {acl['owner'], 'S-1-5-18'})


class RenameCompatibilityTests(unittest.TestCase):
    def test_existing_state_survives_product_rename(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            parent = Path(root)
            environment_key = 'LOCALAPPDATA' if os.name == 'nt' else 'XDG_STATE_HOME'
            with patch.dict(os.environ, {environment_key: root}):
                self.assertEqual(default_root(), parent / 'agent-link')
                legacy = parent / 'codex-bridge'
                legacy.mkdir()
                legacy_store = Store(legacy)
                original = legacy_store.initialize('existing-peer')
                self.assertEqual(Store().identity(), original)
                (parent / 'agent-link').mkdir()
                self.assertEqual(default_root(), parent / 'agent-link')
