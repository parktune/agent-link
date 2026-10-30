import sys
import unittest
from pathlib import Path

from codex_bridge.rpc import AppServer, RpcError


class RpcTests(unittest.IsolatedAsyncioTestCase):
    def server(self):
        return AppServer([sys.executable, str(Path(__file__).with_name("fake_server.py"))], timeout=2)

    async def test_notification_race_and_thread_turn_filter(self):
        async with self.server() as server:
            thread = await server.start_thread(Path.cwd())
            self.assertEqual(await server.turn(thread, "hello"), "hello received")

    async def test_failed_turn_is_not_success(self):
        async with self.server() as server:
            with self.assertRaisesRegex(RpcError, "Turn failed"):
                await server.turn("thread-a", "fail")

    async def test_rpc_error(self):
        async with self.server() as server:
            with self.assertRaisesRegex(RpcError, "unknown method"):
                await server.call("unknown", {})

    async def test_disconnect_fails_pending_request(self):
        async with self.server() as server:
            with self.assertRaisesRegex(RpcError, "disconnected"):
                await server.turn("thread-a", "disconnect")
