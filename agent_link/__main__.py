"""`python -m agent_link ...` is the `agent-link` command; `python -m agent_link mcp ...` is the session MCP server."""
import sys

from .cli import agent_link
from .mcp_adapter import main as mcp

if __name__ == '__main__':
    if sys.argv[1:2] == ['mcp']:
        sys.exit(mcp(sys.argv[2:]))
    sys.exit(agent_link(sys.argv[1:]))
