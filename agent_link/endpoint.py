"""SSH forced-command relay: hub frames on stdin <-> the spoke daemon's unix socket (10.3).

Stateless. Only the hub->host allowlist passes; anything else is answered `unauthorized` and the
connection closes. Authentication itself happens in the daemon (`hello` token check).
"""
import asyncio
import os
import sys

from .protocol import FRAME_LIMIT, HUB_TO_HOST, ProtocolError, encode, read_frame


async def relay(socket_path, stdin=None, stdout=None):
    loop = asyncio.get_running_loop()
    if stdin is None:
        stdin = asyncio.StreamReader(limit=FRAME_LIMIT)
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin)
    out = stdout or sys.stdout.buffer

    def emit(data):
        out.write(data)
        out.flush()

    try:
        sock_reader, sock_writer = await asyncio.open_unix_connection(os.fspath(socket_path), limit=FRAME_LIMIT)
    except OSError:
        emit(encode(ProtocolError('offline', 'spoke daemon is not running').frame()))
        return 1

    async def upstream():
        first = True
        while True:
            try:
                frame = await read_frame(stdin)
            except ProtocolError as error:
                emit(encode(error.frame()))
                return
            if frame is None:
                return
            allowed = frame['op'] in ('hello', 'enroll') if first else frame['op'] in HUB_TO_HOST - {'hello', 'enroll'}
            if not allowed:
                emit(encode(ProtocolError('unauthorized', f'frame {frame["op"]} not allowed').frame()))
                return
            first = False
            sock_writer.write(encode(frame))
            await sock_writer.drain()

    async def downstream():
        while line := await sock_reader.readline():
            emit(line)

    tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        sock_writer.close()
    return 0
