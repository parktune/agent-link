#!/usr/bin/env python3
"""Install the agent-link plugin (skills agent-link:send, agent-link:register) and MCP server for
Claude Code and Codex on this machine.

Run from the clone after `pip install -e .` into the repo's .venv. Changes the user's own config
only: this repo becomes a local plugin marketplace named `agent-link`, the plugin is installed from
it, and the MCP server `agent-link` is added at user scope. Standalone `agent-link` skills left by
earlier versions are removed. --replace reinstalls; --dry-run only prints the plan.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
MCP = Path(sys.executable).with_name('agent-link-mcp')
PLUGIN = 'agent-link@agent-link'


def output(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    return result.stdout if result.returncode == 0 else ''


def claude_plan(replace):
    steps = []
    marketplaces = {m.get('name') for m in json.loads(output(['claude', 'plugin', 'marketplace', 'list', '--json']) or '[]')}
    if 'agent-link' not in marketplaces:
        steps.append(['claude', 'plugin', 'marketplace', 'add', str(REPO)])
    installed = {p.get('id') for p in json.loads(output(['claude', 'plugin', 'list', '--json']) or '[]')}
    # A marketplace added from a local path loads the plugin in place, so edits need no reinstall.
    if PLUGIN not in installed:
        steps.append(['claude', 'plugin', 'install', PLUGIN])
    return steps


def codex_plan(replace):
    steps = []
    marketplaces = {line.split()[0] for line in output(['codex', 'plugin', 'marketplace', 'list']).splitlines()[1:] if line.strip()}
    if 'agent-link' not in marketplaces:
        steps.append(['codex', 'plugin', 'marketplace', 'add', str(REPO)])
    rows = [line for line in output(['codex', 'plugin', 'list']).splitlines() if line.startswith(PLUGIN + ' ')]
    installed = bool(rows) and 'not installed' not in rows[0]
    if installed and replace:
        steps.append(['codex', 'plugin', 'remove', PLUGIN])  # Codex installs a cached copy
    if not installed or replace:
        steps.append(['codex', 'plugin', 'add', PLUGIN])
    return steps


def mcp_plan(tool, replace):
    exists = subprocess.run([tool, 'mcp', 'get', 'agent-link'], capture_output=True, timeout=30).returncode == 0
    scope = ['--scope', 'user'] if tool == 'claude' else []
    steps = []
    if exists and replace:
        steps.append([tool, 'mcp', 'remove', *scope, 'agent-link'])
    if not exists or replace:
        steps.append([tool, 'mcp', 'add', *scope, 'agent-link', '--', str(MCP), '--tool', tool])
    return steps


def old_skills():
    """Standalone skills written by agent-link <= 0.4 (now provided by the plugin)."""
    for home in (Path.home() / '.claude', Path.home() / '.codex'):
        skill = home / 'skills' / 'agent-link'
        text = (skill / 'SKILL.md').read_text(errors='replace') if (skill / 'SKILL.md').exists() else ''
        if 'name: agent-link' in text:
            yield skill


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--replace', action='store_true', help='Reinstall the plugin and MCP registration')
    parser.add_argument('--dry-run', action='store_true', help='Print the plan only')
    args = parser.parse_args()
    if not MCP.exists():
        raise SystemExit(f'{MCP} not found. Run: {REPO}/.venv/bin/python -m pip install -e {REPO}')
    prefix = '[dry-run] ' if args.dry_run else ''
    for skill in old_skills():
        print(f'{prefix}remove old standalone skill {skill}', flush=True)
        if not args.dry_run:
            shutil.rmtree(skill)
    for tool, plan in (('claude', claude_plan), ('codex', codex_plan)):
        if not shutil.which(tool):
            print(f'skip {tool}: CLI not found', flush=True)
            continue
        for step in plan(args.replace) + mcp_plan(tool, args.replace):
            print(prefix + 'run ' + ' '.join(step), flush=True)
            if not args.dry_run:
                subprocess.run(step, check=True, timeout=120)
    if not args.dry_run:
        print('Done. Restart open Claude Code / Codex sessions; then use agent-link:register and agent-link:send.')


if __name__ == '__main__':
    main()
