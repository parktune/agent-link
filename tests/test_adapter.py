import asyncio
import json
import os
import shlex
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from agent_link.box import wait_for_mail
from agent_link.inject import (CodexQueue, channel_notification, claude_channel_enabled, claude_launch_args,
                               claude_restart_argv, codex_envelope)
from agent_link.mcp_adapter import Adapter
from agent_link.protocol import ProtocolError, encode, make_message, parse_target, uuid7, validate_message
from tests.support import Environment, until


class ProtocolTests(unittest.TestCase):
    def test_targets_and_ids(self):
        self.assertEqual(parse_target('head'), (None, None, 'head'))
        self.assertEqual(parse_target('codex:head'), (None, 'codex', 'head'))
        self.assertEqual(parse_target('gpu-1:claude:head'), ('gpu-1', 'claude', 'head'))
        for bad in ('gpu:head', 'a:b:c:d', '-x', 'gpu:gemini:x'):
            with self.assertRaises(ProtocolError):
                parse_target(bad)
        value = uuid7()
        self.assertEqual((value[14], value[19] in '89ab'), ('7', True))
        self.assertLess(uuid7(), (__import__('time').sleep(0.002), uuid7())[1])

    def test_message_limits(self):
        message = make_message('local:claude:main', 'head', 'x' * 10)
        validate_message(message, 10)
        with self.assertRaisesRegex(ProtocolError, 'too_large'):
            validate_message(make_message('local:claude:main', 'head', '가' * 4), 10)
        with self.assertRaisesRegex(ProtocolError, 'in_reply_to'):
            validate_message(make_message('local:claude:main', 'head', 'x', 'reply'))


class InjectTests(unittest.IsolatedAsyncioTestCase):
    def test_channel_opt_in_is_read_from_the_launch_command(self):
        flag = '--dangerously-load-development-channels'
        self.assertTrue(claude_channel_enabled(cmdlines=[['node', 'x'], ['claude', flag, 'server:agent-link']]))
        self.assertTrue(claude_channel_enabled(cmdlines=[['claude', '--channels=plugin:a@b,server:agent-link']]))
        self.assertFalse(claude_channel_enabled(cmdlines=[['claude', flag, 'server:other']]))
        self.assertFalse(claude_channel_enabled(cmdlines=[['claude', 'server:agent-link']]))

    def test_restart_command_keeps_the_users_flags_and_adds_the_channel(self):
        flag = '--dangerously-load-development-channels'
        self.assertEqual(claude_launch_args([['python', 'mcp'], ['claude', '--resume', 'project-head']]), ['--resume', 'project-head'])
        self.assertEqual(claude_launch_args([['node', '/usr/lib/claude/cli.js', '-c']]), ['-c'])
        self.assertEqual(claude_launch_args([['bash']]), [])
        self.assertEqual(claude_restart_argv('project-head', ['--resume', 'project-head']),
                         ['claude', flag, 'server:agent-link', '--resume', 'project-head'])
        self.assertEqual(claude_restart_argv('x', ['--dangerously-skip-permissions', '-c', '--model', 'opus', '-n', 'old',
                                                   '--channels', 'plugin:a@b', '-r']),
                         ['claude', flag, 'server:agent-link', '--dangerously-skip-permissions', '--model', 'opus',
                          '--channels', 'plugin:a@b', '--resume', 'x'])
        self.assertEqual(claude_restart_argv('x', [f'{flag}=server:other', '--name=old', '--resume=abc']),
                         ['claude', flag, 'server:other', 'server:agent-link', '--resume', 'x'])
        self.assertEqual(claude_restart_argv('x', [flag, 'plugin:z@y', 'server:agent-link', '--verbose']),
                         ['claude', flag, 'plugin:z@y', 'server:agent-link', '--verbose', '--resume', 'x'])

    def test_push_payloads(self):
        message = make_message('gpu1:claude:head', 'local:claude:main', '보고', 'reply', uuid7(), 1)
        method, params = channel_notification(message)
        self.assertEqual(method, 'notifications/claude/channel')
        self.assertTrue(all(key.replace('_', '').isalnum() for key in params['meta']))
        self.assertEqual(params['meta']['in_reply_to'], message['in_reply_to'])
        self.assertIn('Do not answer', codex_envelope(message))

    async def test_codex_queue_argv(self):
        with tempfile.TemporaryDirectory() as root:
            log, fake = Path(root, 'argv.json'), Path(root, 'codex')
            fake.write_text(f'#!{sys.executable}\nimport json, sys\njson.dump(sys.argv[1:], open({str(log)!r}, "w"))\n'
                            'sys.exit(1 if "--thread=gone" in sys.argv else 0)\n')
            fake.chmod(0o700)
            await CodexQueue(str(fake))('0199-thread', '--resume\n두 줄')
            self.assertEqual(json.loads(log.read_text()), ['queue', '--thread=0199-thread', '--message=--resume\n두 줄', '--resume'])
            with self.assertRaises(RuntimeError):
                await CodexQueue(str(fake))('gone', 'x')


