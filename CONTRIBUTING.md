# Development

Python 3.11+, no third-party runtime dependencies. Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m agent_bridge probe
```

The deterministic tests do not call a model. `python3 -m agent_bridge demo` requires
Codex login and uses real model turns. Keep transcripts in ignored `.runtime/`.
Do not change another active conversation or publish account information in tests.

## GitHub

The repository uses `main`. GitHub repository creation and pushing are separate from
local development. After choosing a GitHub owner/repository and creating an empty repo:

```bash
git remote add origin git@github.com:YOUR_OWNER/agent-bridge.git
git push -u origin main
```

Use an HTTPS origin instead if that matches your authentication setup. Do not put
tokens in the remote URL. No GitHub repository or remote is created automatically.
The included GitHub Actions workflow runs deterministic tests without OpenAI secrets.
