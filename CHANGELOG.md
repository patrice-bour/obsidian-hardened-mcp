# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial project scaffolding (M1): pyproject, CI, project layout
- `VaultPath` immutable sandbox class with strict validation
- Security test suite (path traversal, symlinks, forbidden zones)
- `read_note`, `list_notes`, `get_vault_info`, `list_tools_capabilities`
  MCP tools (M1)
- Round-trip-aware frontmatter parser/serializer using `ruamel.yaml`
  (M2): preserves comments, key order and quote style on write-back
- `get_frontmatter` MCP tool returning JSON-clean frontmatter (M2)
- YAML safety: rejects any non-default tag (`!!python/object/...`,
  `!Custom`, etc.) at parse time to defeat round-trip exfiltration of
  unsafe constructs to downstream readers
- Frontmatter size cap (64 KiB default) defending against decompression
  / billion-laughs style attacks
- Atomic filesystem writer (M3): same-directory tmp + write + flush +
  fsync + `os.replace` + dir-fsync; tmp file cleaned on every error path
- `AuditEvent` model + `AuditLogger`: append-only daily JSONL files
  under `~/.obsidian-power-mcp/audit/`, `audit_id` is the SHA256 of the
  canonical payload (deterministic for replay/correlation)
- `create_note`, `update_note`, `append_to_note`, `patch_note` MCP
  tools (M3) — every one supports `dry_run=true` to preview changes
- Atomic frontmatter field operations (M3): `set_frontmatter_field`,
  `delete_frontmatter_field`, `merge_frontmatter` (shallow + deep) with
  full round-trip preservation of untouched fields
- New error codes: `PATCH_COUNT_MISMATCH`, `FIELD_NOT_FOUND`,
  `ALREADY_EXISTS`

### Changed (M3.5 — code review hardening)
- **`audit_id` is now a CONTENT HASH** over `(tool, vault_path, op_kind,
  outcome, params_hash, dry_run, snapshot_id)`. Volatile fields (`ts`,
  `request_id`, `duration_ms`) are deliberately excluded so two events
  with the same content fingerprint share the same `audit_id`. This
  fixes the previous "deterministic" claim that was actually random.
- **`request_id` is generated ONCE per tool call** and propagated through
  every `emit_audit`. Previously each `_emit` minted its own random id,
  breaking correlation between phases of the same operation.
- **`params_hash` is canonical** (JSON sort_keys + `default=repr`), so
  dicts with the same keys but different insertion orders give the
  same hash. Previously the `repr()`-based fingerprint was non-canonical.
- **Frontmatter writers validate value types** at the tool boundary.
  `set_frontmatter_field` / `merge_frontmatter` reject bytes, Path,
  set/frozenset, tuple, custom classes, datetime objects, oversized
  strings, deeply nested structures, and non-string dict keys with
  `UNSAFE_YAML`. Closes the round-trip safety loop with the parser.
- **`dry_run` operations deepcopy the in-memory frontmatter** before
  mutation. The on-disk file and the original parse result are both
  guaranteed unchanged after a `dry_run=True` call.
- **Deep merge type-mismatch** behaviour explicitly documented: "wholesale
  replace at the offending key" when patch and target shape differ
  (dict vs list/scalar/None). Tests added.
- `_emit` / `_params_hash` extracted from `tools/write.py` into
  `tools/_base.py` (`emit_audit`, `params_hash`, `new_request_id`).
  Removes private cross-module imports from `tools/frontmatter.py`.
- Documented the **single-writer assumption** (no concurrent-write lock)
  in `docs/security-model.md` instead of pretending it was implemented.
- New `docs/security-model.md` enumerating threats handled and explicit
  non-goals (TOCTOU at write time, hostile local users, concurrent
  writers, mode preservation, multi-vault isolation, iCloud offload
  during write, network adversaries).
