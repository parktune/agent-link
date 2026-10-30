import asyncio
import contextlib
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from codex_bridge.cloud import cloud_probe
from codex_bridge.daemon import DaemonReader, summary
from codex_bridge.rpc import RpcError

try:
    from websockets.asyncio.server import unix_serve
    from websockets.exceptions import ConnectionClosed
except ImportError:
    unix_serve = None


@unittest.skipUnless(unix_serve is not None and hasattr(socket, 'AF_UNIX'), 'diagnostics extra and Unix required')
class DaemonSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='codex-bridge-test-')
        self.path = Path(self.temp.name, 'control.sock')
        self.calls = []
        self.mode = 'normal'
        self.server = await unix_serve(self.handle, str(self.path))

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.temp.cleanup()

    async def handle(self, ws):
        try:
            async for raw in ws:
                message = json.loads(raw)
                self.calls.append(message)
                if message.get('method') == 'initialized':
                    continue
                if message['method'] == 'initialize':
                    await ws.send(json.dumps({'id': message['id'], 'result': {}}))
                    continue
                if self.mode == 'timeout':
                    await ws.wait_closed()
                    return
                if self.mode == 'approval':
                    await ws.send(json.dumps({'id': 700, 'method': 'item/commandExecution/requestApproval', 'params': {}}))
                    self.calls.append(json.loads(await ws.recv()))
                await ws.send(json.dumps({'method': 'account/updated', 'params': {'private': 'not-output'}}))
                if self.mode == 'error':
                    reply = {'id': message['id'], 'error': {'message': 'private-account-token'}}
                else:
                    reply = {'id': message['id'], 'result': {
                        'status': 'connected', 'installationId': 'private-id', 'environmentId': 'private-env',
                    }}
                await ws.send(json.dumps(reply))
        except ConnectionClosed:
            pass

    async def test_real_unix_handshake_and_sanitized_status(self):
        async with DaemonReader(self.path) as reader:
            result = await reader.call('remoteControl/status/read', {})
        public = summary({}, result)
        self.assertEqual(public['status'], 'connected')
        self.assertTrue(public['read_only'])
        self.assertNotIn('private', json.dumps(public))
        methods = [m['method'] for m in self.calls]
        self.assertEqual(methods, ['initialize', 'initialized', 'remoteControl/status/read'])

    async def test_mutation_rejected_before_send(self):
        async with DaemonReader(self.path) as reader:
            with self.assertRaisesRegex(RpcError, 'permits only'):
                await reader.call('remoteControl/disable', {})
        self.assertNotIn('remoteControl/disable', [m.get('method') for m in self.calls])

    async def test_server_approval_request_is_rejected(self):
        self.mode = 'approval'
        async with DaemonReader(self.path) as reader:
            await reader.call('remoteControl/status/read', {})
        rejection = next(m for m in self.calls if m.get('id') == 700)
        self.assertIn('error', rejection)

    async def test_timeout_is_bounded(self):
        self.mode = 'timeout'
        async with DaemonReader(self.path, timeout=0.1) as reader:
            with self.assertRaises(TimeoutError):
                await reader.call('remoteControl/status/read', {})

    async def test_upstream_error_body_is_not_exposed(self):
        self.mode = 'error'
        async with DaemonReader(self.path) as reader:
            with self.assertRaises(RpcError) as caught:
                await reader.call('remoteControl/status/read', {})
        self.assertNotIn('private-account-token', str(caught.exception))


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_owner_blocks_enable_and_pairing(self):
        candidate = AsyncMock()
        candidate.call.return_value = {'status': 'disabled', 'installationId': 'same-id'}
        context = AsyncMock()
        context.__aenter__.return_value = candidate
        daemon = {'status': 'connected', 'installationId': 'same-id'}
        with patch('codex_bridge.daemon.read_daemon_status', new=AsyncMock(return_value=({}, daemon))), \
             patch('codex_bridge.cloud.AppServer', return_value=context), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            code = await cloud_probe()
        self.assertEqual(code, 2)
        self.assertEqual(candidate.call.await_args_list[0].args[0], 'remoteControl/status/read')
        self.assertEqual(candidate.call.await_count, 1)
        self.assertFalse(json.loads(output.getvalue())['remote_control_mutated'])

    async def test_unknown_daemon_state_fails_closed(self):
        with patch('codex_bridge.daemon.read_daemon_status', new=AsyncMock(side_effect=TimeoutError())), \
             patch('codex_bridge.cloud.AppServer') as create, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(await cloud_probe(), 1)
        create.assert_not_called()
