# Design — `read_multiple_notes` (v0.3.0)

**Status**: approved
**Date**: 2026-05-07
**Tracking**: v0.3 task #1 (mcpvault parity)
**Author**: brainstorming session 2026-05-07

## Context and motivation

The v0.1.0 → v0.2.x line ships a single-note read tool (`read_note`).
The MCP ecosystem (notably `bitbonsai/mcpvault`) has converged on a
batch-read primitive `read_multiple_notes` that lets a client load
several notes in one round-trip. Adding this closes a parity gap and
removes an N-round-trip cost for clients that pre-fetch a working
set (e.g., a graph traversal that visits 10 notes).

This is the first of three tools landing in v0.3.0
(`read_multiple_notes` → `manage_tags` → M6-11 `Context.elicit()`).

## Non-goals

- Not a streaming / pagination API. The caller submits a list, the
  server returns all of it (subject to caps).
- Not a search/filter API — the caller already knows which paths it
  wants. `search_notes` and `list_notes` cover discovery.
- No `include_frontmatter` / `include_backlinks` / etc. options.
  YAGNI — the caller can compose with `get_frontmatter` if needed.
  We can add such flags in a v0.3.x patch if a user ever asks.

## API

### Signature

```python
@tool_call
def read_multiple_notes(config: AppConfig, paths: list[str]) -> ToolResult:
    """Read N notes in one round-trip with partial-success semantics."""
```

### Input validation (before any I/O)

| Condition | Error |
|---|---|
| `paths == []` | `INVALID_PATH "paths cannot be empty"` |
| `len(paths) > config.max_batch` | `BATCH_TOO_LARGE "N paths exceeds max_batch=M"` |

Duplicates are **allowed**. They return the same content twice — the
caller chose to ask twice.

### Iteration semantics

Iterate in **input order**. For each path `paths[i]`:

1. `VaultPath.from_user(path, config.vault_root)` — escape /
   forbidden-zone errors are caught and stored as
   `results[i].error`. Iteration **continues**.
2. `read_text(vp, max_size_bytes=config.max_file_size_bytes)` — NOT_FOUND
   / NOT_A_FILE / FILE_TOO_LARGE / etc. are caught and stored as
   `results[i].error`. Iteration **continues**.
3. Success → `results[i] = {path, content, size}` and
   `cumulative_bytes += size`.
4. **If `cumulative_bytes > config.max_batch_bytes` after this read**:
   stop. Remaining paths `paths[i+1..N-1]` get
   `error.code = BATCH_TOO_LARGE` with message
   `"cumulative size cap reached at index <i>"`. `stopped_early` is
   set to `true` in the response.

The cap is **post-read** by design: a single under-cap file always
gets returned, even if it tips the cumulative total over. This is
simpler to reason about than a pre-flight stat sweep and matches the
"partial success" spirit (give the caller what we can).

### Output schema

Example with `max_batch_bytes = 10 * 1024 * 1024` and four input
paths whose individual sizes (when readable) are 4 MB, N/A
(missing), 7 MB, ?:

```json
{
  "ok": true,
  "data": {
    "results": [
      {"path": "notes/a.md", "content": "...", "size": 4194304},
      {"path": "notes/b.md", "error": {"code": "NOT_FOUND", "message": "notes/b.md not found"}},
      {"path": "notes/c.md", "content": "...", "size": 7340032},
      {"path": "notes/d.md", "error": {"code": "BATCH_TOO_LARGE", "message": "cumulative size cap reached after index 2"}}
    ],
    "cumulative_bytes": 11534336,
    "stopped_early": true
  }
}
```

Index 2 succeeded and tipped the cumulative total above 10 MB
(4 MB + 7 MB = 11 MB). Iteration stopped, index 3 was marked.

#### Schema invariants

- `len(results) == len(paths)` always (when the input passes initial
  validation).
- `results[i].path == paths[i]` always — the original input string,
  not the `VaultPath.relative` normalisation. Clients correlate by
  index but `path` echoes the input for human readability.
- Each entry has **exactly one** of `{content, size}` or `{error}`,
  never both, never neither.
- `cumulative_bytes` is the sum of `size` across **all entries that
  have a successful read** — including the entry that tripped the
  cap, because that read completed before the cap was checked
  (see "Iteration semantics" step 4).
- `stopped_early: bool` is `true` iff at least one entry has
  `error.code == BATCH_TOO_LARGE` because of the cumulative cap.
  This distinguishes "all paths processed, some failed" from
  "iteration cut short by cap".
- Per-entry `BATCH_TOO_LARGE` message format:
  `"cumulative size cap reached after index <j>"` where `<j>` is
  the index of the *last successful read* (the one that pushed
  cumulative over the cap).

### Audit

**No audit emission.** This matches the existing convention for
`read_note` and `list_notes`: `CLAUDE.md` invariant #4 limits
`AuditEvent` to write/destructive ops. A high-volume read tool
emitting per-path events would dominate the audit log without
adding security value.

### New `ErrorCode`

