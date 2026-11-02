---
name: settings
description: Show or change this Claude Code or Codex session's agent-link settings - register it again under another name, withdraw the registration, and turn the mailbox watch on or off (Claude sessions without push). Use when the user invokes agent-link:settings (/agent-link:settings, $agent-link:settings) or asks to unregister, rename or check this session in agent-link, or to start or stop watching its mailbox. First-time registration with the restart command is agent-link:register.
---

# agent-link: settings

Shows and changes this session's agent-link settings with the `agent-link` MCP tools `whoami`, `register`, `unregister` and `set_watch`. The session's ID is `host:tool:name`. Sending and reading messages is `/agent-link` (Codex `$agent-link`).

The text after the command picks the action:

| Text | Action |
|---|---|
| (none), `status`, "상태" | Show (section 2) |
| `register [name]`, "등록 [이름]", a bare name | Register (section 3) |
| `unregister`, "철회", "등록 해제" | Withdraw (section 4) |
| `watch on`, "감시 시작" | Watch on (section 5) |
| `watch off`, "감시 종료" | Watch off (section 5) |

If the text matches none of these, show the settings and ask which one to change.

## 1. Check the daemon

Call `whoami`. If `daemon_connected` is false, the hub (`hubctl serve`, user's PC) or spoke daemon (`agent-hub daemon`, server) is not running. Say so and stop; do not start it yourself.

## 2. Show

Report in a few lines: registered or not, the ID and mailbox folder, push (`push.available`, or its `reason`), and for a Claude session without push the watch: `watch.enabled` and whether it runs (`watch.running_pid`). End with the actions from the table above that apply.

## 3. Register

Pick the name:

- If the user gave a name, use it.
- Otherwise use the session's own name: `whoami` shows it as `session_name` (set with `/rename`). Do not ask when it is present and valid.
- If there is no `session_name`, or it is not a valid agent name (1-64 letters, digits, `_ . -`), ask the user in one short question, suggesting `/rename <name>`. Never invent or guess a name.

If the session is already registered under that name, skip to "Then" below.

- Codex only: find the thread for push. Run `printenv CODEX_THREAD_ID`. If it prints an ID, pass it as `thread_id` (the `/rename` name is then looked up from it). If it is empty, ask the user to name the session with `/rename <name>` and pass that name as `thread_id`. Without it, messages for this session go to the mailbox only.
- Call `register(thread_id?)` to use the session name, or `register(name, thread_id?)` when the user gave a name. Registering again under another name moves this session to that name.

Then arm the watch if it applies (section 6) and tell the user the ID and mailbox folder in one or two lines. If `push.available` is false, explain once:

- Claude: messages are saved to the mailbox; while the watch is on, it wakes this session when one arrives, a few seconds late and only while this session is open. To receive them live instead, the user leaves this session and runs `restart.command` from the result (shown exactly, in a code block; it resumes this session by name with the channel flag, `restart.by_id` by session ID). `agent-link:register` walks through this.
- Codex: the thread is unknown, so messages are saved to the mailbox.

## 4. Withdraw

If the session is not registered, say so and stop. Otherwise call `unregister`. It stops the watch, and other agents can no longer message this session (sending to it fails with `not_found`); the mailbox folder and its messages stay. Report the withdrawn ID (`unregistered`) in one line. Registering again later is section 3.

## 5. Watch on or off

The watch is only for a Claude session without push. If `whoami` has no `watch` (not registered, Codex, or push available), say why in one line and stop.

- On: call `set_watch(enabled=true)`, then arm it (section 6).
- Off: call `set_watch(enabled=false)`. It is remembered for this agent name, so `/agent-link` does not arm it again, and it stops a running watch (`stopped_pid`). That watch then exits with "stopped", which needs nothing.

Report the new state in one line.

## 6. Arm the watch

When `watch.enabled` is true and `watch.running_pid` is null, run `watch.command` exactly as given with Bash `run_in_background: true`. It waits until the mailbox has unread mail, prints one line and exits, and that exit wakes this session. Do not poll it or wait for it.

- When it exits with "unread message(s)", read the mailbox as in `/agent-link` section 3, which arms the watch again.
- "already watched" or "stopped" needs nothing.