class AdapterTests(Environment):
    async def test_codex_adapter_pushes_with_thread_and_falls_back_to_box_without(self):
        await self.server('gpu1', allow_origination='host')
        queued = []

        async def fake_queue(thread, text):
            queued.append((thread, text))

        with_thread = Adapter('codex', self.root / 'gpu1/s.sock', codex=fake_queue)
        without = Adapter('codex', self.root / 'gpu1/s.sock', codex=fake_queue)
        tasks = [asyncio.create_task(a.run()) for a in (with_thread, without)]
        try:
            await until(lambda: with_thread.client.connected and without.client.connected)
            await with_thread.register('trainer', thread_id='0199-abc')
            await without.register('notes')
            self.assertEqual((with_thread.whoami()['push']['available'], without.whoami()['push']['available']), (True, False))
            sender = await self.session('gpu1', 'claude', 'alpha')
            self.assertEqual((await sender.send('codex:trainer', 'train'))['status'], 'delivered')
            self.assertEqual(queued[0][0], '0199-abc')
            self.assertIn('train', queued[0][1])
            self.assertEqual((await sender.send('codex:notes', 'note'))['status'], 'stored')
            box = await without.call('check_box', {})
            self.assertEqual([m['content'] for m in box['messages']], ['note'])
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for adapter in (with_thread, without):
                await adapter.client.close()

    async def test_restarted_session_recovers_registration_by_session_id(self):
        await self.server('gpu1')
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'sess-1'
        try:
            first = Adapter('claude', self.root / 'gpu1/s.sock', channel=False)
            task = asyncio.create_task(first.run())
            await until(lambda: first.client.connected)
            await first.register('head')
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await first.client.close()
            second = Adapter('claude', self.root / 'gpu1/s.sock', channel=False)
            task = asyncio.create_task(second.run())
            await until(lambda: second.identity is not None)
            self.assertEqual(second.whoami()['id'], 'gpu1:claude:head')
            self.assertFalse(second.whoami()['push']['available'])  # no channel flag -> mailbox
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await second.client.close()
        finally:
            del os.environ['CLAUDE_CODE_SESSION_ID']


    async def claude_without_push(self):
        await self.server('gpu1', allow_origination='host')
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': ''}):  # not the session running the tests
            adapter = Adapter('claude', self.root / 'gpu1/s.sock', channel=False)
        task = asyncio.create_task(adapter.run())
        await until(lambda: adapter.client.connected)
        return adapter, task

    async def start_watch(self, command, watchers):
        watchers.append(await asyncio.create_subprocess_exec(
            *shlex.split(command), '--interval', '0.05', stdout=asyncio.subprocess.PIPE))
        return watchers[-1]

    async def stop(self, adapter, task, watchers):
        for process in watchers:
            if process.returncode is None:
                process.kill()
                await process.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await adapter.client.close()

    async def test_claude_without_push_gets_a_watch_that_exits_on_mail(self):
        adapter, task = await self.claude_without_push()
        watchers = []
        try:
            self.assertNotIn('watch', adapter.whoami())  # nothing to watch before registering
            watch = (await adapter.register('head'))['watch']
            self.assertEqual((watch['enabled'], watch['running_pid']), (True, None))
            self.assertIn('wait-box', watch['command'])
            first = await self.start_watch(watch['command'], watchers)
            await until(lambda: adapter.whoami()['watch']['running_pid'] == first.pid)
            second = await self.start_watch(watch['command'], watchers)  # armed twice: the second leaves at once
            out, _ = await asyncio.wait_for(second.communicate(), 10)
            self.assertIn('already watched', out.decode())
            self.assertIsNone(first.returncode)

            sender = await self.session('gpu1', 'claude', 'alpha')
            self.assertEqual((await sender.send('claude:head', 'ping'))['status'], 'stored')
            out, _ = await asyncio.wait_for(first.communicate(), 10)
            self.assertEqual((first.returncode, '1 unread message(s)' in out.decode()), (0, True))
            reply = await adapter.call('check_box', {})
            self.assertEqual([m['content'] for m in reply['messages']], ['ping'])
            self.assertEqual(reply['watch'], {**watch, 'running_pid': None})  # ready to arm again
        finally:
            await self.stop(adapter, task, watchers)

    async def test_settings_turn_the_watch_off_and_on_and_withdraw_the_session(self):
        adapter, task = await self.claude_without_push()
        watchers = []
        try:
            watch = (await adapter.call('register', {'name': 'head'}))['watch']
            running = await self.start_watch(watch['command'], watchers)
            await until(lambda: adapter.whoami()['watch']['running_pid'] == running.pid)

            off = await adapter.call('set_watch', {'enabled': False})
            self.assertEqual((off['watch']['enabled'], off['stopped_pid']), (False, running.pid))
            out, _ = await asyncio.wait_for(running.communicate(), 10)
            self.assertEqual((running.returncode, 'stopped' in out.decode()), (0, True))
            self.assertFalse((await adapter.call('register', {'name': 'head'}))['watch']['enabled'])  # remembered

            self.assertTrue((await adapter.call('set_watch', {'enabled': True}))['watch']['enabled'])
            running = await self.start_watch(watch['command'], watchers)
            await until(lambda: adapter.whoami()['watch']['running_pid'] == running.pid)

            self.assertIn('gpu1:claude:head', [a['id'] for a in await adapter.call('list_agents', {})])
            gone = await adapter.call('unregister', {})
            self.assertEqual((gone['unregistered'], gone['registered'], gone['withdrawn']), ('gpu1:claude:head', False, True))
            self.assertNotIn('watch', gone)
            await asyncio.wait_for(running.wait(), 10)  # the watch stopped with the registration
            self.assertNotIn('gpu1:claude:head', [a['id'] for a in await adapter.call('list_agents', {})])
            with self.assertRaisesRegex(ProtocolError, 'unauthorized'):
                await adapter.call('set_watch', {'enabled': True})
            self.assertNotIn('withdrawn', await adapter.call('register', {'name': 'head'}))
        finally:
            await self.stop(adapter, task, watchers)

    async def test_watch_only_for_claude_without_push(self):
        identity = {'id': 'gpu1:claude:head', 'box': str(self.root / 'box/claude-head/received')}
        for tool, channel, expected in (('claude', False, True), ('claude', True, False), ('codex', None, False)):
            adapter = Adapter(tool, self.root / 's.sock', channel=channel)
            adapter.identity = identity
            self.assertEqual(adapter.watch() is not None, expected, (tool, channel))
            if not expected:
                with self.assertRaisesRegex(ProtocolError, 'only for Claude'):
                    adapter.set_watch(False)


