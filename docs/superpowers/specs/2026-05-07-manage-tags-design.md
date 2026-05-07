# Design — `manage_tags` (v0.3.0)

**Status**: approved
**Date**: 2026-05-07
**Tracking**: v0.3 task #2 (mcpvault parity, dedicated tag tool)
**Author**: brainstorming session 2026-05-07

## Context and motivation

Today, manipulating Obsidian tags through the MCP server requires
juggling `set_frontmatter_field("tags", [...])`, `merge_frontmatter`,
or `delete_frontmatter_field("tags")` — none of which are aware of the
list-of-strings shape, deduplication semantics, or the `#tag` notation
clients commonly emit.

`manage_tags` adds a dedicated tag primitive on top of the same
round-trip-safe write machinery (`_mutate_frontmatter`) so callers can:

- add tags idempotently (silent dedupe)
- remove tags safely (no-op when absent, cleanup of empty `tags:`)
- replace the full set in one shot (including clearing all)
- read the current set without round-tripping a full frontmatter parse

This closes the v0.3 mcpvault parity gap noted in
`docs/v0.1-followups.md` while staying in line with `CLAUDE.md`
invariants (atomic writes, audit emission, hook validation,
round-trip YAML).

## Non-goals

- No support for inline `#tag` in note bodies. Out of scope; would
  require body parsing and is a separate concern.
- No tag rename / refactor across the vault. A future
  `rename_tag(old, new)` is plausible but not for v0.3.
- No tag autocompletion or fuzzy match — pure exact-string ops.
- No CSV / scalar string format support for legacy `tags:`. We refuse
  with `MALFORMED_FRONTMATTER` and let the user migrate explicitly.

## API

### Signature

```python
@tool_call
def manage_tags(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    op: Literal["add", "remove", "replace", "list"],
    tags: list[str] | None = None,
    *,
    hooks: HookRegistry | None = None,
    dry_run: bool = False,
) -> ToolResult:
```

### Parameter contract

| Param | Required for | Notes |
|---|---|---|
| `path` | all | Vault-relative; passes through `VaultPath.from_user`. |
| `op` | all | One of `"add"`, `"remove"`, `"replace"`, `"list"`. |
| `tags` | `add`, `remove`, `replace` | List of tag strings. Required and non-empty for `add`/`remove`. May be `[]` for `replace` (= clear all). Ignored for `list`. |
| `hooks` | write ops | Standard `HookRegistry` from server boot; runs against the post-write frontmatter. |
| `dry_run` | write ops | If `True`, computes the result and emits a dry-run audit but does NOT write to disk. |

### Up-front validation (before any I/O)

| Condition | Error |
|---|---|
| `op == "add"` or `"remove"` with `tags is None` or `tags == []` | new `INVALID_TAG` `"op='add' requires non-empty tags"` |
| Any `tag` in input fails the regex `^[A-Za-z0-9_/.-]+$` after `#` strip | `INVALID_TAG` `"tag '<value>' invalid: must match [A-Za-z0-9_/.-]+ and not start/end with /"` |
| Any `tag` starts or ends with `/` after normalisation | `INVALID_TAG` (same message) |
| Tag normalisation produces an empty string (e.g., `"#"` alone) | `INVALID_TAG` |

### Tag normalisation (input side)

For each tag string in `tags`:

1. Trim leading/trailing ASCII whitespace.
2. Strip a single leading `#` if present (e.g., `"#wip"` → `"wip"`).
3. Validate against `^[A-Za-z0-9_/.-]+$`.
4. Reject if it starts or ends with `/`.

After normalisation across the input list, **dedupe in input order**:
the first occurrence wins, subsequent duplicates are dropped silently.

### Existing-frontmatter shape check

When the function reads the existing `tags:` field:

- Absent → treated as `[]` for read ops; created with the input list
  for `add`/`replace`; no-op for `remove`.
- Present and `isinstance(existing, list)` and every element is `str`
  → OK, operate on it.
- Anything else (string CSV, dict, scalar non-string, list with
  non-string elements) → `MALFORMED_FRONTMATTER` `"existing 'tags:'
  field is not a list of strings"`.

This is intentional: silent migration of `tags: a, b, c` to a YAML
list would surprise clients reading diffs. Refuse and let the caller
fix the source explicitly.

## Operation semantics

### `op="add"`

1. Read existing tags (list[str], default `[]`).
2. For each normalised input tag in input order, append to the result
   list IF it is not already present.
3. Preserve existing order: existing tags first (in their original
   order), then any genuinely new tags (in input order).
4. If any change was made, write the new frontmatter; `tags:` is
   created if absent.
5. If no change was made (every input tag was already present), skip
   the disk write to keep mtime stable. The audit event is still
   emitted with `outcome="success"` so observers see the call
   happened.
