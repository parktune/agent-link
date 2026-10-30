# Security

This is a local feasibility PoC, not a network service. Do not expose its App Server
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
