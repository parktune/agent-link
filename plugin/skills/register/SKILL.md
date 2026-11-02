---
name: register
description: Register this Claude Code or Codex session in agent-link (its mailbox) under its session name, and for a Claude session without push show the command that reopens it with the agent-link channel flag by resuming the same session name. Use when the user invokes agent-link:register (/agent-link:register, $agent-link:register) or asks to register this session in agent-link. Withdrawing, renaming and the mailbox watch are agent-link:settings.
---

# agent-link: register

Registers this session with the agent-link daemon on this machine (the hub on the user's PC, the spoke daemon on a server), so messages for it land in its mailbox. Its ID becomes `host:tool:name`. Then, for a Claude session started without the channel flag, it shows how to reopen the same session with live push. Use the `agent-link` MCP tools `whoami` and `register`. Other settings are `agent-link:settings`; sending and reading messages is `/agent-link` (Codex `$agent-link`).

## 1. Check the daemon

Call `whoami`. If `daemon_connected` is false, the hub (`agent-link hub serve`, user's PC) or spoke daemon (`agent-link daemon`, server) is not running. Say so and stop; do not start it yourself.

## 2. Pick the name

- If the user gave a name (the text after the command), use it.
- Otherwise use the session's own name: `whoami` shows it as `session_name` (set with `/rename`). Do not ask when it is present and valid.
- If there is no `session_name`, or it is not a valid agent name (1-64 letters, digits, `_ . -`), ask the user in one short question, suggesting `/rename <name>`. Never invent or guess a name.

If the session is already registered under that name, skip to step 4.

## 3. Register

- Codex only: find the thread for push. Run `printenv CODEX_THREAD_ID`. If it prints an ID, pass it as `thread_id` (the `/rename` name is then looked up from it). If it is empty, ask the user to name the session with `/rename <name>` and pass that name as `thread_id`. Without it, messages for this session go to the mailbox only.
- Call `register(thread_id?)` to use the session name, or `register(name, thread_id?)` when the user gave a name. Registering again under another name moves this session to that name.

## 4. Until the restart: arm the mailbox watch (Claude without push)

If the result has `watch` with `enabled` true and `running_pid` null, run `watch.command` exactly as given with Bash `run_in_background: true`. It exits when mail arrives, which wakes this session; then read the mailbox as in `/agent-link` section 3. Do not poll it. "already watched" or "stopped" needs nothing.

## 5. Report

Tell the user the ID and mailbox folder in one or two lines. Then:

- **Claude, `push.available` true:** messages arrive live in this session. Nothing else to do.
- **Claude, `push.available` false because the organization has not enabled Channels** (`push.reason` says so and there is no `restart`): messages go to the mailbox and the watch from step 4 wakes this session. Reopening with the flag would not help: Claude Code drops channel events until an administrator sets `channelsEnabled: true` in the organization's managed settings. Say that in one or two lines; show no restart command.
- **Claude, `push.available` false:** messages go to the mailbox; the watch from step 4 wakes this session meanwhile, a few seconds late. To receive them live, show `restart.command` from the result exactly, in a code block, with these steps:
  1. Leave this session (`/exit` or Ctrl+C twice). A copy would start if it were still open.
  2. Run the command in a terminal. It goes to this session's folder, adds `--dangerously-load-development-channels server:agent-link`, keeps the other flags this session was started with, and resumes the session by its name (`restart.resume`).
  3. The reopened session keeps its registration; `/agent-link:settings` shows `push.available` true.

  If there is no `session_name`, `restart.command` already resumes by session ID. Otherwise add `restart.by_id` as the fallback for when the name matches more than one session. Do not run either command yourself.
- **Codex:** with a thread (`push.available` true) messages are queued into this session (`codex queue`); without one they go to the mailbox.
