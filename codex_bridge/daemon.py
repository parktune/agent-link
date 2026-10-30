"""Read-only local daemon diagnostics over its official Unix WebSocket interface."""
import asyncio
import json

from .rpc import RpcError


class DaemonUnavailable(RpcError):
    pass


async def daemon_info(timeout=10):
    process = await asyncio.create_subprocess_exec(
        'codex', 'app-server', 'daemon', 'version',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode:
        raise RpcError('Cannot inspect daemon version')
    info = json.loads(stdout)
    if info.get('status') == 'notRunning':
        raise DaemonUnavailable('No running daemon')
    if info.get('status') != 'running' or not info.get('socketPath'):
        raise RpcError('Cannot determine daemon control socket')
    return info


class DaemonReader:
    """A local read-only connection. No thread enumeration, mutation, or approvals."""
    METHODS = frozenset({'initialize', 'remoteControl/status/read'})

    def __init__(self, socket_path, timeout=10):
        self.socket_path = str(socket_path)
        self.timeout = timeout
        self.counter = 0

    async def __aenter__(self):
        try:
            from websockets.asyncio.client import unix_connect
        except ImportError:
            raise RpcError('Install local daemon diagnostics with: pip install -e ".[diagnostics]"') from None
        self.ws = await unix_connect(
            self.socket_path, uri='ws://localhost/rpc', open_timeout=self.timeout,
            close_timeout=2, max_size=8 * 1024 * 1024, ping_interval=None,
        )
        try:
            await self.call('initialize', {
                'clientInfo': {'name': 'codex_bridge_diagnostics', 'version': '0.1.0'},
                'capabilities': {'experimentalApi': True},
            })
            await self.ws.send(json.dumps({'method': 'initialized'}))
        except BaseException:
            await self.ws.close()
            raise
        return self

    async def __aexit__(self, *args):
        await self.ws.close()

    async def call(self, method, params):
        if method not in self.METHODS:
            raise RpcError('DaemonReader permits only initialization and Remote Control status reads')
        self.counter += 1
        request_id = self.counter
        async with asyncio.timeout(self.timeout):
            await self.ws.send(json.dumps({'id': request_id, 'method': method, 'params': params}))
            while True:
                message = json.loads(await self.ws.recv())
                if 'id' in message and 'method' in message:
                    await self.ws.send(json.dumps({'id': message['id'], 'error': {
                        'code': -32601, 'message': 'Read-only diagnostics cannot handle server requests',
                    }}))
                elif message.get('id') == request_id:
                    if 'error' in message:
                        raise RpcError('Daemon rejected read-only request')
                    return message.get('result')


async def read_daemon_status():
    info = await daemon_info()
    async with DaemonReader(info['socketPath']) as reader:
        status = await reader.call('remoteControl/status/read', {})
    # Internal callers can compare IDs in memory. Public output must use summary().
    return info, status


def summary(info, status):
    return {'type': 'daemon_status', 'status': status['status'],
            'has_environment': bool(status.get('environmentId')),
            'cli_version': info.get('cliVersion'),
            'daemon_version': info.get('appServerVersion'),
            'read_only': True, 'model_called': False}


async def cloud_status():
    try:
        info, status = await read_daemon_status()
        return summary(info, status)
    except (RpcError, TimeoutError):
        raise
    except Exception:
        raise RpcError('Local daemon status query failed') from None