class WaitForMailTests(unittest.TestCase):
    def test_waits_for_a_folder_that_does_not_exist_yet_and_ignores_a_stale_lock(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root, 'claude-head', 'received')
            folder.parent.mkdir()
            (folder.parent / 'watch.pid').write_text('999999999\n')  # no such process
            calls = []

            def sleep(_):
                calls.append(1)
                if len(calls) == 3:
                    folder.mkdir()
                    (folder / 'a.md').write_text('x')
                    (folder / 'b.tmp').write_text('half written')

            self.assertEqual(wait_for_mail(folder, 0, sleep), (1, None))
            self.assertEqual(len(calls), 3)
            self.assertFalse((folder.parent / 'watch.pid').exists())


class StdioTests(Environment):
    async def test_claude_adapter_over_stdio_emits_channel_notification(self):
        await self.server('gpu1', allow_origination='host')
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'agent_link', 'mcp', '--tool', 'claude', '--socket', str(self.root / 'gpu1/s.sock'),
            '--assume-channel', stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        lines = []

        async def reader():
            while line := await process.stdout.readline():
                lines.append(json.loads(line))

        reading = asyncio.create_task(reader())

        async def call(id, method, params):
            process.stdin.write(encode({'jsonrpc': '2.0', 'id': id, 'method': method, 'params': params}))
            await process.stdin.drain()
            await until(lambda: any(m.get('id') == id for m in lines))
            return next(m for m in lines if m.get('id') == id)

        try:
            init = await call(1, 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                                'clientInfo': {'name': 'test', 'version': '0'}})
            self.assertIn('claude/channel', init['result']['capabilities']['experimental'])
            self.assertIn('Never answer a type="reply"', init['result']['instructions'])
            process.stdin.write(encode({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))
            tools = await call(2, 'tools/list', {})
            self.assertEqual({t['name'] for t in tools['result']['tools']},
                             {'whoami', 'register', 'unregister', 'set_watch', 'list_agents', 'send_message', 'check_box',
                              'recent_messages'})
            await until(lambda: True)
            await asyncio.sleep(0.3)  # adapter connects to the daemon in the background
            registered = await call(3, 'tools/call', {'name': 'register', 'arguments': {'name': 'head'}})
            self.assertFalse(registered['result'].get('isError'), registered)
            sender = await self.session('gpu1', 'claude', 'alpha')
            self.assertEqual((await sender.send('head', '안녕'))['status'], 'delivered')
            await until(lambda: any(m.get('method') == 'notifications/claude/channel' for m in lines))
            note = next(m for m in lines if m.get('method') == 'notifications/claude/channel')
            self.assertEqual(note['params']['content'], '안녕')
            self.assertEqual(note['params']['meta']['from'], 'gpu1:claude:alpha')
        finally:
            process.stdin.close()
            process.kill()
            await process.wait()
            reading.cancel()


class SessionNameTests(Environment):
    def write(self, path, entries):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(e) + '\n' for e in entries))

    def test_reads_latest_rename_from_claude_and_codex_records(self):
        from agent_link.sessions import claude_session_name, codex_session_name
        sid = '7fb85085-a689-4451-b6c0-9c5a17ad161d'
        self.write(self.root / 'claude/projects/-home-x' / f'{sid}.jsonl', [
            {'type': 'user', 'message': 'custom-title in text only'},
            {'type': 'custom-title', 'customTitle': 'first', 'sessionId': sid},
            {'type': 'custom-title', 'customTitle': 'project-head', 'sessionId': sid}])
        self.assertEqual(claude_session_name(sid, self.root / 'claude'), 'project-head')
        self.assertIsNone(claude_session_name('../../etc', self.root / 'claude'))
        self.assertIsNone(claude_session_name('0' * 36, self.root / 'claude'))
        self.write(self.root / 'codex/session_index.jsonl', [
            {'id': 'thread-1', 'thread_name': 'old'}, {'id': 'thread-2', 'thread_name': 'other'},
            {'id': 'thread-1', 'thread_name': 'trainer'}])
        self.assertEqual(codex_session_name('thread-1', self.root / 'codex'), 'trainer')
        self.assertIsNone(codex_session_name('thread-3', self.root / 'codex'))

    async def test_register_without_a_name_uses_the_session_name(self):
        await self.server('gpu1')
        sid = '11111111-2222-3333-4444-555555555555'
        os.environ['CLAUDE_CODE_SESSION_ID'] = sid
        os.environ['CLAUDE_CONFIG_DIR'] = str(self.root / 'claude')
        adapter = Adapter('claude', self.root / 'gpu1/s.sock', channel=False)
        task = asyncio.create_task(adapter.run())
        try:
            await until(lambda: adapter.client.connected)
            with self.assertRaisesRegex(ProtocolError, 'no /rename name'):
                await adapter.call('register', {})
            self.write(self.root / 'claude/projects/p' / f'{sid}.jsonl',
                       [{'type': 'custom-title', 'customTitle': '리뷰 세션'}])
            with self.assertRaisesRegex(ProtocolError, 'cannot be an agent name'):
                await adapter.call('register', {})
            self.write(self.root / 'claude/projects/p' / f'{sid}.jsonl',
                       [{'type': 'custom-title', 'customTitle': 'project-head'}])
            self.assertEqual(adapter.whoami()['session_name'], 'project-head')
            self.assertEqual((await adapter.call('register', {}))['id'], 'gpu1:claude:project-head')
            self.assertEqual((await adapter.call('register', {'name': 'custom'}))['id'], 'gpu1:claude:custom')
            restart = adapter.whoami()['restart']
            self.assertEqual(restart['resume'], 'project-head')  # the session name, not the agent name
            self.assertRegex(restart['command'], r'^cd \S+ && claude --dangerously-load-development-channels '
                                                 r'server:agent-link .*--resume project-head$')
            self.assertTrue(restart['by_id'].endswith(f'--resume {sid}'))
            adapter.channel = True
            self.assertNotIn('restart', adapter.whoami())  # already live
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await adapter.client.close()
            del os.environ['CLAUDE_CODE_SESSION_ID'], os.environ['CLAUDE_CONFIG_DIR']