```python
class ErrorCode(StrEnum):
    ...
    BATCH_TOO_LARGE = "BATCH_TOO_LARGE"
```

Used for both:
- the up-front rejection (N > `max_batch`), and
- the per-entry cumulative-cap marker (entries past the cap point).

The message disambiguates ("N paths exceeds max_batch=M" vs
"cumulative size cap reached at index i").

### New config field

`AppConfig.max_batch_bytes: int = 10 * 1024 * 1024`  (10 MB)

Surchargeable via the YAML config file the same way other AppConfig
fields are. Documented in `docs/config-reference.md`.

## Test plan (TDD)

File: `tests/tools/test_read_multiple_notes.py`

### Unit tests (write red first, then implement to green)

1. `test_empty_paths_rejected`
2. `test_too_many_paths_rejected` (asserts no read effected by
   patching `read_text` and verifying it was not called)
3. `test_single_success`
4. `test_all_succeed_preserves_order`
5. `test_partial_success_not_found`
6. `test_partial_success_path_escape`
7. `test_partial_success_forbidden_zone` (e.g., `.obsidian/foo.md`)
8. `test_partial_success_file_too_large`
9. `test_cumulative_cap_stops_iteration` — 2 files of 6 MB then
   1 of 6 MB, `max_batch_bytes=10MB` → `[ok, ok, BATCH_TOO_LARGE]`,
   `stopped_early=true`, `cumulative_bytes=12MB` (the 2nd read
   completed before the cap was checked)
10. `test_cumulative_cap_marks_remaining` — 5 files of 4 MB,
    cap 10 MB → `[ok, ok, ok, BATCH_TOO_LARGE×2]`,
    `cumulative_bytes=12MB`
11. `test_no_early_stop_when_under_cap` — `stopped_early=false`
12. `test_duplicates_allowed`
13. `test_path_field_preserves_input` — input `"./notes/a.md"`
    yields `results[0].path == "./notes/a.md"`
14. `test_no_audit_event_emitted` — fixture `AuditLogger` recording
    no entries
15. `test_cumulative_bytes_field_correct`

### Property test (hypothesis)

- `test_results_length_equals_input_length` — for any valid
  `paths` list with `1 ≤ N ≤ max_batch`, `len(results) == N`.

### E2E

Add 1–2 cases in `tests/e2e/`:
- happy path with 3 notes
- partial-success with 1 NOT_FOUND in the middle

### Coverage target

100 % on the new function (the helper logic is simple and the
branches are all exercised by the unit tests above). The project
overall stays at ≥ 85 %.

## Cross-cutting changes

| File | Change |
|---|---|
| `src/obsidian_hardened_mcp/tools/read.py` | Add `read_multiple_notes` |
| `src/obsidian_hardened_mcp/config.py` | Add `max_batch_bytes` field to `AppConfig` |
| `src/obsidian_hardened_mcp/domain/results.py` | Add `BATCH_TOO_LARGE` to `ErrorCode` |
| `src/obsidian_hardened_mcp/server.py` (or equivalent registration site) | Register the new MCP tool |
| `docs/architecture.md` | Mention in Read tools section |
| `docs/config-reference.md` | Document `max_batch_bytes` |
| `README.md` | Add row in the tools table |
| `CHANGELOG.md` | `### Added` entry under `[Unreleased]` (will roll up into `[0.3.0]`) |

No migration required — new config field has a sensible default,
existing deployments pick it up transparently.

## Invariants checked against `CLAUDE.md`

| # | Invariant | Compliance |
|---|---|---|
| 1 | All vault paths via `VaultPath` | ✅ `VaultPath.from_user` per path |
| 2 | Atomic writes | N/A (read tool) |
| 3 | 2-phase HMAC for destructive ops | N/A (read tool) |
| 4 | AuditEvent on write/destructive | N/A (read tool — invariant scopes writes only) |
| 5 | `ruamel.yaml` round-trip | N/A (no frontmatter parsing) |
| 6 | Frontmatter writer type whitelist | N/A (no writes) |
| 7 | `request_id` per call, `audit_id` content hash | N/A (no audit) |
| 8 | Validation hooks before write | N/A (no writes) |
| 9 | Single-writer assumption | N/A (read tool) |

## Risks and mitigations

- **Risk**: an over-zealous client passes thousands of paths to defeat
  rate limits. **Mitigation**: `max_batch` rejects up front.
- **Risk**: cumulative-cap logic off-by-one (does the entry that trips
  the cap get returned or not?). **Mitigation**: explicit unit test
  `test_cumulative_cap_stops_iteration` pins the contract — the entry
  *that pushes us over* is included; the *next* entry is the first to
  be marked.
- **Risk**: clients build the wrong mental model and expect index-free
  result correlation. **Mitigation**: README example shows results in
  input order with the `path` field echoing the input.

## Out of scope

- Streaming reads (would need a different MCP shape)
- Per-call cap override (`max_batch_bytes` as input parameter)
- `include_frontmatter` / `include_backlinks` flags

These can be addressed in a v0.3.x patch if a real user need surfaces.
