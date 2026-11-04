"""Create a named Codex thread through the local Codex app-server, for `agent-link codex register <name>`.

Codex has no flag that names a new session at start, and a new session's thread ID is not known until it
runs. The app-server daemon (the one `codex queue` uses) can do both: `thread/start` creates the thread and
returns its ID, `thread/name/set` gives it the name (as /rename would). `codex resume <ID>` then opens it.
`codex app-server proxy` carries a WebSocket session to the daemon over stdio; this speaks just enough of it.
"""
import base64
import json
import os
import struct
import subprocess


class CodexAppError(RuntimeError):
    pass


class AppServer:
    def __init__(self, executable='codex', timeout=30):
        self.process = subprocess.Popen([executable, 'app-server', 'proxy'], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.timeout, self.next_id = timeout, 1
        key = base64.b64encode(os.urandom(16)).decode()
        self._write(f'GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                    f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'.encode())
        head = b''
        while not head.endswith(b'\r\n\r\n'):
            head += self._read(1)
        if b' 101 ' not in head.split(b'\r\n', 1)[0]:
            raise CodexAppError('the Codex app-server refused the connection: ' + head.split(b'\r\n', 1)[0].decode())
        self.call('initialize', {'clientInfo': {'name': 'agent-link', 'version': '1'}})
        self._send({'jsonrpc': '2.0', 'method': 'initialized'})

    def _write(self, data):
        try:
            self.process.stdin.write(data)
            self.process.stdin.flush()
        except OSError:
            raise CodexAppError(self._failure()) from None

    def _read(self, size):
        data = b''
        while len(data) < size:
            chunk = self.process.stdout.read(size - len(data))
            if not chunk:
                raise CodexAppError(self._failure())
            data += chunk
        return data

    def _failure(self):
        try:
            self.process.wait(timeout=2)
            detail = self.process.stderr.read().decode(errors='replace').strip()
        except subprocess.TimeoutExpired:
            detail = ''
        return ('cannot reach the Codex app-server daemon' + (f': {detail[-300:]}' if detail else '')
                + '. Start it with `codex app-server daemon start` and try again.')

    def _send(self, message):
        data, mask = json.dumps(message).encode(), os.urandom(4)
        size = len(data)
        if size < 126:
            head = bytes([0x81, 0x80 | size])
        elif size < 1 << 16:
            head = bytes([0x81, 0x80 | 126]) + struct.pack('>H', size)
        else:
            head = bytes([0x81, 0x80 | 127]) + struct.pack('>Q', size)
        self._write(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _receive(self):
        first, second = self._read(2)
        size = second & 0x7f
        if size == 126:
            size = struct.unpack('>H', self._read(2))[0]
        elif size == 127:
            size = struct.unpack('>Q', self._read(8))[0]
        return first & 0x0f, self._read(size)

    def call(self, method, params):
        ident, self.next_id = self.next_id, self.next_id + 1
        self._send({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params})
        while True:
            opcode, payload = self._receive()
            if opcode == 8:
                raise CodexAppError('the Codex app-server closed the connection')
            if opcode != 1:
                continue
            message = json.loads(payload)
            if message.get('id') != ident:
                continue  # notifications about other work on the daemon
            if 'error' in message:
                raise CodexAppError(f'{method}: {message["error"].get("message", message["error"])}')
            return message.get('result') or {}

    def close(self):
        self.process.kill()
        self.process.wait()


def create_named_thread(name, cwd, executable='codex'):
    """A new, empty Codex thread in `cwd` named `name`; returns its ID."""
    server = AppServer(executable)
    try:
        thread = server.call('thread/start', {'cwd': str(cwd)})['thread']['id']
        server.call('thread/name/set', {'threadId': thread, 'name': name})
        return thread
    finally:
        server.close()
