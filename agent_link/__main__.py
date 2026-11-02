"""`python -m agent_link hubctl|agent-hub|mcp ...` (same as the installed console scripts)."""
import sys

from .cli import agent_hub, hubctl
from .mcp_adapter import main as mcp

COMMANDS = {'hubctl': hubctl, 'agent-hub': agent_hub, 'mcp': mcp}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit('usage: python -m agent_link {hubctl|agent-hub|mcp} ...')
    sys.exit(COMMANDS[sys.argv[1]](sys.argv[2:]))
