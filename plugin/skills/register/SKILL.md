---
name: register
description: Register this Claude Code or Codex session in agent-link so other agent sessions can message it, rename its registration, or show its registration and push status. Use when the user invokes agent-link:register (/agent-link:register, $agent-link:register) or asks to register, rename or check this session in agent-link.
---

# agent-link: register

Registers this session with the agent-link daemon on this machine (the hub on the user's PC, the spoke daemon on a server). Its ID becomes `host:tool:name`. Use the `agent-link` MCP tools `whoami` and `register`. Sending and reading messages is `/agent-link` (Codex `$agent-link`).

## 1. Check the current state

Call `whoami`. If `daemon_connected` is false, the hub (`hubctl serve`, user's PC) or spoke daemon (`agent-hub daemon`, server) is not running. Say so and stop; do not start it yourself.

## 2. Pick the name

- If the user gave a name (the text after the command), use it.
- Otherwise use the session's own name: `whoami` shows it as `session_name` (set with `/rename`). Do not ask when it is present and valid.
- If there is no `session_name`, or it is not a valid agent name (1-64 letters, digits, `_ . -`), ask the user in one short question, suggesting `/rename <name>`. Never invent or guess a name.

If the session is already registered under the chosen name, arm the watch if needed (step 4) and report it (step 5).

## 3. Register

- Codex only: find the thread for push. Run `printenv CODEX_THREAD_ID`. If it prints an ID, pass it as `thread_id` (the `/rename` name is then looked up from it). If it is empty, ask the user to name the session with `/rename <name>` and pass that name as `thread_id`. Without it, messages for this session go to the mailbox only.
- Call `register(thread_id?)` to use the session name, or `register(name, thread_id?)` when the user gave a name. Registering again under another name moves this session to that name.

## 4. Wake on mail (Claude without push)

If the `register` (or `whoami`) result has a `watch` command, run it exactly as given with Bash `run_in_background: true`. It waits until the mailbox has unread mail, prints one line and exits, and that exit wakes this session. Do not poll it or wait for it.

- Arm it once. If a watch you started in this conversation is still running, leave it; a second one exits at once with "already watched".
- When it exits with "unread message(s)", read the mailbox as in `/agent-link` section 3, which arms the watch again.

## 5. Report

Tell the user the resulting ID and mailbox folder in one or two lines. If `push.available` is false, explain once:

- Claude: messages are saved to the mailbox, and the background watch (step 4) wakes this session when one arrives, a few seconds late and only while this session is open. To receive them live instead, start Claude Code with `claude --dangerously-load-development-channels server:agent-link`.
- Codex: the thread is unknown, so messages are saved to the mailbox.
