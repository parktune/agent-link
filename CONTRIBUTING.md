# Development

Python 3.11+. Install into the repo venv and run the tests (no model calls):

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/verify_ssh_loopback.py   # optional: real OpenSSH loopback, needs sshd and /run/sshd
```

Layout:

| Module | Role |
|---|---|
| `agent_link/protocol.py` | IDs, message model, JSON Lines frames, error codes |
| `agent_link/spoke.py` | Spoke daemon: registry, same-host routing, outbox, hub link session |
| `agent_link/hub.py` | Hub: global routing, one SSH stream per host, the agent directory for servers |
| `agent_link/endpoint.py` | SSH forced-command relay with the frame allowlist |
| `agent_link/box.py` | Mailbox folders |
| `agent_link/mcp_adapter.py`, `inject.py`, `client.py` | Session adapter (MCP server) and push into Claude/Codex |
| `agent_link/cli.py` | `agent-link hub`, `agent-link` |
| `skills/agent-link/SKILL.md` | The `/agent-link` (`$agent-link`) skill: send, read the mailbox, this session's settings |

Record significant decisions as ADRs in `docs/adr/`. Keep the manual scenarios in
`docs/manual-scenarios.md` in step with the tests. Never commit `hub/`, `spoke/`, `.runtime/`,
tokens, invitation codes or session transcripts.
