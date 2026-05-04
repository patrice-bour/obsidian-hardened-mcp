# M6 implementation brief — destructive ops with 2-phase HMAC tokens

## Goal

Add the destructive surface of the server: `delete_note`, `rename_note`,
`move_note`. Each takes a **two-phase confirmation** path so that no
hallucinated tool call by an upstream LLM can wipe a vault on the first try.

The user explicitly insisted on a high-security stance. Destructive ops are
the most-asked, least-reversible class — they get the heaviest treatment.

## What "two-phase confirm" means

Phase 1 (no `confirm_token` in the call):
- The tool **does not touch the disk**.
- It computes a **preview** (full path, snapshot id reserved, body length,
  what wikilinks would be updated for `rename`/`move`) and emits an
  `OperationToken` HMAC-signed against the secret + payload.
- Returns the token in `data["confirm_token"]` plus the preview.

Phase 2 (caller passes the same `confirm_token` back):
- The token is looked up in the in-memory registry. Single-use; once
  consumed, the entry is removed.
- If the token is unknown, expired, or its bound payload differs from
  the current call, the operation is rejected with
  `ErrorCode.CONFIRMATION_REQUIRED`.
- A snapshot of the original state is taken under `.opmcp-trash/<ts>/`
  before any mutation.
- The op runs; the audit event records `op_kind="destructive"` plus the
  `snapshot_id`.

## New module: `security/confirm.py`

Mirror the design of `security/audit_logger.py` (small, single-purpose).

```python
@dataclass(frozen=True)
class OperationToken:
    token: str               # base64url(HMAC_SHA256(secret, payload+nonce+exp))
    operation: Literal["delete_note", "rename_note", "move_note", "batch"]
    target: VaultPath        # the file/dir the op will touch
    expires_at: datetime     # UTC; default +90s from issue
    payload_hash: str        # canonical hash of the FULL phase-2 payload

class ConfirmRegistry:
    def __init__(self, secret: bytes, ttl_seconds: int = 90) -> None: ...
    def issue(self, *, operation, target, payload_hash) -> OperationToken: ...
    def consume(self, token: str, *, expected_operation, expected_target,
                expected_payload_hash) -> None:
        # Single-use. Raises:
        #   - InvalidConfirmationTokenError (unknown / replayed)
        #   - ExpiredConfirmationTokenError
        #   - PayloadMismatchError
        ...
```

### HMAC secret bootstrapping

- Path: `~/.obsidian-power-mcp/secret`
- Mode: `0o600` (enforced at write; refuse to load if mode is wider — single
  finding to log + abort load).
- Generated on first boot (`secrets.token_bytes(32)`) if absent.
- Loaded once at server startup; passed into `ConfirmRegistry` via
  `create_server`.
- **Never** logged, never returned to clients.

### Token format

```
token := base64url(nonce || hmac_sha256(secret, op || "\x1e" || str(target.relative)
                                        || "\x1e" || payload_hash
                                        || "\x1e" || expires_at.isoformat()
                                        || "\x1e" || nonce))
```

Length: 32-byte nonce + 32-byte HMAC = 64 bytes → 86 chars base64url.

### Storage

In-memory `dict[token, OperationToken]`. Single-use: pop on consume. TTL
sweep on every `consume()` and `issue()` (cheap, called on demand).

In-memory only by design — server restart invalidates phase-1 tokens, which
is **acceptable** (TTL is 90 s anyway) and aligns with the threat-model
mention in `docs/security-model.md`.

## New module: `fs/snapshot.py`

```python
def snapshot_for_destruction(vp: VaultPath, *, snapshot_root: Path) -> str:
    """Copy the file (or directory tree) into snapshot_root/<UTC-ts>/...
    Returns the snapshot_id (the timestamp directory name)."""
```

- Snapshots live in `<vault_root>/.opmcp-trash/` (already in the forbidden
  zones so MCP tools can never read them back).
- Per snapshot: `YYYYMMDDTHHMMSSZ-<short-hash>/<original-relative-path>`.
- Use `shutil.copy2` for files (preserves metadata). For directory ops in
  M6 (`rename_note`, `move_note` of a single file) we still snapshot the
  one file.
