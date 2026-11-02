#!/bin/sh
# One-time setup on each machine (local PC and every server): venv, package, skill + MCP, commands.
# Re-run after `git pull` to update. Changes only this repo and your own user config.
set -eu
# Fail when install.py fails even though its output is piped to sed (dash 0.5.11+, bash, zsh).
(set -o pipefail) 2>/dev/null && set -o pipefail
cd "$(dirname "$0")"
REPO=$(pwd)

ok_python() { "$1" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; }

if [ -x .venv/bin/python ] && ok_python .venv/bin/python; then
    echo "[1/4] using existing .venv ($(.venv/bin/python --version))"
else
    rm -rf .venv
    PY=
    for candidate in python3.13 python3.12 python3.11 python3 /usr/bin/python3; do
        if command -v "$candidate" >/dev/null 2>&1 && ok_python "$(command -v "$candidate")"; then
            PY=$(command -v "$candidate")
            break
        fi
    done
    if [ -n "$PY" ]; then
        "$PY" -m venv .venv
    elif command -v uv >/dev/null 2>&1; then
        echo "No Python 3.11+ found; uv will download Python 3.12 for this venv."
        uv venv --quiet --python 3.12 .venv
        uv pip install --quiet --python .venv/bin/python pip
    else
        echo "Python 3.11 or newer is required (found none). Install one, or install uv, then re-run." >&2
        exit 1
    fi
    echo "[1/4] created .venv ($(.venv/bin/python --version))"
fi

.venv/bin/python -m pip install --quiet --disable-pip-version-check -e .
echo "[2/4] installed agent-link $(.venv/bin/python -c 'import agent_link; print(agent_link.__version__)')"

.venv/bin/python scripts/install.py --replace | sed 's/^/      /'
echo "[3/4] installed /agent-link, the agent-link:settings plugin and the MCP server"

mkdir -p "$HOME/.local/bin"
for command in hubctl agent-hub; do
    ln -sf "$REPO/.venv/bin/$command" "$HOME/.local/bin/$command"
done
echo "[4/4] linked hubctl and agent-hub into ~/.local/bin"
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "      note: add ~/.local/bin to PATH (e.g. in ~/.bashrc) to run them by name" ;;
esac

cat <<'EOF'

Next:
  server   : nothing else. The hub starts this server's daemon when it connects.
  local PC : hubctl discover   (pick servers from ~/.ssh/config)  or  hubctl add-host <ssh-alias>
Then open Claude with  claude --dangerously-load-development-channels server:agent-link
and use /agent-link:settings to register the session, /agent-link to message others
(Codex: $agent-link:settings, $agent-link).
EOF
