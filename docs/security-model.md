# Security model

This document states what `obsidian-hardened-mcp` defends against, what it
does *not* defend against, and the operational assumptions that make the
defences valid. Read it before deploying.

## Are you the right kind of user?

In one paragraph: this is an **MCP server speaking stdio over a local
subprocess** that lets a single LLM client (Claude Code, Claude Desktop,
similar) read and write files inside one Obsidian vault on the same
machine. It is built for one human running one client against one vault
they own. It is not a multi-tenant service, it does not bind a network
port, it has no authentication of its own, and it does not defend
against malicious code already running with your user privileges. If
you were hoping to run this as a shared service, expose it over a
network, or sandbox an LLM that you don't trust to read your
filesystem, this is the wrong tool — see "Out of scope" below.

## Threat model — what we defend against

The server is designed for a **single-user, locally-trusted** context: one
human, running one MCP client (Claude Code, Claude Desktop, etc.) on their
own machine, talking to one Obsidian vault on their own filesystem.
Inside that context we defend against the following classes of bug or
attack:

### Path tampering by tool input

Any string a tool receives as a path is funnelled through
`domain.vault_path.VaultPath.from_user`, which rejects:

- Absolute paths (`/etc/passwd`, `/Users/...`, etc.)
- Path traversal (`..`, mid-path `..`)
- Symlink escape — components that resolve outside the vault root
- Forbidden zones — `.obsidian/`, `.git/`, `.trash/`, `.ohmcp-trash/`,
  the project config file
- Length attacks — > 4096 chars total, > 32 segments, > 255-byte segments
- Null byte injection
- Unicode NFD / NFC confusion (paths are normalised to NFC)

A property-based test (`tests/security/test_vault_path.py`) sweeps random
inputs and asserts no path ever escapes the vault root. The module is held
to **100 % line and branch coverage**.

### Unsafe YAML constructs in frontmatter

Both READ and WRITE paths refuse YAML tags outside a strict whitelist of
YAML 1.2 default types (`str`, `int`, `float`, `bool`, `null`, `seq`,
`map`, `timestamp`, `binary`, `omap`, `set`).

- On read: `frontmatter.parser._reject_custom_tags` walks the parsed
  structure and refuses any input carrying e.g. `!!python/object/apply`,
  `!Custom`, or any non-default tag — even if `ruamel.yaml` would have
  parsed it without executing the tag.
- On write: `tools.frontmatter._ensure_safe_value` whitelists the Python
  types allowed in a frontmatter value (None, bool, int, float, str,
  list, dict with string keys; capped depth and size). `bytes`, `Path`,
  `set`, `tuple`, `datetime` objects, and arbitrary classes are rejected
  *before* the file is touched.

This closes the round-trip loop: an attacker cannot use the server to
exfiltrate an unsafe construct that a downstream YAML reader (e.g.
PyYAML in unsafe mode) would later execute.

### Torn writes / partial files

Every write goes through `fs.writer.atomic_write_text`:

1. Open a tmp file in the **same directory** as the target with `O_EXCL`
   and a 32-bit random suffix (`secrets.token_hex(4)`).
2. Write content, `flush`, `os.fsync`.
3. `os.replace` (atomic POSIX rename — only atomic when src/dst share a
   filesystem, which is why the tmp lives in the target directory).
4. `os.fsync` the directory so the rename survives a crash.
5. On any failure between step 1 and 4, the tmp file is unlinked.

The tests assert that simulated `fsync`/`replace` failures leave the
target either at its old content or its new content, never in between.

### LLM-driven destructive ops

Destructive tools (`delete_note`, `rename_note`, `move_note`,
`execute_command`) require a **two-phase HMAC confirmation**. A single
hallucinated tool call cannot mutate the vault — or trigger an
Obsidian command — on the first try.

- **Phase 1** (`confirm_token=None`): the tool computes a preview, issues
  a single-use `OperationToken` HMAC-signed against the secret + payload,
  and returns it. The disk is **not** touched.
- **Phase 2** (`confirm_token=<from phase 1>`): the registry verifies the
  token (single-use, TTL-bound, payload-bound), the original file is
  copied into `<vault>/.ohmcp-trash/<UTC-ts>-<short-hash>/`, then the
  destructive op runs atomically (`os.replace` for rename/move,
  `Path.unlink` for delete).

Token format: `base64url(nonce || HMAC_SHA256(secret, op || target ||
payload_hash || expires_at || nonce))`. 32-byte nonce + 32-byte HMAC =
86 base64url chars.

