import asyncio
import json
import os
import shlex
import sys
import tempfile
import unittest
import unittest.mock
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

    async def start_watch(self, adapter, watchers):
        watchers.append(await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'agent_link', 'wait-box', '--box', adapter.identity['box'], '--interval', '0.05',
            stdout=asyncio.subprocess.PIPE))
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
            self.assertIn('hook_installed', watch)
            first = await self.start_watch(adapter, watchers)
            await until(lambda: adapter.whoami()['watch']['running_pid'] == first.pid)
            second = await self.start_watch(adapter, watchers)  # armed twice: the second leaves at once
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
            running = await self.start_watch(adapter, watchers)
            await until(lambda: adapter.whoami()['watch']['running_pid'] == running.pid)

            off = await adapter.call('set_watch', {'enabled': False})
            self.assertEqual((off['watch']['enabled'], off['stopped_pid']), (False, running.pid))
            out, _ = await asyncio.wait_for(running.communicate(), 10)
            self.assertEqual((running.returncode, 'stopped' in out.decode()), (0, True))
            self.assertFalse((await adapter.call('register', {'name': 'head'}))['watch']['enabled'])  # remembered

            self.assertTrue((await adapter.call('set_watch', {'enabled': True}))['watch']['enabled'])
            running = await self.start_watch(adapter, watchers)
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

    async def test_hook_wakes_a_mailbox_session_once_per_new_message(self):
        await self.server('gpu1', allow_origination='host')
        sid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': sid}):
            adapter = Adapter('claude', self.root / 'gpu1/s.sock', channel=False)
        task = asyncio.create_task(adapter.run())
        socket_path = self.root / 'gpu1/s.sock'

        async def hook(event, session=sid):
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'agent_link', 'wait-box', '--hook', '--socket', str(socket_path), '--interval', '0.05',
                stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            process.stdin.write(json.dumps({'session_id': session, 'hook_event_name': event}).encode())
            process.stdin.close()
            _, err = await asyncio.wait_for(process.communicate(), 15)
            if process.returncode == 2:
                hook.texts.append(err.decode())
            return process.returncode
        hook.texts = []
        try:
            await until(lambda: adapter.client.connected)
            self.assertEqual(await hook('Stop', 'not-registered-session'), 0)
            starting = asyncio.create_task(hook('SessionStart'))  # before the registration
            await asyncio.sleep(0.2)
            await adapter.register('head')
            await until(lambda: adapter.whoami()['watch']['running_pid'] is not None)
            sender = await self.session('gpu1', 'claude', 'alpha')
            await sender.send('claude:head', 'ping')
            self.assertEqual(await asyncio.wait_for(starting, 15), 2)

            again = asyncio.create_task(hook('Stop'))  # nothing new: no second wake
            await until(lambda: adapter.whoami()['watch']['running_pid'] is not None)
            self.assertFalse(again.done())
            await sender.send('claude:head', 'pong')
            self.assertEqual(await asyncio.wait_for(again, 15), 2)
            first, second = hook.texts
            self.assertIn('1 new message(s) for this session', first)
            self.assertIn('[agent-link] request from gpu1:claude:alpha', first)
            self.assertIn('--- BEGIN MESSAGE ---\nping\n--- END MESSAGE ---', first)
            self.assertIn('pong', second)
            self.assertNotIn('ping', second)  # carried once, then moved to read/
            self.assertEqual((await adapter.call('check_box', {}))['messages'], [])

            renamed = asyncio.create_task(hook('Stop'))  # a rename moves the box: the hook follows it
            await until(lambda: adapter.whoami()['watch']['running_pid'] is not None)
            adapter.registration = {'name': 'head2'}
            await adapter._register()
            await until(lambda: adapter.whoami()['watch']['running_pid'] is not None)
            self.assertFalse(renamed.done())
            await sender.send('claude:head2', 'after rename')
            self.assertEqual(await asyncio.wait_for(renamed, 15), 2)
            self.assertIn('after rename', hook.texts[-1])

            adapter.set_watch(False)
            self.assertEqual(await hook('Stop'), 0)  # watch off: ends at once
        finally:
            await self.stop(adapter, task, [])

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

    def test_new_only_does_not_wake_twice_for_the_same_unread_mail(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root, 'claude-head', 'received')
            folder.mkdir(parents=True)
            (folder / 'a.md').write_text('x')
            self.assertEqual(wait_for_mail(folder, 0, new_only=True), (1, None))  # unread at start: wake once

            def sleep(_):
                sleep.calls += 1
                if sleep.calls == 3:
                    (folder / 'b.md').write_text('y')
            sleep.calls = 0
            self.assertEqual(wait_for_mail(folder, 0, sleep, new_only=True), (2, None))  # a.md alone did not wake it
            self.assertEqual(sleep.calls, 3)

            (folder / 'a.md').unlink()  # read
            (folder / 'b.md').unlink()

            def later(_):
                later.calls += 1
                if later.calls == 2:
                    (folder / 'a.md').write_text('a new message under an old name')
            later.calls = 0
            self.assertEqual(wait_for_mail(folder, 0, later, new_only=True), (1, None))


