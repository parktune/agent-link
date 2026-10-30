# Development

Python 3.11+. Base probe/demo has no third-party runtime dependencies.
Install optional daemon diagnostics before running the full test suite:

```bash
python3 -m pip install -e ".[security,diagnostics]"
``` Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m codex_bridge probe
```

The deterministic tests do not call a model. `python3 -m codex_bridge demo` requires
Codex login and uses real model turns. Keep transcripts in ignored `.runtime/`.
Do not change another active conversation or publish account information in tests.

## Cloud diagnostics

`python3 -m codex_bridge cloud-inspect` reads schemas from the installed CLI without
contacting the relay. `cloud-status` reads the running daemon through its Unix WebSocket without modifying
Remote Control state. `cloud-probe` checks for an existing relay owner first (exit 2
means skipped to avoid a conflict). When safe, it contacts OpenAI using official experimental RPCs;
it temporarily enables Remote Control on a separate App Server and creates a pairing
artifact. It does not call a model. Run it only in an appropriate logged-in environment.
Tests in CI simulate RPC results and do not contact the cloud.

## GitHub

Public repository: https://github.com/Parktune/agent-link

The default branch is `main`. Clone and contribute through a branch and pull request:

```bash
git clone https://github.com/Parktune/agent-link.git
cd agent-link
git switch -c your-change
```

Maintainers with push access can publish local commits using `git push origin main`.
Do not put tokens in remote URLs. GitHub Actions runs deterministic tests without
OpenAI secrets.


## SSH security mode

Run the deterministic suite on Ubuntu (Python 3.11 and 3.13). Windows support
and its CI matrix are deferred by project scope. It covers the durable mailbox,
authentication and replay/state boundaries, subprocess framing/limits, and an
actual MCP SDK stdio session. These tests do not install Tailscale, modify SSH
configuration, contact remote hosts or call a model.

The optional Linux `python scripts/verify_security_ssh.py` starts a temporary
OpenSSH listener on loopback with temporary host/client keys and known_hosts.
It does not touch system SSH or user keys. `--models` additionally invokes two
actual Codex conversations using scoped synthetic MCP tool authorization in the
temporary test processes. It consumes model usage; the regular CI does not run it.
No actual company's server pair is assumed or auto-connected by tests.
