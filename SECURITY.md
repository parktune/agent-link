# Security boundaries

## Security mode (SSH)

This mode uses an existing, administrator-approved OpenSSH route. It creates no
public listener, VPN, cloud relay or network discovery service. Host key checking
and noninteractive public-key authentication are mandatory; unknown host keys are
not accepted automatically. Agent/port forwarding, LocalCommand and preexisting
multiplexed sessions are disabled for these exchanges. SSH config (including any
ProxyJump/ProxyCommand), keys, endpoint executable and profiles are trusted local
configuration. They must be managed under the organization's policy.

An additional 80-bit one-use enrollment code expires after 5 minutes and closes
after 5 failed claims. Codes travel only in encrypted SSH stdin, not argv or env.
The remote profile persists a code hash, then a hash of the issued 256-bit token.
The initiator stores that token in its private local DB. Public peer listings,
audit metadata and transport errors exclude tokens and remote message bodies.

The SSH account is itself an OS trust boundary. A normal shell-enabled SSH key
can run commands outside this bridge. To restrict a dedicated credential to the
mailbox, an administrator can deploy a forced command/restricted key and a
dedicated OS account; the bridge does not alter system sshd/authorized_keys or
firewall configuration. No claim of SSH-account sandboxing is made.

The bridge endpoint accepts only identity lookup, enrollment, and bounded mailbox
exchange. It cannot execute remote commands, read arbitrary files, start Codex
turns, change peer routes or approve tools. Messages are bound to authenticated
peer IDs and validated against task ownership and terminal state transitions.
SQLite transactions, stable message UUIDs, acknowledgments and retained tombstones
support retries after lost responses without re-inserting messages. This is not a
guarantee of exactly-once execution of a model's downstream actions.

MCP returns remote text as untrusted tool output in the existing conversation.
Incoming data is never injected as a trusted user turn or automatically executed.
The receiving Codex retains its own sandbox/approval policy. The shipping MCP
registration does not preapprove tools or weaken that policy. Review the data
scope before sending requests or results. SSH-only A2A does not prevent cloud
model inference from receiving data included in the model context.

The mailbox uses an owner-only directory: mode 0700 / DB 0600 on Unix; explicit
current-user SID + SYSTEM ACL on Windows. It is not encrypted at rest. Local
account compromise, administrators, backups and SQLite free pages are outside
this boundary. Apply the company's disk encryption, retention and backup policy.
A profile has a live mailbox cap; pruning removes eligible bodies but keeps
UUID/hash/route tombstones. Local audit contains action, time and ID, not body.
Bilateral revocation requires both profiles to revoke. SSH keys/sessions remain
separately administered.

This is a new implementation, not an independently audited enterprise product.
Windows command quoting, Unicode, state ACL and MCP tests do not replace a real
Windows SSH server rollout test. Use approved non-sensitive pilot data first.

## Comfort mode

The Tailscale mode is reserved but not implemented in this repository. Neither
installing the security extra nor running security commands installs, logs in,
starts or contacts Tailscale. The separate ssh-remote-tools repository has its
own implementation and threat model; it is not a dependency of security mode.

## Legacy research and local demos

The opt-in `cloud-*` commands are historical OpenAI relay diagnostics. They are
not invoked by security mode. cloud-probe temporarily enables a separate host
only after checking for an existing owner; it does not implement a relay client.
Daemon status inspection has a read-only allowlist and does not touch threads.

The original App Server demo uses read-only test threads and refuses server
requests. Read-only is not secret isolation; use clean accounts/config for
untrusted input. Do not commit runtime transcripts, credentials, Codex home files
or pairing codes. `.runtime/` is ignored. Report issues privately to the owner or
through a GitHub private security advisory, without sharing credentials.
