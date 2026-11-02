import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from agent_link.inject import CodexQueue, channel_notification, claude_channel_enabled, codex_envelope
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
            await with_thread.register('trainer', 'work', thread_id='0199-abc')
            await without.register('notes', 'personal')
            self.assertEqual((with_thread.whoami()['push']['available'], without.whoami()['push']['available']), (True, False))
            sender = await self.session('gpu1', 'claude', 'alpha')
            self.assertEqual((await sender.send('codex:trainer', 'train'))['status'], 'delivered')
            self.assertEqual(queued[0][0], '0199-abc')
            self.assertIn('train', queued[0][1])
            self.assertEqual((await sender.send('codex:notes', 'note'))['status'], 'stored')
            box = await without.call('check_box', {})
            self.assertEqual([m['content'] for m in box['messages']], ['note'])
            self.assertEqual(box['messages'][0]['account'], 'personal')
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
            await first.register('head', 'work')
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
                             {'whoami', 'register', 'list_agents', 'send_message', 'check_box', 'recent_messages'})
            await until(lambda: True)
            await asyncio.sleep(0.3)  # adapter connects to the daemon in the background
            registered = await call(3, 'tools/call', {'name': 'register', 'arguments': {'name': 'head', 'account': 'work'}})
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
