import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from codex_bridge.security_store import Store


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'security extra required')
class McpTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_stdio_session_catalog_and_read_only_inbox(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            store.initialize('mcp-test')
            params = StdioServerParameters(command=sys.executable, args=[
                '-m', 'codex_bridge', 'security', '--state-dir', root, 'mcp'],
                env=dict(os.environ))
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = {tool.name for tool in tools.tools}
                    self.assertEqual(names, {'bridge_peers', 'bridge_send_task', 'bridge_receive',
                                             'bridge_reply', 'bridge_task_status', 'bridge_ack'})
                    peers = await session.call_tool('bridge_peers', {})
                    self.assertFalse(peers.isError)
                    received = await session.call_tool('bridge_receive', {'wait_seconds': 0})
                    self.assertFalse(received.isError)
                    payload = json.loads(received.content[0].text)
                    self.assertEqual(payload['trust'], 'untrusted_remote_content')
                    self.assertEqual(payload['messages'], [])
                    rejected = await session.call_tool('bridge_reply', {'task_id': 'invalid', 'text': 'bad'})
                    self.assertTrue(rejected.isError)