The HMAC secret lives at `~/.obsidian-hardened-mcp/secret` with mode
`0o600`. Any wider mode is treated as compromised — the loader refuses
and the server aborts. Manual rotation: `rm ~/.obsidian-hardened-mcp/secret`,
restart. There is no automatic rotation in v0.1.

In-memory storage is **by design**: a server restart invalidates all
phase-1 tokens. The 90 s TTL makes that acceptable; phase-1 issuances
older than 90 s would have expired anyway.

Snapshots accumulate under `<vault>/.ohmcp-trash/`. They are NEVER
re-exposed by read tools (the directory is in the VaultPath
forbidden-zone list). Manual cleanup convention: prune
`.ohmcp-trash/` yourself when disk usage matters.

`update_backlinks=True` (rename/move) is best-effort: only exact
wikilink targets `[[oldname]]` / `[[oldname.md]]` are rewritten,
free-text occurrences are left alone, and unreadable files are counted
as `skipped_unreadable` rather than crashing the operation.

### Optional Local REST API surface (M7)

When `OBSIDIAN_REST_TOKEN` is set, the server can talk to the
Obsidian Local REST API plugin running on `https://127.0.0.1:27124`.
This unlocks `execute_command` — a tool that triggers a named
Obsidian command (e.g. `editor:focus-current`, `workspace:close`) by
HTTP POST.

Threat-model decisions:

- **`verify=False`** on the httpx client. The plugin ships a
  self-signed certificate for `127.0.0.1`; we accept that posture
  because (a) the endpoint is loopback only — an attacker that can
  speak to it already has process-level access — and (b) the bearer
  token is what actually authenticates the call. A user-provided CA
  bundle is a v0.2 followup (M7-03).
- **Token never logged.** `RestClient.__repr__` masks it. Audit
  records carry `tool="execute_command"` but never the token. Auth
  failures surface as `REST_AUTH_FAILED` with no token in the message.
- **Two-phase HMAC for `execute_command`.** Same protocol as the file
  ops, but the token is bound to the **command id** instead of a
  vault path. The HMAC includes a `p:` / `c:` discriminator to
  prevent collisions when a path and a command share the same
  string. Phase 1 issues the token (no REST call). Phase 2 consumes
  it then POSTs to `/commands/<id>/`.
- **Lazy availability cache.** A `RestAvailabilityDetector` probes
  the API at most once per 60 s; failures are cached for the same
  window so a down endpoint isn't hammered. `get_vault_info`
  exposes `rest_available` based on the cached state.
- **Open command surface.** v0.1 ships without a per-command
  allow-list — any command id the plugin accepts can run, gated
  only by 2-phase HMAC. An allow-list is tracked as M7-04.
- **No semantic dry-run for `execute_command`.** Unlike file ops, a
  pre-execution `GET /commands/<id>/` lookup would add latency and
  a second failure mode for marginal UX gain. v0.1's `dry_run=True`
  returns the command id and the fact that REST is configured;
  nothing more. Tracked as M7-05.

`execute_command` failures emit a destructive `outcome="failure"`
audit entry with `snapshot_id=null` (REST commands have no
filesystem state to snapshot before-the-fact).

### Audit trail integrity (within process scope)

Every write or destructive operation emits a JSONL line to
`~/.obsidian-hardened-mcp/audit/YYYY-MM-DD.jsonl` (off-vault, so vault sync
or git operations cannot rewrite it). Each entry carries:

- `request_id` — unique per MCP tool call (propagated through every
  internal `emit_audit` made within that call so multi-step operations
  correlate correctly).
- `audit_id` — a SHA256 **content hash** over `(tool, vault_path,
  op_kind, outcome, params_hash, dry_run, snapshot_id)`. It deliberately
  ignores volatile fields (`ts`, `request_id`, `duration_ms`) so two
  events with the same content fingerprint share the same `audit_id`,
  which is what enables replay/dedup.
- `params_hash` — canonical JSON-based fingerprint of the tool's input
  parameters; stable across Python versions and dict insertion orders.

## Non-goals — what we DO NOT defend against

These are deliberate scope decisions, not oversights. If your context
matches one of the following, **do not deploy this server**:

### Hostile local users on the same machine

A user with write access to the vault (e.g. a shared home directory)
can:

- Insert a symlink between `VaultPath.from_user()` validation and
  `atomic_write_text()` execution (TOCTOU). The sandbox does NOT
  re-validate at write time using a held file descriptor.
