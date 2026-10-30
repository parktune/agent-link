# Development

Python 3.11+, no third-party runtime dependencies. Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m codex_bridge probe
```

The deterministic tests do not call a model. `python3 -m codex_bridge demo` requires
Codex login and uses real model turns. Keep transcripts in ignored `.runtime/`.
Do not change another active conversation or publish account information in tests.

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
