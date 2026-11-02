# Security boundaries

This is a new, independently unaudited implementation. Pilot it with non-sensitive data.

## Network

- Servers open no listening port besides sshd. The spoke daemon binds a unix socket only
  (mode 0600 by default; optionally a group, see `socket_group`).
- All connections start at the hub on the user's PC and use the system OpenSSH client with
  `BatchMode=yes`, `StrictHostKeyChecking=yes`, no agent/port forwarding, no LocalCommand.
  Unknown host keys, authentication failures and wrong remote paths stop with an explanation;
  nothing is bypassed or accepted automatically. agent-link never edits `sshd_config`,
  `authorized_keys`, known_hosts or firewalls; `add-host` prints the forced-command line instead.

## Host enrollment and tokens

- `add-host` issues a 5-minute, one-use invitation on the server (80-bit code, hash stored, closed
  after 5 wrong attempts) and consumes it immediately over SSH. The code travels only over SSH
  pipes and stays in memory; it is never printed, logged, or put in argv, environment or files.
  The manual `--code` path reads it from a hidden prompt.
- The hub generates a 256-bit peer token per host. Servers store only its SHA-256 hash; the hub
  keeps it in its 0600 SQLite database under a 0700 directory. Every endpoint session starts
  with `hello` and is rejected `unauthorized` without a valid token. `agent-link revoke` deletes
  the hashes and drops the current hub connection; `agent-link hub remove-host` deletes the hub side.
- Per-agent tokens and permissions are out of MVP scope: any process that can open the local
  daemon socket can register as a session on that host.

## SSH account

The recommended setup is a dedicated OS account whose `authorized_keys` entry for the hub key is
`restrict,command="<abs path>/agent-link endpoint"`. The endpoint passes only the allowlisted frames
(`hello`, `enroll`, `deliver`, `ack`, `exchange`, `ping`) and has no shell, file, session selection or
turn-start operation. With an ordinary shell-enabled key the allowlist does not sandbox the
account: the key itself grants a shell.

## Messages

- Message content is untrusted input. Channel instructions and the skill tell sessions that it
  grants no permissions and must not override the local user; the receiving session keeps its own
  sandbox/approval policy. Labels are not a security boundary.
- Loop limits: replies to a `reply` are rejected (`reply_to_reply`) and `hops` above the limit
  (default 3) are rejected by both the sending spoke and the hub. Server sessions may start
  requests to any host by default (`allow_origination = "all"`); set `"none"` in a server's
  config.toml to let its sessions only reply to requests they received, or `"host"` for same-host only.
- The hub tells each server which agents exist on the other hosts (ID, status, delivery mode; no
  folders or session IDs) so server sessions can address them by name.
- Codex push passes the message to `codex queue --message=...` as one argument; for that moment it
  is visible to other local accounts through the process list. Do not use push on hosts shared with
  untrusted users.
- Mailbox files (`<repo>/hub`, `<repo>/spoke`) are 0600 in 0700 folders and git-ignored. They are
  not encrypted at rest.
- Hub and daemon logs record metadata (id, from, to, size, time, status), never bodies. Same-host
  deliveries report metadata only to the hub (`mirror_local_metadata`).
- Do not put secrets in message bodies: they reach model providers through the receiving session.

## Reporting

Report issues privately to the owner or through a GitHub private security advisory.
