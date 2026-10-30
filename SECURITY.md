# Security

This is a feasibility PoC with an opt-in outbound OpenAI Remote Control probe.
It does not implement an authenticated bridge peer service. Do not expose its App Server
connection to untrusted clients. It has no peer authentication, replay protection,
remote request authorization, or file access policy yet.

The demo uses fresh read-only test threads and refuses server-initiated requests.
Read-only still permits reads allowed by the Codex environment; it does not isolate
secrets or disable every configured capability. Use a dedicated account/container
and clean Codex configuration when testing untrusted content. Do not submit remote
commands to a trusted user thread as a shortcut for authorization.

No pairing codes, account tokens, Codex home files, raw stderr, or runtime transcripts
should be committed. `.runtime/` is ignored. Report potential issues through a private
GitHub security advisory once a repository is published; until then contact the project
owner privately without sharing credentials.


`cloud-probe` temporarily exposes a separate App Server through Codex Remote Control
using official RPCs and existing Codex authentication. It does not print pairing
codes, credentials, or raw upstream errors. It disables its temporary Remote Control
state on exit and terminates its subprocess; enrollment records and unexpired pairing
artifacts may remain in Codex/cloud storage. It does not revoke clients or remove
credentials. A host-side probe passing is not proof of safe A2A authorization.


Daemon diagnostics use a local Unix WebSocket with a strict method allowlist:
initialization and `remoteControl/status/read`. This helper cannot start turns,
list conversations, grant approvals, change Remote Control, or stop the daemon.
It compares installation IDs only in memory. Before starting a temporary cloud
host, diagnostics check for an existing owner using the same installation and skip
if one is active; an unreadable daemon fails closed. This is a snapshot check,
not an atomic cloud lease or a guarantee against another process starting later.