class ClaudeHookTests(unittest.TestCase):
    def test_wake_text_carries_short_messages_and_leaves_long_ones_and_the_rest_unread(self):
        from agent_link.box import Box
        from agent_link import cli
        with tempfile.TemporaryDirectory() as root:
            box = Box(root)
            for n in range(cli.HOOK_MESSAGES + 1):
                content = 'x' * (cli.HOOK_CHARS + 10) if n == 1 else f'note {n}'
                box.put({**make_message('gpu1:claude:alpha', 'gpu1:claude:head', content),
                         'created_at': f'2026-11-03T10:00:0{n}Z'})
            folder = box.folder('gpu1:claude:head')
            text = cli.hook_wake_text(folder)
            self.assertIn(f'{cli.HOOK_MESSAGES} new message(s) for this session; 2 more unread', text)
            self.assertIn('note 0', text)
            self.assertNotIn(f'note {cli.HOOK_MESSAGES}', text)
            self.assertIn('[... cut here; the full text is still unread', text)
            self.assertEqual(len(list(folder.glob('*.md'))), 2)  # the long one and the one past the limit
            self.assertEqual(len(list((folder / 'read').glob('*.md'))), cli.HOOK_MESSAGES - 1)


    def test_install_keeps_other_hooks_and_replaces_its_own(self):
        from agent_link import claude_hooks
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'settings.json')
            other = {'hooks': [{'type': 'command', 'command': 'notify-send done'}]}
            path.write_text(json.dumps({'model': 'opus', 'hooks': {'Stop': [other]}}))
            self.assertFalse(claude_hooks.installed(path))
            claude_hooks.install(path, python='/old/python')
            claude_hooks.install(path, python='/new/python')  # again: replaced, not added twice
            settings = json.loads(path.read_text())
            self.assertEqual(settings['model'], 'opus')
            self.assertEqual(settings['hooks']['Stop'][0], other)
            for event in ('SessionStart', 'Stop'):
                ours = [h for e in settings['hooks'][event] for h in e['hooks'] if 'wait-box --hook' in h['command']]
                self.assertEqual(len(ours), 1)
                self.assertEqual((ours[0]['asyncRewake'], ours[0]['command'].split()[0]), (True, '/new/python'))
                self.assertGreaterEqual(ours[0]['timeout'], 86400)
            self.assertTrue(claude_hooks.installed(path))
            claude_hooks.install(path, remove=True)
            self.assertEqual(json.loads(path.read_text()), {'model': 'opus', 'hooks': {'Stop': [other]}})

    def test_install_refuses_a_broken_settings_file(self):
        from agent_link import claude_hooks
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'settings.json')
            path.write_text('{"model": "opus",')
            with self.assertRaises(ValueError):
                claude_hooks.install(path)
            self.assertEqual(path.read_text(), '{"model": "opus",')


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
            self.assertEqual(restart['command'], 'agent-link claude resume project-head')
            self.assertEqual(restart['by_id'], f'agent-link claude resume {sid}')
            self.assertRegex(restart['full'], r'^cd \S+ && claude --dangerously-load-development-channels '
                                              r'server:agent-link .*--resume project-head$')
            adapter.channel = True
            self.assertNotIn('restart', adapter.whoami())  # already live
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await adapter.client.close()
            del os.environ['CLAUDE_CODE_SESSION_ID'], os.environ['CLAUDE_CONFIG_DIR']


    def test_title_tracker_reads_only_what_was_appended(self):
        from agent_link.sessions import TitleTracker
        sid = '7fb85085-a689-4451-b6c0-9c5a17ad161d'
        path = self.root / 'claude/projects/p' / f'{sid}.jsonl'
        self.write(path, [{'type': 'custom-title', 'customTitle': 'first'}])
        titles = TitleTracker('claude', sid, config_dir=self.root / 'claude')
        self.assertEqual(titles(), 'first')
        with path.open('a') as handle:
            handle.write(json.dumps({'type': 'user', 'message': 'hi'}) + '\n')
            handle.write('{"type": "custom-title", "customTitle": "sec')  # still being written
        self.assertEqual(titles(), 'first')
        with path.open('a') as handle:
            handle.write('ond"}\n')
        self.assertEqual(titles(), 'second')

    async def test_registration_follows_a_rename_with_its_mailbox(self):
        await self.server('gpu1')
        sid = '11111111-2222-3333-4444-555555555555'
        transcript = self.root / 'claude/projects/p' / f'{sid}.jsonl'
        self.write(transcript, [{'type': 'custom-title', 'customTitle': 'old-name'}])
        os.environ['CLAUDE_CODE_SESSION_ID'] = sid
        os.environ['CLAUDE_CONFIG_DIR'] = str(self.root / 'claude')
        adapter = Adapter('claude', self.root / 'gpu1/s.sock', channel=False)
        task = asyncio.create_task(adapter.run())
        try:
            await until(lambda: adapter.client.connected)
            self.assertEqual((await adapter.call('register', {}))['id'], 'gpu1:claude:old-name')
            peer = await self.session('gpu1', 'claude', 'peer')
            self.assertEqual((await peer.send('old-name', 'before the rename'))['status'], 'stored')
            other = await self.session('gpu1', 'claude', 'taken')
            with transcript.open('a') as handle:
                handle.write(json.dumps({'type': 'custom-title', 'customTitle': 'taken'}) + '\n')
            await adapter.follow_rename()
            self.assertEqual(adapter.whoami()['id'], 'gpu1:claude:old-name')  # the new name is taken: kept
            self.assertIn('kept old-name', adapter.whoami()['rename_error'])
            with transcript.open('a') as handle:
                handle.write(json.dumps({'type': 'custom-title', 'customTitle': 'new-name'}) + '\n')
            await adapter.follow_rename()
            me = adapter.whoami()
            self.assertEqual(me['id'], 'gpu1:claude:new-name')
            self.assertNotIn('rename_error', me)
            spoke = self.spokes['gpu1']
            self.assertNotIn('gpu1:claude:old-name', spoke.agents)  # moved, not left behind as a closed session
            self.assertEqual(spoke.lookup(sid)['name'], 'new-name')
            self.assertEqual([m['content'] for m in spoke.box.unread('gpu1:claude:new-name')], ['before the rename'])
            self.assertFalse(spoke.box.folder('gpu1:claude:old-name').parent.exists())
            with self.assertRaisesRegex(Exception, 'not_found'):
                await peer.send('gpu1:claude:old-name', 'x')
            self.assertEqual((await peer.send('new-name', 'after'))['status'], 'stored')
            del other
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await adapter.client.close()
            del os.environ['CLAUDE_CODE_SESSION_ID'], os.environ['CLAUDE_CONFIG_DIR']


