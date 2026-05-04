# Architecture

## Module layout

```
src/obsidian_power_mcp/
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
    ├── frontmatter.py     # get_frontmatter (M2); set/delete/merge_frontmatter_field (M3)
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
- **Forbidden zone access** (`.obsidian/`, `.git/`, `.trash/`, `.opmcp-trash/`,
  the project config file)
- **Length / segment count attacks** (4096-char path, 32-segment, 255-byte
  segment limits)
- **Null byte injection**
- **Unicode NFD vs NFC confusion** (HFS+/APFS, iCloud)

Coverage on `domain/vault_path.py` is enforced at **100 %** by CI; an
hypothesis property test (500 examples) asserts that no input ever produces
a `VaultPath` whose absolute form escapes the vault root.
