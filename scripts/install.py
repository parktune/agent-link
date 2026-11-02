#!/usr/bin/env python3
"""Register the agent-link skill and MCP server for Claude Code and Codex on this machine.

Run from the clone after `pip install -e .` into the repo's .venv. Changes the user's own config
only: ~/.claude/skills, ~/.codex/skills and the user-scope MCP server lists. Nothing system-wide.
Existing entries are kept; use --replace to update them. --dry-run only prints the plan.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / 'skills' / 'agent-link'
MCP = Path(sys.executable).with_name('agent-link-mcp')


def plan(replace):
    steps = []
    for tool, home in (('claude', Path.home() / '.claude'), ('codex', Path.home() / '.codex')):
        if not shutil.which(tool):
            steps.append((f'skip {tool}: CLI not found', None))
            continue
        target = home / 'skills' / 'agent-link'
        if target.exists() and not replace:
            steps.append((f'keep existing skill {target} (use --replace to update)', None))
        else:
            steps.append((f'install skill -> {target}', ('copy', target)))
        exists = subprocess.run([tool, 'mcp', 'get', 'agent-link'], capture_output=True, timeout=30).returncode == 0
        if exists and not replace:
            steps.append((f'keep existing {tool} MCP server agent-link', None))
            continue
        if exists:
            remove = [tool, 'mcp', 'remove'] + (['--scope', 'user'] if tool == 'claude' else []) + ['agent-link']
            steps.append(('run ' + ' '.join(remove), ('run', remove)))
        add = [tool, 'mcp', 'add'] + (['--scope', 'user'] if tool == 'claude' else []) + \
              ['agent-link', '--', str(MCP), '--tool', tool]
        steps.append(('run ' + ' '.join(add), ('run', add)))
    return steps


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--replace', action='store_true', help='Update an existing skill / MCP registration')
    parser.add_argument('--dry-run', action='store_true', help='Print the plan only')
    args = parser.parse_args()
    if not MCP.exists():
        raise SystemExit(f'{MCP} not found. Run: {REPO}/.venv/bin/python -m pip install -e {REPO}')
    for text, action in plan(args.replace):
        print(('[dry-run] ' if args.dry_run else '') + text, flush=True)
        if args.dry_run or not action:
            continue
        kind, value = action
        if kind == 'copy':
            if value.exists():
                shutil.rmtree(value)
            value.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(SKILL, value)
        else:
            subprocess.run(value, check=True, timeout=60)
    if not args.dry_run:
        print('Done. Restart open Claude Code / Codex sessions to load the skill and MCP server.')


if __name__ == '__main__':
    main()
