"""Agent IDs, the message model and JSON Lines framing shared by hub, spoke daemon and adapters."""
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

PROTOCOL_VERSION = 1
TOOLS = ('claude', 'codex')
LOCAL_HOST = 'local'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
TYPES = ('request', 'reply')
MESSAGE_FIELDS = frozenset({'id', 'from', 'to', 'type', 'in_reply_to', 'content', 'hops', 'created_at'})
FRAME_LIMIT = 1 << 20

# hub -> host frames the endpoint lets through (8.3). `ping` is a keepalive addition.
HUB_TO_HOST = frozenset({'hello', 'enroll', 'deliver', 'ack', 'exchange', 'ping', 'directory'})
HOST_TO_HUB = frozenset({'hello_ok', 'enroll_ok', 'delivered', 'error', 'outbox', 'presence', 'exchange_ok', 'pong'})

ERROR_CODES = frozenset({
    'not_found', 'offline', 'ambiguous', 'too_large', 'hops_exceeded', 'unauthorized', 'reply_to_reply',
    'origination_denied', 'outbox_full', 'invalid', 'timeout', 'inject_failed', 'unsupported_version',
})


class ProtocolError(Exception):
    def __init__(self, code, detail=''):
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code, self.detail = code, detail

    def frame(self, **extra):
        return {'op': 'error', 'code': self.code, 'detail': self.detail, **extra}


def check_name(value, what='name'):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ProtocolError('invalid', f'{what} must be 1-64 of A-Z a-z 0-9 _ . - and start with a letter or digit')
    return value


def check_tool(value):
    if value not in TOOLS:
        raise ProtocolError('invalid', f'tool must be one of {", ".join(TOOLS)}')
    return value


def agent_id(host, tool, name):
    return f'{check_name(host, "host")}:{check_tool(tool)}:{check_name(name)}'


def parse_target(text):
    """`name`, `tool:name` or `host:tool:name` -> (host | None, tool | None, name)."""
    if not isinstance(text, str):
        raise ProtocolError('invalid', 'target must be a string')
    parts = text.split(':')
    if len(parts) == 1:
        return None, None, check_name(parts[0])
    if len(parts) == 2:
        if parts[0] not in TOOLS:
            raise ProtocolError('invalid', 'use name, tool:name or host:tool:name')
        return None, parts[0], check_name(parts[1])
    if len(parts) == 3:
        return check_name(parts[0], 'host'), check_tool(parts[1]), check_name(parts[2])
    raise ProtocolError('invalid', 'use name, tool:name or host:tool:name')


def uuid7():
    """RFC 9562 UUIDv7: 48-bit millisecond timestamp, then random bits."""
    value = (time.time_ns() // 1_000_000) << 80 | int.from_bytes(os.urandom(10), 'big')
    value = value & ~(0xF << 76) | 0x7 << 76
    value = value & ~(0x3 << 62) | 0x2 << 62
    return str(uuid.UUID(int=value))


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def make_message(sender, to, content, type='request', in_reply_to=None, hops=0):
    return {'id': uuid7(), 'from': sender, 'to': to, 'type': type, 'in_reply_to': in_reply_to,
            'content': content, 'hops': hops, 'created_at': now_iso()}


def content_size(message):
    return len(message['content'].encode())


def validate_message(message, max_content=65536):
    if not isinstance(message, dict) or set(message) != MESSAGE_FIELDS:
        raise ProtocolError('invalid', 'message fields')
    try:
        uuid.UUID(message['id'])
        if message['in_reply_to'] is not None:
            uuid.UUID(message['in_reply_to'])
    except (ValueError, TypeError, AttributeError):
        raise ProtocolError('invalid', 'message ids must be UUIDs') from None
    host, tool, _ = parse_target(message['from'])
    if host is None or tool is None:
        raise ProtocolError('invalid', 'from must be a full host:tool:name ID')
    parse_target(message['to'])
    if message['type'] not in TYPES:
        raise ProtocolError('invalid', 'type must be request or reply')
    if message['type'] == 'reply' and message['in_reply_to'] is None:
        raise ProtocolError('invalid', 'a reply needs in_reply_to')
    if not isinstance(message['hops'], int) or isinstance(message['hops'], bool) or message['hops'] < 0:
        raise ProtocolError('invalid', 'hops must be a non-negative integer')
    if not isinstance(message['content'], str) or not isinstance(message['created_at'], str):
        raise ProtocolError('invalid', 'content and created_at must be strings')
    if content_size(message) > max_content:
        raise ProtocolError('too_large', f'content exceeds {max_content} bytes')
    return message


def metadata(message):
    """What may leave a host about a locally delivered message: never the body (4.2)."""
    return {'id': message['id'], 'from': message['from'], 'to': message['to'], 'type': message['type'],
            'size': content_size(message), 'at': now_iso()}


def encode(frame):
    return (json.dumps(frame, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


async def read_frame(reader):
    """One JSON object per line. Returns None at EOF."""
    try:
        line = await reader.readline()
    except ValueError:  # asyncio raises this when a line exceeds the stream limit
        raise ProtocolError('too_large', 'frame exceeds limit') from None
    if not line:
        return None
    if not line.endswith(b'\n'):
        raise ProtocolError('invalid', 'truncated frame')
    try:
        frame = json.loads(line)
    except json.JSONDecodeError:
        raise ProtocolError('invalid', 'frame is not JSON') from None
    if not isinstance(frame, dict) or not isinstance(frame.get('op'), str):
        raise ProtocolError('invalid', 'frame needs an op')
    return frame


async def write_frame(writer, frame):
    writer.write(encode(frame))
    await writer.drain()
