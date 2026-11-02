#!/usr/bin/env python3
"""Install agent-link for Claude Code and Codex on this machine: the /agent-link skill, the
agent-link plugin (agent-link:register, agent-link:settings) and the MCP server.

Run from the clone after `pip install -e .` into the repo's .venv. Changes the user's own config
only: the standalone skill is copied to ~/.claude/skills and ~/.codex/skills, this repo becomes a
local plugin marketplace named `agent-link` and the plugin is installed from it, and the MCP server
`agent-link` is added at user scope. --replace reinstalls; --dry-run only prints the plan.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / 'skills' / 'agent-link'
MCP = Path(sys.executable).with_name('agent-link-mcp')
PLUGIN = 'agent-link@agent-link'
HOMES = {'claude': Path.home() / '.claude', 'codex': Path.home() / '.codex'}


def output(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        print(f'note: {" ".join(argv)} timed out; treated as empty', flush=True)
        return ''
    return result.stdout if result.returncode == 0 else ''


def skill_plan(tool):
    """Copy the /agent-link skill; refresh our own copy on every run, never overwrite someone else's."""
    target = HOMES[tool] / 'skills' / 'agent-link'
    current = (target / 'SKILL.md').read_text(errors='replace') if (target / 'SKILL.md').exists() else None
    if current is not None and 'name: agent-link' not in current:
        return [('note', f'keep {target}: it is not the agent-link skill')]
    return [('copy', target)]


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
    # Without -m, `codex plugin list` also fetches remote marketplaces and can hang.
    rows = [line for line in output(['codex', 'plugin', 'list', '-m', 'agent-link']).splitlines()
            if line.startswith(PLUGIN + ' ')]
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


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--replace', action='store_true', help='Reinstall the plugin and MCP registration')
    parser.add_argument('--dry-run', action='store_true', help='Print the plan only')
    args = parser.parse_args()
    if not MCP.exists():
        raise SystemExit(f'{MCP} not found. Run: {REPO}/.venv/bin/python -m pip install -e {REPO}')
    prefix = '[dry-run] ' if args.dry_run else ''
    for tool, plan in (('claude', claude_plan), ('codex', codex_plan)):
        if not shutil.which(tool):
            print(f'skip {tool}: CLI not found', flush=True)
            continue
        for kind, target in skill_plan(tool):
            if kind == 'note':
                print(target, flush=True)
                continue
            print(f'{prefix}install /agent-link skill -> {target}', flush=True)
            if not args.dry_run:
                shutil.rmtree(target, ignore_errors=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(SKILL, target)
        for step in plan(args.replace) + mcp_plan(tool, args.replace):
            print(prefix + 'run ' + ' '.join(step), flush=True)
            if not args.dry_run:
                subprocess.run(step, check=True, timeout=120)
    if not args.dry_run:
        print('Done. Restart open Claude Code / Codex sessions; then use /agent-link:register and /agent-link '
              '(Codex: $agent-link:register, $agent-link).')


if __name__ == '__main__':
    main()
