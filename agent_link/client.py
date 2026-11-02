"""IPC client for the local spoke daemon (server) or hub (local PC) unix socket."""
import asyncio
import itertools
import os

from .protocol import FRAME_LIMIT, ProtocolError, read_frame, write_frame


class SpokeClient:
    def __init__(self, socket_path, on_deliver=None):
        self.socket_path = os.fspath(socket_path)
        self.on_deliver = on_deliver
        self.pending = {}
        self.writer = None
        self.lock = asyncio.Lock()
        self._ids = itertools.count(1)
        self.reader_task = None

    @property
    def connected(self):
        return self.writer is not None

    async def connect(self):
        reader, self.writer = await asyncio.open_unix_connection(self.socket_path, limit=FRAME_LIMIT)
        self.reader_task = asyncio.create_task(self._read(reader))

    async def wait_closed(self):
        if self.reader_task:
            await asyncio.gather(self.reader_task, return_exceptions=True)

    async def close(self):
        if self.writer:
            self.writer.close()
        await self.wait_closed()

    async def _send(self, frame):
        if not self.writer:
            raise ProtocolError('offline', 'not connected to the agent-link daemon')
        async with self.lock:
            await write_frame(self.writer, frame)

    async def request(self, op, timeout=90, **fields):
        rid = f'c{next(self._ids)}'
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        try:
            await self._send({'op': op, 'rid': rid, **fields})
            reply = await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise ProtocolError('timeout', f'no answer to {op}') from None
        finally:
            self.pending.pop(rid, None)
        if reply['op'] == 'error':
            raise ProtocolError(reply.get('code') or 'invalid', reply.get('detail', ''))
        return reply

    async def _read(self, reader):
        try:
            while (frame := await read_frame(reader)) is not None:
                rid = frame.get('rid', '')
                if frame['op'] == 'deliver':
                    asyncio.create_task(self._deliver(frame))
                elif rid in self.pending and not self.pending[rid].done():
                    self.pending[rid].set_result(frame)
        except (ProtocolError, ConnectionError):
            pass
        finally:
            self.writer = None
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ProtocolError('offline', 'daemon connection closed'))

    async def _deliver(self, frame):
        message = frame.get('msg') or {}
        try:
            if not self.on_deliver:
                raise ProtocolError('inject_failed', 'no session adapter')
            await self.on_deliver(message)
            answer = {'op': 'delivered', 'id': message.get('id')}
        except ProtocolError as error:
            answer = error.frame(id=message.get('id'))
        except Exception as error:  # injection failures fall back to the mailbox on the daemon side
            answer = ProtocolError('inject_failed', str(error)[:300]).frame(id=message.get('id'))
        try:
            await self._send({**answer, 'rid': frame.get('rid')})
        except (ProtocolError, ConnectionError):
            pass