- Read or rewrite the audit log under `~/.obsidian-hardened-mcp/audit/` if
  the home directory permissions allow it.
- Read the HMAC secret at `~/.obsidian-hardened-mcp/secret` if home
  permissions allow it.

If your vault sits on a shared filesystem with mutually-distrusting users,
this server is the wrong tool.

### Concurrent writers

The server has **no advisory lock between concurrent calls**. Two MCP
clients (or two tool calls in flight on the same client) writing to the
same note can race; the last `os.replace` wins, the other write is silently
lost. The audit log records both calls.

This is acceptable for the single-user/single-client use case the server
targets. If you run multiple MCP clients against the same vault, treat
the server as **single-writer**: open one client at a time, or coordinate
manually. We may add per-path `anyio.Lock` serialisation in a later
release; v0.1 will not.

### Restore-from-snapshot

Destructive ops write a snapshot under `.ohmcp-trash/` before mutating.
v0.1 ships **no restore tool** — restoration is a manual operation
(copy the snapshot back to its original path). A scripted
`restore_from_snapshot` is on the v0.2 roadmap.

### Disk pressure from snapshots

Snapshots are **not** automatically pruned. Long-running deployments
with frequent destructive ops will see `<vault>/.ohmcp-trash/` grow.
Treat it as your manual responsibility to prune (the directory is in
the VaultPath forbidden-zone list, so no MCP tool will ever read or
delete from it on your behalf).

### Multi-vault isolation

v0.1 binds to one vault root chosen at server startup. There is no
multi-tenant isolation — if you start the server pointing at vault A,
that's the only thing it can touch. Multi-vault is on the v2 roadmap.

### Mode preservation

Newly-written notes default to file mode `0o644` (the platform default
for `os.open`). Notes you have intentionally tightened to `0o600` will
be loosened on rewrite. **Don't store secrets in vault notes.**

### Network adversaries

The MCP transport is stdio. The optional Local REST API integration
(future M7) talks to `127.0.0.1` only. There is no network listener.

### iCloud offload races

The reader detects iCloud `.icloud` placeholder stubs and refuses to read
them with a `FILE_OFFLOADED` error. The writer does NOT detect this; if
iCloud offloads a file mid-write, the placeholder is simply replaced by
the new content. iCloud history may diverge from the file timeline.

## Operational assumptions

For the threat model above to hold, you must:

1. Run the server on a machine you trust, under your own user account.
2. Keep `~/.obsidian-hardened-mcp/` permissions tight (owner-only).
3. Treat the audit log as evidence — don't share or sync the directory.
4. Run **one MCP client at a time** against a given vault, or accept
   write-loss risk on concurrent edits.
5. Don't store secrets in note bodies or frontmatter (mode-loosening).
6. If you set `OBSIDIAN_REST_TOKEN`, treat it as a vault credential —
   don't share it across machines, rotate it if exposed, and remember
   that any command id valid in your Obsidian config becomes runnable
   via `execute_command`.

## What is enforced by tests

- Path sandbox: 100 % line+branch coverage, 500-example property test
  asserting no input ever escapes vault root.
- YAML safety: read-side and write-side rejection, including
  `!!python/object/apply` vector and bytes/Path/set/custom-class on
  write.
- Atomic writer: simulated failures verify no torn file, tmp cleanup,
  caller-visible error.
- Audit content hash: content-only fingerprint, independent of `ts`,
  `request_id`, `duration_ms`; differs when any of `(tool, vault_path,
  op_kind, outcome, params_hash, dry_run, snapshot_id)` differs.
- Dry-run immutability: the file on disk is byte-identical before/after
  a `dry_run=True` call; the in-memory `CommentedMap` is `deepcopy`-d
  before any mutation.
- 2-phase confirmation: phase-1 issues a token without writing,
  phase-2 consumes (single-use), tokens past their TTL are rejected,
  payload-mismatched calls are rejected, replay is rejected, HMAC
  tampering is rejected, and the secret file is refused if its mode
  is wider than `0o600`.
- REST surface: `RestClient` masks the bearer token in `repr()` and
  in error messages; `execute_command` short-circuits with
  `REST_UNAVAILABLE` when no token is configured or the detector
  reports the API down; the command-bound HMAC includes a `c:`
  discriminator so a path target and a command target with the same
  string never collide.

What is **not** enforced by tests (yet):

- TOCTOU at the `from_user` / `atomic_write_text` boundary.
- Concurrent writer race.
- Mode preservation across rewrite.
- iCloud offload during write.

These are documented gaps tracked for v0.2+.
