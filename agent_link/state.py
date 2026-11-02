"""Owner-private state directory, configuration and structured logs."""
from dataclasses import dataclass, fields
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import time
import tomllib

ORIGINATION = ('none', 'host', 'all')
RETIRED_KEYS = ('mirror_local_metadata',)


def state_root():
    override = os.environ.get('AGENT_HUB_STATE')
    if override:
        return Path(override)
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'agent-link'


def default_socket():
    return Path(os.environ.get('AGENT_HUB_SOCKET') or state_root() / 'agent-link.sock')


def private_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError(f'{path} must not be a symlink')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def open_db(path):
    path = Path(path)
    private_directory(path.parent)
    if path.is_symlink():
        raise RuntimeError(f'{path} must not be a symlink')
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    path.chmod(0o600)
    return db


@dataclass
class Config:
    host_name: str = ''
    allow_origination: str = 'all'
    max_content_bytes: int = 65536
    max_hops: int = 3
    outbox_max_items: int = 1000
    outbox_max_bytes: int = 16 << 20
    deliver_timeout: float = 60.0
    socket_mode: int = 0o600
    socket_group: str = ''

    @classmethod
    def load(cls, root=None, **overrides):
        path = Path(root or state_root()) / 'config.toml'
        values = {}
        if path.exists():
            with path.open('rb') as handle:
                values = tomllib.load(handle)
        known = {f.name for f in fields(cls)}
        for retired in RETIRED_KEYS:  # accepted and ignored so an older config.toml still loads
            values.pop(retired, None)
        unknown = set(values) - known
        if unknown:
            raise ValueError(f'Unknown config keys in {path}: {", ".join(sorted(unknown))}')
        config = cls(**{**values, **{k: v for k, v in overrides.items() if v is not None}})
        if config.allow_origination not in ORIGINATION:
            raise ValueError(f'allow_origination must be one of {", ".join(ORIGINATION)}')
        if not config.host_name:
            config.host_name = socket.gethostname().split('.')[0]
        return config


class Log:
    """JSON Lines events. Message bodies are never passed in; callers log metadata only."""

    def __init__(self, path=None, stream=None):
        self.path = Path(path) if path else None
        self.stream = stream
        if self.path:
            private_directory(self.path.parent)

    def __call__(self, event, **values):
        line = json.dumps({'at': round(time.time(), 3), 'event': event, **values}, ensure_ascii=False)
        if self.path:
            with self.path.open('a') as handle:
                handle.write(line + '\n')
            self.path.chmod(0o600)
        if self.stream:
            print(line, file=self.stream, flush=True)


def stderr_log(path=None):
    return Log(path, sys.stderr)
