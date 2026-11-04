"""The Claude Code hook that wakes a mailbox session when mail arrives (user settings.json).

Where the organization keeps Claude Channels off, messages go to the session's mailbox. Claude Code runs
`agent-link wait-box --hook` in the background (asyncRewake) at SessionStart and after every turn (Stop);
it exits 2 on new mail, which wakes the session. Sessions that are not agent-link mailbox sessions end at once.
"""
import json
import os
from pathlib import Path
import shlex
import sys

EVENTS = ('SessionStart', 'Stop')
MARK = 'wait-box --hook'
TIMEOUT = 7 * 24 * 3600  # Claude Code kills a hook at its timeout without a word; the next Stop starts it again
# What the user sees when the hook wakes the session (Claude Code's default is "Stop hook feedback"), and the
# prefix the model gets before the hook's text (default: "Stop hook blocking error from command ...").
SUMMARY = 'agent-link: new message'
PREFIX = 'agent-link mailbox (not an error):'


def settings_path():
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'settings.json'


def hook_command(python=None):
    return shlex.join([python or sys.executable, '-m', 'agent_link', 'wait-box', '--hook'])


def _ours(entry):
    return any(MARK in str(hook.get('command', '')) for hook in entry.get('hooks') or [] if isinstance(hook, dict))


def _load(path):
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    settings = json.loads(text) if text.strip() else {}
    if not isinstance(settings, dict):
        raise ValueError('settings.json is not a JSON object')
    return settings


def installed(path=None):
    try:
        hooks = _load(path or settings_path()).get('hooks') or {}
    except (OSError, ValueError):
        return False
    return all(any(_ours(e) and _current(e) for e in hooks.get(event) or [] if isinstance(e, dict)) for event in EVENTS)


def _current(entry):
    """Installed by this version (an older entry lacks the notice text and shows "Stop hook feedback")."""
    return any(hook.get('rewakeSummary') == SUMMARY for hook in entry.get('hooks') or [] if isinstance(hook, dict))


def install(path=None, python=None, remove=False):
    """Add (or with `remove`, take out) the agent-link hooks; other hooks stay as they are. Returns the path.

    Raises ValueError when settings.json is not valid JSON, so a hand-edited file is never overwritten.
    """
    path = path or settings_path()
    settings = _load(path)
    hooks = settings.setdefault('hooks', {})
    for event in EVENTS:
        entries = [e for e in hooks.get(event) or [] if not (isinstance(e, dict) and _ours(e))]
        if not remove:
            entries.append({'hooks': [{'type': 'command', 'command': hook_command(python), 'asyncRewake': True,
                                       'timeout': TIMEOUT, 'rewakeSummary': SUMMARY, 'rewakeMessage': PREFIX}]})
        if entries:
            hooks[event] = entries
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop('hooks')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.agent-link.tmp')
    temporary.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + '\n')
    os.replace(temporary, path)
    return path