6. Output `data.added` is the list of tags that were genuinely added
   (could be empty); `data.removed` is `[]` for `add`.

### `op="remove"`

1. Read existing tags. If absent, no-op success.
2. For each normalised input tag, drop the FIRST matching entry from
   the existing list. Silent no-op for entries not present.
3. If the resulting list is empty and `tags:` existed → call
   `_delete_field(fm, "tags")` to remove the key entirely.
4. If the existing list was already empty or `tags:` was absent, skip
   the disk write (no-op success).
5. Output `data.removed` lists the tags that were genuinely removed;
   `data.added` is `[]`.

### `op="replace"`

1. Compute the new list from normalised + deduped input.
2. If new list is `[]`, call `_delete_field(fm, "tags")` (matches
   `remove`-everything behaviour for symmetry).
3. Otherwise, set `fm["tags"] = new_list` wholesale (no merge).
4. If the new list equals the existing list (same elements, same
   order), skip the disk write (same mtime-stability optimisation
   as `add`). Audit still emits.
5. Output `data.added` lists tags in new but not in old;
   `data.removed` lists tags in old but not in new. Useful for the
   client that wants to know what really changed.

### `op="list"`

1. Read existing tags. Default `[]` if absent.
2. Return `{path, tags}`. **No audit emission, no write, no dry_run
   field in the response.**

## Output schema

### Write ops (`add` / `remove` / `replace`)

```json
{
  "ok": true,
  "data": {
    "path": "notes/x.md",
    "request_id": "abc123...",
    "op": "add",
    "tags": ["existing-a", "existing-b", "newly-added"],
    "added": ["newly-added"],
    "removed": []
  },
  "dry_run": false,
  "audit_id": "<content hash>"
}
```

Field meanings:
- `path`: vault-relative path of the note, post-normalisation by
  `VaultPath`
- `request_id`: shared per call, propagated to all audit events
- `op`: echo of the input `op` (helps clients log)
- `tags`: final state of the tag list AFTER the operation
- `added` / `removed`: real delta — what actually changed (empty for
  no-op cases)

For `replace` with `tags=[]`: `tags` is `[]`, `added` is `[]`,
`removed` is the previous list.

### Read op (`list`)

```json
{
  "ok": true,
  "data": {
    "path": "notes/x.md",
    "tags": ["a", "b"]
  }
}
```

No `request_id`, no `audit_id`, no `dry_run` (matches `read_note` /
`get_frontmatter` envelope).

## New ErrorCode

```python
class ErrorCode(StrEnum):
    ...
    INVALID_TAG = "invalid_tag"
```

Used for:
- missing `tags` when `op` requires it
- a tag string that fails normalisation / regex validation
- a tag string that is empty after `#` strip + whitespace trim

## Audit

Per `CLAUDE.md` invariant #4: write/destructive ops emit an
`AuditEvent`. `manage_tags` write modes (add/remove/replace) emit one
event with `tool="manage_tags"`, `op_kind="write"`, `vault_path` as
the relative path, `params_hash` over `(path, op, normalised_tags)`,
and `dry_run` honored.

`list` does **not** emit (read tool, parity with `read_note` /
`get_frontmatter`).

## No HMAC confirmation

Coherent with sibling frontmatter tools (`set_frontmatter_field`,
`delete_frontmatter_field`, `merge_frontmatter`): metadata edits are
audited but not gated by 2-phase HMAC. The destructive-ops gate is
reserved for `delete_note` / `rename_note` / `move_note` /
`execute_command` — operations that affect the file as a whole or
escape the server boundary.

## Test plan (TDD)

File: `tests/unit/test_tools_frontmatter.py` (new `TestManageTags`
class), or a dedicated `tests/unit/test_tools_manage_tags.py` if the
existing file feels too large after merging.

### Unit tests (20)

**Validation:**
1. `test_add_with_empty_tags_rejected` → `INVALID_TAG`
2. `test_add_with_none_tags_rejected` → `INVALID_TAG`
3. `test_remove_with_empty_tags_rejected` → `INVALID_TAG`
4. `test_invalid_tag_chars_rejected` (e.g., `"a b"`, `"a\nb"`,
   `"/wip"`, `"wip/"`) → `INVALID_TAG`
5. `test_hash_prefix_stripped` (input `"#wip"` → stored `"wip"`)
6. `test_existing_non_list_tags_rejected` → `MALFORMED_FRONTMATTER`

**`add` semantics:**
7. `test_add_to_empty_creates_tags_key`
8. `test_add_dedupe_silent` (existing `["a"]` + add `["a","b"]` →
   `["a","b"]`, `added=["b"]`)
9. `test_add_preserves_existing_order_then_new`

