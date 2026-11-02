"""Mailbox folders: <repo>/hub (local PC) or <repo>/spoke (server), one Markdown file per message.

Used when a message cannot be pushed into an open session: the session is closed, push is not
available for it, or the push failed. Layout: <root>/<tool>-<name>/received/[read/]<file>.md, plus
<root>/<tool>-<name>/watch.pid while a session waits for mail with `agent-link wait-box`, and
<root>/<tool>-<name>/watch.off when the user turned that watch off.
"""
import os
from pathlib import Path
import re
import signal
import time

from .protocol import parse_target

REPO_ROOT = Path(__file__).resolve().parents[1]


def default_box_root(role):
    return Path(os.environ.get('AGENT_HUB_BOX') or REPO_ROOT / role)


def _private(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


class Box:
    def __init__(self, root):
        self.root = Path(root)

    def folder(self, agent):
        _, tool, name = parse_target(agent)
        return self.root / f'{tool}-{name}' / 'received'

    def put(self, message):
        folder = _private(self.folder(message['to']))
        stamp = re.sub(r'[^0-9]', '', message['created_at'])[:14]
        sender = message['from'].replace(':', '-')
        path = folder / f'{stamp[:8]}-{stamp[8:]}_{sender}_{message["id"][-8:]}.md'
        header = {'id': message['id'], 'from': message['from'], 'to': message['to'],
                  'type': message['type'], 'in_reply_to': message['in_reply_to'] or '', 'hops': message['hops'],
                  'created_at': message['created_at']}
        text = '---\n' + ''.join(f'{k}: {v}\n' for k, v in header.items()) + '---\n\n' + message['content'] + '\n'
        temporary = path.with_suffix('.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(text)
        os.replace(temporary, path)
        return path

    def unread(self, agent, limit=20):
        """Return unread messages oldest first and move them to received/read/."""
        folder = self.folder(agent)
        if not folder.is_dir():
            return []
        messages = []
        for path in sorted(folder.glob('*.md'))[:limit]:
            messages.append({**parse(path.read_text()), 'file': str(path)})
            target = _private(folder / 'read') / path.name
            os.replace(path, target)
            messages[-1]['file'] = str(target)
        return messages

    def count(self, agent):
        folder = self.folder(agent)
        return len(list(folder.glob('*.md'))) if folder.is_dir() else 0


class Stopped(Exception):
    """The waiter was asked to stop (SIGTERM from `stop_waiter`)."""


def watch_files(folder):
    """(watch.pid, watch.off) next to a received/ folder."""
    parent = Path(folder).parent
    return parent / 'watch.pid', parent / 'watch.off'


def waiter_alive(folder):
    """PID of another live `wait-box` watching this received/ folder, or None."""
    lock, _ = watch_files(folder)
    try:
        pid = int(lock.read_text())
        os.kill(pid, 0)
        cmdline = Path(f'/proc/{pid}/cmdline')
        if cmdline.exists() and b'wait-box' not in cmdline.read_bytes():
            return None  # the PID was reused by something else
    except (OSError, ValueError):
        return None
    return pid if pid != os.getpid() else None


def stop_waiter(folder):
    """Stop the live waiter of this folder, if any, and return its PID."""
    pid = waiter_alive(folder)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return None
    return pid


def set_watch_enabled(folder, enabled):
    """Remember the user's choice; turning the watch off also stops a running waiter."""
    _, off = watch_files(folder)
    if enabled:
        off.unlink(missing_ok=True)
        return None
    _private(off.parent)
    off.touch()
    return stop_waiter(folder)


def watch_enabled(folder):
    return not watch_files(folder)[1].exists()


def wait_for_mail(folder, interval=2.0, sleep=time.sleep):
    """Block until the received/ folder holds an unread message and return (count, None).

    Return (0, pid) at once when another live waiter already watches this folder, so a session
    that arms the watch twice keeps one. The folder may not exist yet (no mail so far).
    Raise Stopped on SIGTERM, which `stop_waiter` sends.
    """
    folder = Path(folder)
    lock, _ = watch_files(folder)
    other = waiter_alive(folder)
    if other:
        return 0, other

    def stop(*_):
        raise Stopped()

    previous = signal.signal(signal.SIGTERM, stop)
    _private(folder.parent)
    lock.write_text(f'{os.getpid()}\n')
    try:
        while True:
            count = len(list(folder.glob('*.md'))) if folder.is_dir() else 0
            if count:
                return count, None
            sleep(interval)
    finally:
        signal.signal(signal.SIGTERM, previous)
        try:
            if int(lock.read_text()) == os.getpid():
                lock.unlink()
        except (OSError, ValueError):
            pass


def parse(text):
    _, header, body = text.split('---\n', 2)
    fields = dict(line.split(': ', 1) for line in header.splitlines() if ': ' in line)
    return {**fields, 'content': body.removeprefix('\n').removesuffix('\n')}