class LaunchRegisterTests(Environment):
    """Sessions opened by `agent-link claude|codex` register as soon as their MCP server starts."""

    async def run_adapter(self, adapter):
        task = asyncio.create_task(adapter.run())
        try:
            await until(lambda: adapter.identity is not None)
            return adapter.whoami()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await adapter.client.close()

    async def test_claude_registers_under_its_session_name(self):
        await self.server('gpu1')
        sid = '33333333-4444-5555-6666-777777777777'
        path = self.root / 'claude/projects/p' / f'{sid}.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'type': 'custom-title', 'customTitle': 'project-head'}) + '\n')
        env = {'AGENT_LINK_REGISTER': '1', 'CLAUDE_CODE_SESSION_ID': sid, 'CLAUDE_CONFIG_DIR': str(self.root / 'claude')}
        with unittest.mock.patch.dict(os.environ, env):
            me = await self.run_adapter(Adapter('claude', self.root / 'gpu1/s.sock', channel=False))
        self.assertEqual(me['id'], 'gpu1:claude:project-head')

    async def test_codex_registers_with_its_thread_for_push(self):
        await self.server('gpu1')
        thread = '01a19a66-0c39-7fc2-a63e-6d7604dcebb9'
        (self.root / 'codex').mkdir()
        (self.root / 'codex/session_index.jsonl').write_text(json.dumps({'id': thread, 'thread_name': 'trainer'}) + '\n')
        env = {'AGENT_LINK_REGISTER': '1', 'AGENT_LINK_THREAD': thread, 'CODEX_HOME': str(self.root / 'codex')}
        with unittest.mock.patch.dict(os.environ, env):
            me = await self.run_adapter(Adapter('codex', self.root / 'gpu1/s.sock'))
        self.assertEqual(me['id'], 'gpu1:codex:trainer')
        self.assertTrue(me['push']['available'])
        self.assertEqual(self.spokes['gpu1'].agents['gpu1:codex:trainer'].delivery, 'push')
