"""The name a session was given with /rename, read from Claude Code's or Codex's own session records."""
import json
import mmap
import os
from pathlib import Path
import re

SESSION_ID = re.compile(r'[A-Za-z0-9-]{8,64}\Z')


def _entries(path, marker):
    try:
        with open(path, errors='replace') as handle:
            for line in handle:
                if marker in line:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
    except OSError:
        return


def claude_session_name(session_id, config_dir=None):
    """Latest `/rename` title of a Claude Code session (`custom-title` entries in its transcript)."""
    if not session_id or not SESSION_ID.fullmatch(session_id):
        return None
    root = Path(config_dir or os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'projects'
    for path in root.glob(f'*/{session_id}.jsonl'):
        titles = [e.get('customTitle') for e in _entries(path, '"custom-title"') if e.get('type') == 'custom-title']
        if titles and titles[-1]:
            return titles[-1]
    return None


def codex_session_name(thread_id, codex_home=None):
    """Latest `/rename` name of a Codex thread (`session_index.jsonl`)."""
    if not thread_id:
        return None
    path = Path(codex_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'session_index.jsonl'
    names = [e.get('thread_name') for e in _entries(path, thread_id) if e.get('id') == thread_id]
    return names[-1] if names and names[-1] else None


CWD = re.compile(rb'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _transcript(path, needles):
    """mmap of a transcript that contains every needle (bytes), else None. Skips the rest cheaply."""
    try:
        with open(path, 'rb') as handle:
            data = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
    except (OSError, ValueError):  # unreadable or empty
        return None
    if all(data.find(needle) != -1 for needle in needles):
        return data
    data.close()
    return None


def find_claude_sessions(name, config_dir=None):
    """Claude Code sessions to resume for `name`, newest first: [{'id', 'cwd', 'mtime'}].

    `name` is the /rename title (the latest one counts) or a session ID. The working folder comes from
    the transcript, because `claude --resume` looks for sessions of the folder it starts in.
    """
    root = Path(config_dir or os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'projects'
    # A session ID first (names can look like IDs, e.g. `agent-link-test`), then the /rename title.
    paths = list(root.glob(f'*/{name}.jsonl')) if SESSION_ID.fullmatch(name) else []
    needles = []
    if not paths:
        paths = list(root.glob('*/*.jsonl'))
        needles = [b'"custom-title"', json.dumps(name, ensure_ascii=False)[1:-1].encode()]
    found = []
    for path in paths:
        data = _transcript(path, needles)
        if data is None:
            continue
        try:
            if needles and claude_session_name(path.stem, config_dir) != name:
                continue
            match = CWD.search(data)
            cwd = json.loads(b'"' + match.group(1) + b'"') if match else ''
        finally:
            data.close()
        found.append({'id': path.stem, 'cwd': cwd, 'mtime': path.stat().st_mtime})
    return sorted(found, key=lambda s: s['mtime'], reverse=True)


def _codex_home(codex_home=None):
    return Path(codex_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex')


def _codex_meta(path):
    """(thread id, cwd) from the first line of a Codex rollout file (`session_meta`)."""
    try:
        with open(path, errors='replace') as handle:
            payload = json.loads(handle.readline()).get('payload') or {}
    except (OSError, ValueError, AttributeError):
        return None, ''
    return payload.get('id') or payload.get('session_id'), payload.get('cwd') or ''


def _codex_rollouts(home, thread='*'):
    paths = (home / 'sessions').glob(f'*/*/*/rollout-*-{thread}.jsonl')
    return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)


def find_codex_sessions(name, codex_home=None):
    """Codex threads to resume for `name` (its /rename name or thread ID), newest first: [{'id', 'cwd', 'mtime'}]."""
    home = _codex_home(codex_home)
    ids = [name] if SESSION_ID.fullmatch(name) and _codex_rollouts(home, name) else []
    if not ids:
        latest = {}
        for entry in _entries(home / 'session_index.jsonl', '"thread_name"'):
            if entry.get('id'):
                latest[entry['id']] = entry.get('thread_name')
        ids = [thread for thread, title in latest.items() if title == name]
    found = []
    for thread in ids:
        paths = _codex_rollouts(home, thread)
        if paths:
            found.append({'id': thread, 'cwd': _codex_meta(paths[0])[1], 'mtime': paths[0].stat().st_mtime})
    return sorted(found, key=lambda s: s['mtime'], reverse=True)


def latest_codex_session(cwd, codex_home=None, limit=500):
    """The newest Codex thread that ran in `cwd` (what `codex resume --last` would pick), or None."""
    for path in _codex_rollouts(_codex_home(codex_home))[:limit]:
        thread, where = _codex_meta(path)
        if thread and where == str(cwd):
            return {'id': thread, 'cwd': where, 'mtime': path.stat().st_mtime}
    return None