**`remove` semantics:**
10. `test_remove_existing_tag`
11. `test_remove_absent_tag_silent_noop`
12. `test_remove_all_drops_tags_key` (after remove, `tags:` absent
    from frontmatter; verify with a fresh `parse_note` of the
    rendered output)

**`replace` semantics:**
13. `test_replace_overwrites_full_list`
14. `test_replace_empty_drops_tags_key`

**`list` semantics:**
15. `test_list_returns_current_tags`
16. `test_list_empty_when_no_tags_key`
17. `test_list_emits_no_audit` (fixture audit logger remains empty)

**Cross-cutting:**
18. `test_round_trip_preserves_other_fields` (frontmatter has
    `title:`, `date:`, comments; only `tags:` should change)
19. `test_dry_run_no_disk_write` (file content unchanged on disk
    after `dry_run=True`)
20. `test_hook_violation_rejected` (a `reserved_tags.forbidden` hook
    rejects → `VALIDATION_FAILED`)

### E2E (2)

In `tests/e2e/scenarios/s3_frontmatter.py`:
- happy: add `["wip"]` to a clean note → list returns `["wip"]`
- partial: add `["a"]` then remove `["a"]` → `tags:` key gone

### Coverage target

100 % of new lines (`manage_tags` + `_normalize_tags` +
`_validate_tag` + the operation dispatchers). Project-wide ≥ 85 %
unchanged.

## Cross-cutting changes

| File | Change |
|---|---|
| `src/obsidian_hardened_mcp/tools/frontmatter.py` | Add `manage_tags`, `_normalize_tags`, `_validate_tag`, the four `_apply_*` op functions |
| `src/obsidian_hardened_mcp/domain/results.py` | Add `INVALID_TAG` to `ErrorCode` |
| `src/obsidian_hardened_mcp/server.py` | Register the new `@app.tool` (between `merge_frontmatter` and `search_notes`) |
| `src/obsidian_hardened_mcp/tools/meta.py` | Add `manage_tags` to capabilities manifest (avoids S0 smoke regression) |
| `tests/unit/test_tools_frontmatter.py` | New `TestManageTags` class (20 tests) |
| `tests/e2e/scenarios/s3_frontmatter.py` | 2 new `rep.add` steps |
| `docs/architecture.md` | New "Tag operations" subsection under Tools |
| `README.md` | Mention in tools list / capability summary |
| `CHANGELOG.md` | Entry under `[Unreleased].Added` |

No migration required — new tool with default behaviour, existing
notes untouched.

## CLAUDE.md invariants compliance

| # | Invariant | Compliance |
|---|---|---|
| 1 | All vault paths via `VaultPath` | ✅ via `_mutate_frontmatter` |
| 2 | Atomic writes | ✅ via `atomic_write_text` |
| 3 | 2-phase HMAC for destructive ops | N/A (frontmatter edit, not file destruction) |
| 4 | AuditEvent on write | ✅ for add/remove/replace; N/A for list (read) |
| 5 | `ruamel.yaml` round-trip | ✅ via `parse_note` / `render_note` |
| 6 | Frontmatter writer type whitelist | ✅ tags are `str`, already in the whitelist |
| 7 | `request_id` once per call | ✅ via `_mutate_frontmatter` |
| 8 | Validation hooks before write | ✅ via `_mutate_frontmatter` |
| 9 | Single-writer assumption | ✅ no concurrency change |

## Risks and mitigations

- **Risk**: a client passes `tags=None` explicitly for `op="list"`,
  expecting it to silently work. **Mitigation**: spec says `tags` is
  ignored for `list`; tests cover this.
- **Risk**: a regex-invalid tag is sneaked in via `replace` because
  the user assumes "no validation on replace". **Mitigation**:
  validation is centralised in `_normalize_tags` and applies to all
  write ops uniformly.
- **Risk**: round-trip preservation breaks if `tags:` is the only
  frontmatter key and gets removed (renders as no-frontmatter file).
  **Mitigation**: existing `parse_note` / `render_note` already handle
  empty-frontmatter rendering correctly; the round-trip test suite
  for `delete_frontmatter_field` already covers this case.
- **Risk**: dedupe collapses tags that look the same after normalisation
  (e.g., `"WIP"` and `"wip"`). **Mitigation**: the spec is
  case-sensitive — `WIP` and `wip` are distinct. This matches Obsidian
  behaviour. Documented explicitly in the docstring.

## Out of scope

- Body inline `#tag` extraction
- Vault-wide `rename_tag`
- Hierarchical tag awareness beyond exact-string match (e.g.,
  removing `"project"` does NOT remove `"project/aaa"`)
- Tag autocompletion / fuzzy match
- Migration from string-CSV `tags: a, b, c` to YAML-list shape

These can be considered for v0.3.x or v0.4 if real users surface the
need.
