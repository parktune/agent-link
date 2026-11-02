#!/usr/bin/env python3
"""Install agent-link for Claude Code and Codex on this machine: the /agent-link skill and the MCP server.

Run from the clone after `pip install -e .` into the repo's .venv. Changes the user's own config
only: the skill is copied to ~/.claude/skills and ~/.codex/skills and the MCP server `agent-link` is
added at user scope. The agent-link plugin and marketplace of 0.5-0.7 (agent-link:register,
agent-link:settings) are removed. --replace reinstalls; --dry-run only prints the plan.
"""
import argparse
import json
import os
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
    """Remove the retired agent-link plugin and its local marketplace, if a previous version installed them."""
    steps = []
    installed = {p.get('id') for p in json.loads(output(['claude', 'plugin', 'list', '--json']) or '[]')}
    if PLUGIN in installed:
        steps.append(['claude', 'plugin', 'uninstall', PLUGIN])
    marketplaces = {m.get('name') for m in json.loads(output(['claude', 'plugin', 'marketplace', 'list', '--json']) or '[]')}
    if 'agent-link' in marketplaces:
        steps.append(['claude', 'plugin', 'marketplace', 'remove', 'agent-link'])
    return steps


def codex_config():
    import tomllib
    path = Path(os.environ.get('CODEX_HOME') or HOMES['codex']) / 'config.toml'
    try:
        with path.open('rb') as handle:
            return tomllib.load(handle)
    except (OSError, ValueError):
        return {}


def codex_plan(replace):
    """Remove the retired plugin, its marketplace and the 0.2 MCP server, read from Codex's config.toml.

    `codex plugin marketplace list` fails once the marketplace manifest is gone, so it cannot tell.
    """
    config, steps = codex_config(), []
    if PLUGIN in (config.get('plugins') or {}):
        steps.append(['codex', 'plugin', 'remove', PLUGIN])
    if 'agent-link' in (config.get('marketplaces') or {}):
        steps.append(['codex', 'plugin', 'marketplace', 'remove', 'agent-link'])
    for name, server in (config.get('mcp_servers') or {}).items():
        if name != 'agent-link' and 'codex_bridge' in ' '.join(map(str, server.get('args') or [])):
            steps.append(['codex', 'mcp', 'remove', name])  # agent-link 0.2 (codex_bridge) is gone
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
        for step in plan(args.replace):  # cleanup of earlier versions: report failures, keep going
            print(prefix + 'run ' + ' '.join(step), flush=True)
            if not args.dry_run and subprocess.run(step, capture_output=True, timeout=120).returncode:
                print(f'note: {" ".join(step)} failed; remove it by hand if it is still there', flush=True)
        for step in mcp_plan(tool, args.replace):
            print(prefix + 'run ' + ' '.join(step), flush=True)
            if not args.dry_run:
                subprocess.run(step, check=True, timeout=120)
    if not args.dry_run:
        print('Done. Reopen Claude Code / Codex sessions to load /agent-link ($agent-link in Codex).')


if __name__ == '__main__':
    main()
