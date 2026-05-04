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

### Added (M4 — pluggable validation)
- `validation.hooks` module with the `ValidationHook` Protocol,
  `HookContext`, `HookResult` (accept/warn/reject), `HookRegistry`,
  `HookViolationError`. Hooks run in declared order; first reject
  short-circuits; warnings accumulate; a crashing hook is treated as a
  rejection (the registry never opens the door because of an unexpected
  exception).
- Three built-in hooks (`validation.builtin_hooks`):
  - `IsoDateHook` — refuse non-ISO-8601 dates in configured fields.
  - `ReservedTagsHook` — refuse forbidden tags or forbidden top-level
    fields (e.g. migration markers).
  - `JsonSchemaHook` — validate frontmatter against a JSON Schema
    (Draft 2020-12) selected by the `type:` field.
- `.obsidian-power-mcp.yaml` config loader (`validation.config_loader`):
  resolves hook names → built-in classes, validates kwargs against each
  hook's signature, loads schema files relative to the vault, refuses
  schema paths that escape the vault, and surfaces errors at boot via
  `ConfigError` rather than at first write.
- `create_server(config, hooks=None)` auto-loads
  `<vault_root>/.obsidian-power-mcp.yaml` when `hooks` is omitted; pass
  `HookRegistry([])` to skip auto-load entirely.
- All write tools and frontmatter atomic operations accept an optional
  `hooks: HookRegistry | None` keyword argument and run validation
  BEFORE any disk write — including in `dry_run=True` mode (preview
  surfaces the same yes/no the real call would).
- New error code `VALIDATION_FAILED` (mapped from `HookViolationError`).
- New `docs/config-reference.md` documenting the YAML format, all
  built-in hooks, and operational notes.
- 272 tests pass (from 204); global coverage 94%; 100% on
  `domain/vault_path`, `fs/`, `domain/`, `security/audit_logger`.

### Fixed (M4.5 — code review hardening)
- **DoS via cyclic JSON Schema `$ref`**: `JsonSchemaHook` now probes
  each schema with a small set of inputs under a lowered recursion
  limit at construction. Mutually-recursive `$refs` (e.g. `A → B → A`)
  are rejected with `CyclicRefError` at server boot rather than
  exploding with `RecursionError` on the first real write.
- **YAML config file safety**: `.obsidian-power-mcp.yaml` is now
  enforced under the same custom-tag whitelist as note frontmatter.
  An attacker cannot smuggle `!!python/object/...` or any non-default
  YAML 1.2 tag into the project config. Shared primitive
  `frontmatter.yaml_safety.enforce_default_tags_only` factored out
  from `frontmatter.parser`.
- **`HookContext` mutation isolation**: `HookRegistry.run` now
  deepcopies the context per hook. A mutating hook can no longer leak
  state to the next hook, nor to the caller. The `frozen=True`
  dataclass already prevented field reassignment but not nested
  dict/list mutation.
- New `docs/v0.1-followups.md` tracks deferred review findings.
