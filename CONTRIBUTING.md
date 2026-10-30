# Development

Python 3.11+, no third-party runtime dependencies. Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m codex_bridge probe
```

The deterministic tests do not call a model. `python3 -m codex_bridge demo` requires
Codex login and uses real model turns. Keep transcripts in ignored `.runtime/`.
Do not change another active conversation or publish account information in tests.

## Cloud diagnostics

`python3 -m codex_bridge cloud-inspect` reads schemas from the installed CLI without
contacting the relay. `cloud-probe` contacts OpenAI using official experimental RPCs;
it temporarily enables Remote Control on a separate App Server and creates a pairing
artifact. It does not call a model. Run it only in an appropriate logged-in environment.
Tests in CI simulate RPC results and do not contact the cloud.

## GitHub

Public repository: https://github.com/Parktune/codex-bridge

The default branch is `main`. Clone and contribute through a branch and pull request:

```bash
git clone https://github.com/Parktune/codex-bridge.git
cd codex-bridge
git switch -c your-change
```

Maintainers with push access can publish local commits using `git push origin main`.
Do not put tokens in remote URLs. GitHub Actions runs deterministic tests without
OpenAI secrets.
