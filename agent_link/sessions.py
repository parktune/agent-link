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


class TitleTracker:
    """The latest /rename name of one session, reading only what its record gained since the last call.

    Cheap enough to call every few seconds: a Claude transcript grows with every turn, so it is read once
    and then only from where the last call stopped.
    """

    def __init__(self, tool, session_id, config_dir=None, codex_home=None):
        self.tool, self.session_id = tool, session_id or ''
        self.config_dir, self.codex_home = config_dir, codex_home
        self.path, self.offset, self.title = None, 0, None

    def _find(self):
        if self.tool == 'claude':
            if not SESSION_ID.fullmatch(self.session_id):
                return None
            root = Path(self.config_dir or os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'projects'
            return next(iter(root.glob(f'*/{self.session_id}.jsonl')), None)
        if not self.session_id:
            return None
        return Path(self.codex_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'session_index.jsonl'

    def _title(self, entry):
        if self.tool == 'claude':
            return entry.get('customTitle') if entry.get('type') == 'custom-title' else None
        return entry.get('thread_name') if entry.get('id') == self.session_id else None

    def __call__(self):
        self.path = self.path or self._find()
        if self.path is None:
            return None
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset = 0  # rewritten
            with open(self.path, 'rb') as handle:
                handle.seek(self.offset)
                data = handle.read(max(0, size - self.offset))
        except OSError:
            self.path = None
            return self.title
        complete = data[:data.rfind(b'\n') + 1]  # a line still being written is read next time
        self.offset += len(complete)
        marker = b'"custom-title"' if self.tool == 'claude' else self.session_id.encode()
        for line in complete.splitlines():
            if marker in line:
                try:
                    title = self._title(json.loads(line))
                except (ValueError, AttributeError):
                    continue
                if title:
                    self.title = title
        return self.title


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


def _claude_continued_in(data):
    """Session IDs a transcript says it was continued in (`continued-in` entries), oldest first.

    Claude Code writes one each time it moves the session to a new ID, e.g. when it is sent to the background.
    """
    ids, pos = [], 0
    while (at := data.find(b'"continued-in"', pos)) != -1:
        start, end = data.rfind(b'\n', 0, at) + 1, data.find(b'\n', at)
        end = len(data) if end == -1 else end
        pos = end
        try:
            entry = json.loads(data[start:end])
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get('type') == 'continued-in' and entry.get('continuedInSessionId'):
            ids.append(entry['continuedInSessionId'])
    return ids


def _claude_transcript_path(root, session_id):
    return next(iter(root.glob(f'*/{session_id}.jsonl')), None) if SESSION_ID.fullmatch(session_id) else None


def _claude_latest_continuation(root, path):
    """Transcript of the newest session that `path` was continued in (following the chain), or `path` itself.

    A continuation that never got a transcript (opened and closed without a turn) is skipped.
    """
    seen = {path.stem}
    while True:
        data = _transcript(path, [b'"continued-in"'])
        if data is None:
            return path
        try:
            targets = _claude_continued_in(data)
        finally:
            data.close()
        for target in reversed(targets):
            following = None if target in seen else _claude_transcript_path(root, target)
            if following:
                seen.add(target)
                path = following
                break
        else:
            return path


def _claude_entry(path):
    data = _transcript(path, [])
    if data is None:
        return None
    try:
        match = CWD.search(data)
        cwd = json.loads(b'"' + match.group(1) + b'"') if match else ''
    finally:
        data.close()
    return {'id': path.stem, 'cwd': cwd, 'mtime': path.stat().st_mtime}


def claude_background_sessions():
    """Session IDs Claude Code is running in the background now (`claude agents --json`); empty if unknown."""
    import subprocess
    try:
        out = subprocess.run(['claude', 'agents', '--json'], capture_output=True, text=True, timeout=15).stdout
        agents = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return set()
    return {a.get('sessionId') for a in agents if isinstance(a, dict) and a.get('kind') == 'background'} - {None}


def find_claude_sessions(name, config_dir=None):
    """Claude Code sessions to resume for `name`, newest first: [{'id', 'cwd', 'mtime'[, 'continued_from']}].

    `name` is the /rename title (the latest one counts) or a session ID. The working folder comes from
    the transcript, because `claude --resume` looks for sessions of the folder it starts in.
    A session Claude Code continued under a new ID (it does so when the session goes to the background)
    resolves to its newest continuation, which then carries 'continued_from'. Resuming the old ID
    instead would reopen the conversation as it was before, and backgrounding it again makes one more copy.
    """
    root = Path(config_dir or os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'projects'
    # A session ID first (names can look like IDs, e.g. `agent-link-test`), then the /rename title.
    paths = list(root.glob(f'*/{name}.jsonl')) if SESSION_ID.fullmatch(name) else []
    needles = []
    if not paths:
        paths = list(root.glob('*/*.jsonl'))
        needles = [b'"custom-title"', json.dumps(name, ensure_ascii=False)[1:-1].encode()]
    found = {}
    for path in paths:
        data = _transcript(path, needles)
        if data is None:
            continue
        data.close()
        if needles and claude_session_name(path.stem, config_dir) != name:
            continue
        latest = _claude_latest_continuation(root, path)
        entry = _claude_entry(latest)
        if entry is None:
            continue
        if latest != path:
            entry['continued_from'] = path.stem
        if entry['id'] not in found or 'continued_from' not in entry:  # the session's own match wins
            found[entry['id']] = entry
    return sorted(found.values(), key=lambda s: s['mtime'], reverse=True)


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