- Snapshot creation is **best-effort**: if it fails, the destructive op
  aborts (we don't proceed without a recovery path).

## New tools (`tools/destructive.py`)

All take a `confirm_token: str | None = None` keyword. Phase 1 = `None`,
phase 2 = the token returned in phase 1.

```python
def delete_note(
    config, audit, registry, *, path: str, confirm_token: str | None = None,
    dry_run: bool = False,
) -> ToolResult:
    """Delete a note. Phase 1 returns a token + preview; phase 2 deletes."""

def rename_note(
    config, audit, registry, *, path: str, new_name: str,
    confirm_token: str | None = None, update_backlinks: bool = False,
    dry_run: bool = False,
) -> ToolResult:
    """Rename within the same folder. `new_name` is a filename only.
    `update_backlinks=True` triggers a best-effort scan + rewrite — see below."""

def move_note(
    config, audit, registry, *, path: str, new_folder: str,
    confirm_token: str | None = None, update_backlinks: bool = False,
    dry_run: bool = False,
) -> ToolResult:
    """Move to a new folder. `new_folder` is a vault-relative folder path."""
```

### Phase 1 behaviour (`confirm_token is None`)

1. Validate `path` (and `new_name` / `new_folder` when applicable) through
   `VaultPath.from_user`.
2. Existence check: refuse with `NOT_FOUND` if `path` doesn't exist.
3. For `rename_note` / `move_note`: refuse with `ALREADY_EXISTS` if the
   destination already exists (no clobber).
4. Compute `payload_hash` (`tools._base.params_hash`) of the full phase-2
   payload, including `update_backlinks` so the user can't flip it.
5. `registry.issue(...)` → returns `OperationToken`.
6. Build the preview:
   - `path`, `would_become` (rename/move) or `would_remove` (delete)
   - `size_bytes`
   - For `update_backlinks=True`: enumerate the backlinks that *would* be
     rewritten (don't rewrite). This is the dry-run-by-design.
7. Emit an audit event with `op_kind="destructive"`, `outcome="success"`,
   `dry_run=True` (the issue itself is treated as a dry-run from an
   audit-trail perspective).
8. Return `ToolResult.success(data={..., "confirm_token": token.token,
   "expires_at": token.expires_at.isoformat()})`.

### Phase 2 behaviour (`confirm_token` provided)

1. Re-validate paths (defence in depth — never trust the token alone).
2. Recompute `payload_hash`. Call `registry.consume(...)` — single-use.
3. Take a snapshot via `fs.snapshot.snapshot_for_destruction`.
4. Perform the op:
   - `delete_note`: `Path.unlink()`.
   - `rename_note` / `move_note`: `os.replace(src, dst)` — atomic POSIX.
5. If `update_backlinks=True`: scan all `.md` files for `[[<old_target>]]`
   and rewrite to `[[<new_target>]]`. Best-effort:
   - The OLD path's basename (with and without `.md`) is the search key.
   - Only update **exact wikilink targets** (i.e. inside `[[ ... ]]`),
     not free-text occurrences.
   - Skip files that fail to read or parse (count as `skipped_*` in the
     result, mirror M5's `search_notes` discipline).
   - Each rewrite goes through `fs.writer.atomic_write_text`.
   - Emit one audit event per rewritten file with `op_kind="write"`.
6. Emit the destructive audit event with `op_kind="destructive"`,
   `outcome="success"`, `snapshot_id=<id>`, `dry_run=False`.

### Dry-run mode

`dry_run=True` is a third orthogonal switch (NOT phase 2): it replays
phase 1 + the planned mutation without writing. Returns the preview but
no `confirm_token` (the caller is asking "what would happen", not
"prepare an op").

## Wiring & registration

- `create_server(config, *, hooks=None, registry=None)` — new optional
  `registry` parameter; default constructs a `ConfirmRegistry` bound to
  the secret. Tests pass an explicit registry to avoid touching the
  filesystem secret.
- `tools/meta.py` manifest gains the three new entries with
  `kind: "destructive"`.
- New `ErrorCode` values: `CONFIRMATION_REQUIRED`,
  `INVALID_CONFIRMATION_TOKEN`, `EXPIRED_CONFIRMATION_TOKEN`,
  `PAYLOAD_MISMATCH`. Mapped in `tools._base.map_exception`.

## Tests to write (TDD)

Cover at minimum these classes (file: `tests/unit/test_tools_destructive.py`,
plus `tests/unit/test_confirm_registry.py`, `tests/unit/test_fs_snapshot.py`).

### Confirm registry
- Issue → consume same token: ok.
- Consume unknown token → `InvalidConfirmationTokenError`.
- Consume after TTL → `ExpiredConfirmationTokenError`.
- Replay (consume twice) → second call raises (single-use).
- Wrong target / wrong operation / wrong payload_hash → `PayloadMismatchError`.
- HMAC tampering: take a valid token, flip a byte → reject.

### Snapshot
- Snapshot a file → copy exists at expected path; original still in place.
- Snapshot id is unique across rapid successive calls (timestamp + hash).
- Snapshot dir lands in `<vault>/.opmcp-trash/` (forbidden zone).
- Snapshot of a missing file → raise.

### `delete_note`
- Phase 1 (no token) returns `confirm_token` + preview, file untouched.
- Phase 2 with returned token deletes file, emits audit, snapshot exists.
- Phase 2 with stale token (>TTL) → `EXPIRED_CONFIRMATION_TOKEN`.
- Phase 2 with payload-mismatched call (different `path`) → reject.
- Phase 1 on `../escape` → `PATH_ESCAPE` (sandbox holds).
- `dry_run=True` returns preview, no token, file untouched.
- File missing → phase 1 fails with `NOT_FOUND`.

### `rename_note`
- Within same folder; success cycle (phase 1 → phase 2 → renamed).
- `new_name` containing `/` → `INVALID_PATH` (must be a filename only).
- Destination exists → phase 1 fails with `ALREADY_EXISTS`.
- `update_backlinks=True`: phase 1 lists backlinks that would be touched
  but doesn't write; phase 2 rewrites all `[[OldName]]` to `[[NewName]]`
  and emits one audit event per rewritten file.
- Files unreadable during backlink scan are counted, not crashed.

### `move_note`
- Move to a different folder; success cycle.
- `new_folder` containing traversal → `PATH_ESCAPE`.
- Cross-volume move not in scope (vault is one filesystem).
- Backlinks: same expectations as `rename_note`.

### Integration
- Server-level: `await server.call_tool("delete_note", {"path": ...})`
  returns a token; second call with the token deletes. Without the token,
  the file is preserved.

## Performance notes

- Backlink scan reads every `.md` file in the vault. On the pbkm vault
  (3618 notes) that's slow without an index. v0.1 ships the naive scan;
  the v0.2 ripgrep / cache work tracked in `docs/v0.1-followups.md`
  (M5-01, M5-02) will speed this up too.

## Items pre-approved as out-of-scope for M6

- Multi-vault: still v2.
- Cross-volume rename: refuse with a clear error.
- Restore-from-snapshot tool: snapshots are written but the restore
  flow is a separate v0.2 feature (track as M6-followup if needed).

## Threat-model implications

Update `docs/security-model.md`:
- The HMAC secret rotation is manual (delete the file, restart).
- Destructive operations now produce snapshots — disk usage grows.
  Document a manual cleanup convention (`.opmcp-trash/` is yours to
  prune).

## Suggested commit shape

```
feat(M6): destructive ops with 2-phase HMAC tokens

Adds delete_note, rename_note, move_note. Each takes a phase-1 issue
(returns a single-use HMAC token + preview) followed by a phase-2
commit that consumes the token, snapshots the original state, and
applies the change atomically.

- security/confirm.py: ConfirmRegistry (HMAC-SHA256 over secret +
  payload + nonce + exp; single-use; 90s TTL).
- fs/snapshot.py: snapshot_for_destruction → .opmcp-trash/<ts>/.
- tools/destructive.py: the three tools, with `confirm_token`,
  `dry_run`, and `update_backlinks` (rename/move).
- New error codes: CONFIRMATION_REQUIRED, INVALID_CONFIRMATION_TOKEN,
  EXPIRED_CONFIRMATION_TOKEN, PAYLOAD_MISMATCH.

Co-Authored-By: Claude ...
```

After M6 is committed and merged, run the M6 code review (see the loop
documented in `AGENTS.md` § "Where to resume"), fix critical findings
inline as M6.5, and only then proceed to M7.
