# Architecture

## Module layout

```
src/obsidian_hardened_mcp/
├── __init__.py            # version
├── __main__.py            # CLI entry point (argparse + create_server + run)
├── server.py              # FastMCP wiring — register tools
├── config.py              # AppConfig (vault root, limits, REST opts)
├── domain/
│   ├── vault_path.py      # immutable, sandbox-validated path (security cornerstone)
│   ├── note.py            # Note / Frontmatter / Wikilink (M2+)
│   ├── results.py         # ToolResult, ErrorCode, ErrorInfo
│   ├── tokens.py          # OperationToken (M6)
│   └── audit.py           # AuditEvent (M3)
├── fs/
│   ├── reader.py          # read_text with size limits + iCloud detection
│   ├── listing.py         # markdown enumeration with forbidden-zone pruning
│   ├── writer.py          # atomic writes (M3)
│   └── snapshot.py        # snapshot-before-destruction (M3)
├── frontmatter/           # ruamel.yaml round-trip parser (M2) + atomic field ops (M3)
├── validation/            # JSON Schema + pluggable hooks (M4)
├── security/              # 2-phase confirm + audit logger (M3, M6)
├── rest/                  # optional Local REST API client (M7)
└── tools/
    ├── _base.py           # @tool_call decorator + exception → ErrorCode mapping
    ├── read.py            # read_note, list_notes
    ├── meta.py            # get_vault_info, list_tools_capabilities
    ├── frontmatter.py     # get_frontmatter (M2); set/delete/merge_frontmatter_field (M3); manage_tags (v0.3.0)
    ├── write.py           # create_note, update_note, append_to_note, patch_note (M3)
    └── destructive.py     # delete_note, rename_note, move_note (M6)
```

## Invariants

1. **Every vault path crosses `VaultPath.from_user`** before any filesystem
   call. Tools never accept raw `Path` objects.
2. **Writes are atomic** via tmp + fsync + `os.replace` in the same directory
   as the target.
3. **Destructive operations require a 2-phase HMAC token** (M6+).
4. **Every write/destructive operation emits an `AuditEvent`** to the JSONL
   audit log (M3+).
5. **Frontmatter parsing uses `ruamel.yaml` in safe mode only**.
6. **Validation hooks run in declared order** (M4+) before any write reaches
   disk; one `reject` aborts the entire operation.

## Tools

### Read tools

#### `read_note`

Fetch the full text of a single note by path. Returns the raw body;
frontmatter is accessible via `get_frontmatter`.

#### `list_notes`

Enumerate all notes under a folder (or the vault root). Returns metadata
(size, modified time) but not full contents.

#### `read_multiple_notes`

Batch-read primitive. Iterates the input `paths` in order, catching
per-path failures (path escape, not-found, file-too-large, etc.) into
`results[i].error` rather than aborting the call. Top-level rejection
applies to empty inputs and to `len(paths) > config.max_batch`. A
cumulative byte cap (`config.max_batch_bytes`, default 10 MB) stops
iteration once exceeded; remaining entries are marked
`BATCH_TOO_LARGE`. No audit emission (per CLAUDE.md invariant #4 —
write/destructive only).

### Frontmatter operations

#### `get_frontmatter`

Parse and return the YAML frontmatter of a note as a structured object,
preserving the original file's YAML formatting (comments, key order,
quote styles). Rejects unsafe YAML constructs (custom tags, Python
objects). The frontmatter block is optional; returns `{}` if absent.

#### `set_frontmatter_field`

Atomically set or update a single YAML field in a note's frontmatter.
The rest of the file (including formatting, comments, and other keys)
is preserved exactly. If the frontmatter block does not exist, one is
created. Emits an audit event on success.

#### `delete_frontmatter_field`

Atomically remove a single YAML field from a note's frontmatter. If the
field does not exist, this is a silent no-op. If the frontmatter block
becomes empty after deletion, the entire block is removed from the file.
Emits an audit event on success.

#### `merge_frontmatter`

Atomically merge a value (typically a dict or list) into a single YAML
field. If the field does not exist, it is created. For objects, keys are
merged with shallow union semantics; lists, scalars, and type mismatches
are replaced wholesale. Emits an audit event on success.

#### `manage_tags`

Dedicated tag primitive for the `tags:` frontmatter field. Supports
four ops: `add` (idempotent), `remove` (silent no-op for absent tags),
`replace` (wholesale set, `[]` clears), and `list` (read-only, no
audit). Input tags are normalised: leading `#` stripped, whitespace
trimmed, validated against `^[A-Za-z0-9_./-]+$`, no leading/trailing
`/`. When the resulting list is empty (after `remove` or
`replace=[]`), the `tags:` key is removed from the frontmatter
entirely. Reuses the `parse_note` / `render_note` / `atomic_write_text` primitives
directly (the same building blocks `_mutate_frontmatter` uses) for round-trip preservation.

### Async wrappers for elicit-gated tools (M6-11)

`delete_note` and `execute_command` are registered as `async def`
wrappers in `server.py`. They `await ctx.elicit(...)` at Phase 2
before delegating to the sync impl in `tools/destructive.py`. This
keeps the impl unchanged (213 existing test hits at the impl level
are unaffected) while routing the confirmation through the client UI.

## Tool result shape

Every tool returns `ToolResult`:

```python
class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    error: ErrorInfo | None = None  # ErrorCode + message + details
    dry_run: bool = False
    audit_id: str | None = None     # set by write/destructive operations (M3+)
```

`ErrorCode` is a stable, machine-readable enum. Clients can branch on it
without parsing free-form messages.

## Threat model (M1)

The path sandbox in `domain/vault_path.py` defends against:

- **Path traversal** (`..`, mid-path, encoded)
- **Absolute path injection** (`/etc/passwd`)
- **Symlink escape** (component is a symlink to outside the vault)
- **Forbidden zone access** (`.obsidian/`, `.git/`, `.trash/`, `.ohmcp-trash/`,
  the project config file)
- **Length / segment count attacks** (4096-char path, 32-segment, 255-byte
  segment limits)
- **Null byte injection**
- **Unicode NFD vs NFC confusion** (HFS+/APFS, iCloud)

Coverage on `domain/vault_path.py` is enforced at **100 %** by CI; an
hypothesis property test (500 examples) asserts that no input ever produces
a `VaultPath` whose absolute form escapes the vault root.
