"""Real unix sockets and real endpoint subprocesses; fake sessions instead of models."""
import asyncio
from pathlib import Path
import secrets
import sys
import tempfile
import unittest

from agent_link.box import Box
from agent_link.client import SpokeClient
from agent_link.hub import CommandTransport, Hub
from agent_link.protocol import ProtocolError
from agent_link.spoke import Spoke, SpokeStore
from agent_link.state import Config, Log


class FakeSession:
    """Stands in for an MCP adapter: registers and records what was pushed to it."""

    def __init__(self, socket_path, tool, name, delivery='push', fail=False):
        self.received, self.fail = [], fail
        self.client = SpokeClient(socket_path, self.on_deliver)
        self.tool, self.name, self.delivery = tool, name, delivery
        self.id = None

    async def on_deliver(self, message):
        if self.fail:
            raise ProtocolError('inject_failed', 'simulated push failure')
        self.received.append(message)

    async def start(self):
        await self.client.connect()
        reply = await self.client.request('register', tool=self.tool, name=self.name,
                                          delivery=self.delivery, session_id=f'session-{self.name}')
        self.id = reply['id']
        return self

    async def send(self, to, content, type='request', in_reply_to=None):
        return await self.client.request('send', to=to, content=content, type=type, in_reply_to=in_reply_to)

    async def box(self):
        return (await self.client.request('receive'))['messages']

    async def stop(self):
        await self.client.close()


async def until(predicate, timeout=10):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError('condition not reached')
        await asyncio.sleep(0.05)


class Environment(unittest.IsolatedAsyncioTestCase):
    """A hub plus server spokes in one temp dir; links run `python -m agent_link agent-hub endpoint`."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='al-')
        self.root = Path(self.temp.name)
        self.spokes, self.sessions, self.broken = {}, [], set()
        self.hub = None

    async def asyncTearDown(self):
        for session in self.sessions:
            await session.stop()
        if self.hub:
            await self.hub.close()
        for spoke in self.spokes.values():
            await spoke.close()
        self.temp.cleanup()

    def config(self, host, **values):
        return Config(host_name=host, **values)

    async def server(self, host, **config):
        folder = self.root / host
        spoke = Spoke(self.config(host, **config), SpokeStore(folder / 'spoke.sqlite3'), Log(),
                      Box(folder / 'box'))
        await spoke.start(folder / 's.sock')
        self.spokes[host] = spoke
        return spoke

    def transport(self, record):
        if record['name'] in self.broken:
            return CommandTransport([sys.executable, '-c', 'import sys; sys.exit(1)'])
        return CommandTransport([sys.executable, '-m', 'agent_link', 'agent-hub', 'endpoint',
                                 '--socket', str(self.root / record['name'] / 's.sock')])

    async def start_hub(self, **config):
        self.hub = Hub(self.config('hub', **config), self.root / 'hub', Log(), self.root / 'hub-box', self.transport)
        await self.hub.start(self.root / 'hub.sock')
        return self.hub

    async def stop_hub(self):
        await self.hub.close()
        self.hub = None

    def enroll(self, host, mode='stream'):
        token = secrets.token_urlsafe(32)
        spoke = self.spokes[host]
        code, _ = spoke.store.invite()
        spoke.store.enroll(code, token)
        from agent_link.hub import HubStore
        store = HubStore(self.root / 'hub' / 'hub.sqlite3')
        store.add_host(host, host, '/unused', token, mode=mode)
        store.db.close()
        return token

    async def session(self, where, tool, name, **options):
        socket_path = self.root / ('hub.sock' if where == 'local' else f'{where}/s.sock')
        session = await FakeSession(socket_path, tool, name, **options).start()
        self.sessions.append(session)
        return session

    async def connected(self, host):
        def ready():
            online = {a.id for a in self.spokes[host].agents.values() if a.conn}
            seen = {a['id'] for a in self.hub.presence.get(host, []) if a['status'] == 'online'}
            return self.hub.links[host].connected and online <= seen
        await until(ready)
