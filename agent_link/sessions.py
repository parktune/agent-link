"""The name a session was given with /rename, read from Claude Code's or Codex's own session records."""
import json
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
