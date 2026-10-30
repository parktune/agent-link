"""Bounded JSON exchange over OpenSSH. No public listener or shell commands from peers."""
import asyncio
import base64
import json
from pathlib import Path
import shlex

from .security_store import WIRE_LIMIT, name


class SSHError(RuntimeError):
    pass


def remote_command(route):
    python = route['python']
    if not isinstance(python, str) or not python or len(python) > 4096 or any(c in python for c in '\r\n\0'):
        raise ValueError('Invalid remote Python executable path')
    profile = name(route.get('profile', 'default'))
    args = [python, '-m', 'codex_bridge', 'security', '--profile', profile, 'endpoint']
    if route['platform'] == 'posix':
        if not python.startswith('/'):
            raise ValueError('Use an absolute remote Python path')
        return shlex.join(args)
    if route['platform'] == 'windows':
        import re
        if not re.match(r'^[A-Za-z]:[\\/]', python):
            raise ValueError('Use an absolute Windows Python path')
        script = ('$ErrorActionPreference = \'Stop\'; '
                  '[Console]::InputEncoding = [Text.UTF8Encoding]::new($false); '
                  '[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); '
                  '$OutputEncoding = [Text.UTF8Encoding]::new($false); & ') + ' '.join("'" + arg.replace("'", "''") + "'" for arg in args)
        script += '; exit $LASTEXITCODE'
        encoded = base64.b64encode(script.encode('utf-16-le')).decode()
        return 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + encoded
    raise ValueError('Remote platform must be posix or windows')


def ssh_argv(route):
    host = name(route['host'])
    argv = ['ssh']
    if route.get('config'):
        argv += ['-F', str(Path(route['config']).expanduser().resolve())]
    argv += ['-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
             '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
             '-o', 'PermitLocalCommand=no', '-o', 'RemoteCommand=none',
             '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
             '-o', 'ConnectTimeout=10', '--', host, remote_command(route)]
    return argv


async def run_json(argv, payload, timeout=30):
    data = (json.dumps(payload, ensure_ascii=False) + '\n').encode()
    if len(data) > WIRE_LIMIT:
        raise ValueError('Exchange request too large')
    process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE,
                                                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def bounded_read(stream):
        chunks, size = [], 0
        while chunk := await stream.read(8192):
            size += len(chunk)
            if size > WIRE_LIMIT:
                raise SSHError('SSH endpoint output exceeded limit')
            chunks.append(chunk)
        return b''.join(chunks)

    async def write():
        process.stdin.write(data)
        await process.stdin.drain()
        process.stdin.close()

    tasks = [asyncio.create_task(bounded_read(process.stdout)),
             asyncio.create_task(bounded_read(process.stderr)), asyncio.create_task(write())]
    try:
        async with asyncio.timeout(timeout):
            stdout, _, _ = await asyncio.gather(*tasks)
            code = await process.wait()
        if code:
            raise SSHError('SSH failed. Verify the approved alias, key authentication, known_hosts and remote Python installation.')
        result = json.loads(stdout)
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise SSHError('Remote bridge rejected the request')
        return result['result']
    except (BrokenPipeError, ConnectionResetError):
        raise SSHError('SSH process disconnected before the exchange completed') from None
    except (json.JSONDecodeError, KeyError):
        raise SSHError('Remote bridge returned invalid JSON') from None
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class SSHTransport:
    async def request(self, route, payload):
        return await run_json(ssh_argv(route), payload)
