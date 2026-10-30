#!/usr/bin/env python3
"""Install the repository's project skill into the current Codex user's skills directory."""
import argparse
import os
from pathlib import Path
import shutil
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replace', action='store_true', help='replace an existing agent-link skill directory')
    args = parser.parse_args()

    source = Path(__file__).resolve().parents[1] / '.agents' / 'skills' / 'agent-link'
    codex_home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser()
    target = codex_home / 'skills' / 'agent-link'
    if not (source / 'SKILL.md').is_file():
        parser.error(f'skill source is missing: {source}')
    if target.exists():
        try:
            if (target / 'SKILL.md').read_bytes() == (source / 'SKILL.md').read_bytes():
                print(f'agent-link is already installed at {target}')
                return 0
        except OSError:
            pass
        if not args.replace:
            print(f'{target} already exists; inspect it or rerun with --replace to update it', file=sys.stderr)
            return 2
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    print(f'Installed $agent-link skill to {target}')
    print('Restart or refresh Codex sessions to discover the skill.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
