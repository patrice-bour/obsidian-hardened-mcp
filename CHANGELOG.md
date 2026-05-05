# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Public-flip preparation pass — no code behaviour change, only
metadata / docs / file-layout adjustments to make the repository
suitable for public release.

### Added
- README "End users — zero install with `uvx`" path: a single
  `uvx --from git+...` invocation replaces the previous clone +
  `uv sync` + `uv run` four-step install for non-developer users.
  Quick-start, Claude Desktop, and Claude Code config examples now
  use `uvx` directly. Multiple-vaults section added.
- `SECURITY.md` with private vulnerability reporting via GitHub Security
  Advisories.
- `CONTRIBUTING.md` covering dev setup, conventional commits, and the
  security-coverage gate.
- `CODE_OF_CONDUCT.md` adopting Contributor Covenant 2.1 by reference.
- `.github/ISSUE_TEMPLATE/{bug_report,feature_request,config}.yml` and
  `.github/PULL_REQUEST_TEMPLATE.md`.
- `# SPDX-License-Identifier: Apache-2.0` header on every Python source
  file under `src/obsidian_full_mcp/` (35 files).
- "Are you the right kind of user?" preamble on `docs/security-model.md`
  so an outsider lands on the threat-model assumptions before the
  invariants.
- `docs/internal/README.md` redirecting users to the right
  user-facing doc.

### Changed
- `docs/m{6,7}-implementation-brief.md` moved under `docs/internal/`
  (historical handoff docs, not user-facing).
- README env-var table now spells out the shell-history caveat for
  `OBSIDIAN_REST_TOKEN` inline (was a click-through to the e2e README).

### Fixed
- README status line now says v0.1.1 (was v0.1.0).
- README test-count drift: `533 passed` (was `530 passed`); E2E
  invocation documented.
- `AGENTS.md` sanity-check block: replaced personal absolute path with
  `<repo-root>` placeholder.
- `CHANGELOG.md` now defines `[Unreleased]` and `[0.1.1]` compare
  links (the v0.1.1 link was missing on tag-cut day).
- `tests/security/test_round_trip_golden.py` golden #39 swapped
  `Patrice Bour` → `Jane Doe` in the dotted-key fixture.

## [0.1.1] - 2026-05-04

Cosmetic + quality pass on top of v0.1.0:

- **Repository renamed** from `obsidian-power-mcp` to `obsidian-full-mcp`
  (Python module, CLI entry point, vault config file, HMAC secret
  directory, and `.opmcp-trash/ → .ofmcp-trash/` slug).
- **End-to-end test harness** (`tests/e2e/`) — 10 scenarios driven
  through a real `python -m obsidian_full_mcp` subprocess on a freshly
  seeded vault: smoke, read, write, frontmatter atomic ops, destructive
  2-phase HMAC, path sandbox, YAML safety, validation hooks, audit
  log, REST branch.
- Post-publication code review pass: 4/4 MUST and 13/13 SHOULD
  findings addressed. Highlights below.

### Added
- `OBSIDIAN_AUDIT_DIR` environment variable to relocate the audit log
  directory (useful for CI runners that publish test artefacts).
- `tests/e2e/` end-to-end harness (1817 lines) plus its README.
- E2E runner sandboxes audit logs under `tests/e2e/.runs/audit/` by
  default so test runs no longer pollute `~/.obsidian-full-mcp/audit/`.

### Changed
- **Breaking** — package, CLI bin, vault config, HMAC secret dir, and
  trash slug all migrated from `obsidian-power-mcp` / `.opmcp-trash`
  family to `obsidian-full-mcp` / `.ofmcp-trash`.
- E2E harness: `__aexit__` now LIFO-safe via try/finally; startup
  wrapped in `asyncio.timeout(15s)`, `call()` in `asyncio.timeout(30s)`.

### Fixed
- `OBSIDIAN_REST_TOKEN` and `OBSIDIAN_REST_URL` env vars are now
  actually wired through the CLI entry point (previously documented
  but silently dead-letter — `__main__.py` constructed `AppConfig`
  directly instead of going through `from_env`).
- E2E S8 audit baseline could pick a different file from S8's read if
  the run crossed midnight UTC. The path is now captured once at
  baseline time and threaded through.
- E2E S7 cleanup race: config + schema file drops moved inside the
  `try/finally` so a partial failure can no longer leave orphan
  `.obsidian-full-mcp.yaml` / `_schemas/journal.json` behind.
- E2E S4 destructive: token tampering now uses `secrets.token_urlsafe`
  instead of a one-letter flip, removing the (small) risk of
  collision with the real token.
- E2E S2 atomic-write tmp leftover detection: the previous `.*tmp*`
  glob passed vacuously; replaced with a `set(parent.iterdir())`
  before/after diff that proves the dir contents grew by exactly the
  new file.
- E2E `audit_inspector.read_recent` uses `deque(maxlen=n)` instead of
  reading the whole audit log into memory.
- E2E `run_e2e` glyph output guarded against non-UTF-8 stdout (legacy
  Windows consoles, pipes-to-file).
- E2E S0 cross-checks `list_tools_capabilities` against the MCP
  initialise/list_tools handshake; the baseline tool set is now a
  subset check rather than equality so v0.2 additions don't break S0.
- E2E S9 with-token branch: bare except now surfaces
  `traceback.format_exc()` so a real bug isn't silently rebadged as
  "Obsidian probably not running".
- E2E S3 frontmatter: removed the post-test `write_text` restore; the
  runner re-seeds at the start of every full run, and the previous
  restore could have written back a partially-mutated body if S3 had
  failed midway.

### Removed
- Deprecated `License :: OSI Approved :: Apache Software License`
  PEP 639 classifier (the project is still licensed under Apache-2.0
  via `license = { text = "Apache-2.0" }`).
- Unused `CallResult.raw` field — was held in every result but never
  read anywhere; the MCP-error path keeps the body in `error_message`.

### Documentation
- Project README: documents `OBSIDIAN_AUDIT_DIR` and points at the
  shell-history caveat for the REST bearer token.
- E2E README: security note on shell history when exporting
  `OBSIDIAN_E2E_REST_TOKEN`; "Determinism notes" section on what the
  101/101 figure assumes (S9 SKIPPED unless opted in, S5 oversize
  filesystem-dependent).

## [0.1.0] - 2026-05-04

First public-preview release. Local-first single-user Obsidian MCP
server with hardened path sandbox, atomic writes, round-trip
frontmatter, 2-phase HMAC for destructive ops, and an optional
loopback-only Local REST API integration.

530 tests pass; 93 % global line+branch coverage; 100 % on
`security/`, `domain/vault_path.py`, and `fs/writer.py`. ruff and
strict mypy clean.

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
  under `~/.obsidian-full-mcp/audit/`, `audit_id` is the SHA256 of the
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
- `.obsidian-full-mcp.yaml` config loader (`validation.config_loader`):
  resolves hook names → built-in classes, validates kwargs against each
  hook's signature, loads schema files relative to the vault, refuses
  schema paths that escape the vault, and surfaces errors at boot via
  `ConfigError` rather than at first write.
- `create_server(config, hooks=None)` auto-loads
  `<vault_root>/.obsidian-full-mcp.yaml` when `hooks` is omitted; pass
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
- **YAML config file safety**: `.obsidian-full-mcp.yaml` is now
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

### Added (M5 — search + wikilinks)
- `search_notes` MCP tool: literal query against note bodies and/or
  frontmatter, three modes (`fulltext` / `frontmatter` / `combined`),
  filters (folder, tag, type), bounded `limit`. Returns a snippet,
  match kind, and per-match metadata. `combined` mode reports BOTH
  matches when a note hits in both layers (`match_kind="combined"` plus
  separate `snippet` / `frontmatter_field` / `frontmatter_snippet`).
- `resolve_wikilink` MCP tool: parses Obsidian-style `[[Target]]`,
  `[[Target|Alias]]`, `[[Target#Heading]]`, `[[Target^block-id]]`,
  `[[folder/Target]]`. Resolves by basename or path-form. Disambiguates
  same-basename hits via `from_path` (Obsidian shortest-relative).
  Returns `{resolved, alias, heading, block_id, ambiguous, candidates}`.

### Fixed (M5 — code review)
- `combined` mode in `search_notes` now reports BOTH fulltext and
  frontmatter matches when both fire on the same note (was: only the
  first kind, silently dropping the other signal).
- Disjoint `from_path` (no folder prefix shared with any candidate)
  correctly leaves the result `ambiguous=True` instead of silently
  picking a candidate.
- `search_notes` exposes `skipped_read` and `skipped_parse` counters
  so unreadable / malformed files no longer disappear silently.
- Windows-style backslash paths in wikilinks are normalised before
  resolution.
- Mismatched `[[` / `]]` brackets in wikilink targets are rejected
  with `INVALID_PATH` rather than silently mis-parsed.

### Added (M6 — destructive ops with 2-phase HMAC)
- `delete_note`, `rename_note`, `move_note` MCP tools, each guarded by
  the same 2-phase confirmation protocol: phase 1 returns a single-use
  HMAC token + preview without touching the disk; phase 2 consumes the
  token, snapshots the original under `.ofmcp-trash/<UTC-ts>-<hash>/`,
  and applies the change atomically (`Path.unlink` for delete,
  `os.replace` for rename/move). 90 s TTL, single-use, payload-bound.
- `security.confirm` module with `OperationToken`, `ConfirmRegistry`,
  `load_or_bootstrap_secret`. HMAC-SHA256 over secret + (op, target,
  payload_hash, expires_at, nonce). Secret bootstrapped to
  `~/.obsidian-full-mcp/secret` with mode `0o600` enforced;
  any wider mode is refused.
- `fs.snapshot.snapshot_for_destruction`: best-effort copy under
  `.ofmcp-trash/`. The directory is in the VaultPath forbidden-zone
  list so MCP read tools cannot expose snapshots back to clients.
- `update_backlinks=True` (rename/move): best-effort scan + rewrite
  of `[[oldname]]` / `[[oldname.md]]` wikilinks across the vault.
  Skips unreadable files (counted in `skipped_unreadable`); emits one
  `op_kind=write` audit event per rewritten file.
- `dry_run=True` orthogonal mode: preview without issuing a token or
  consuming one.
- New error codes: `CONFIRMATION_REQUIRED`,
  `INVALID_CONFIRMATION_TOKEN`, `EXPIRED_CONFIRMATION_TOKEN`,
  `PAYLOAD_MISMATCH`.

### Fixed (M6.5 — code review hardening)
- Backlink-rewrite audit attribution: `_rewrite_backlinks_phase2`
  used to hardcode `tool="rename_note"` even when called from
  `move_note`. The helper now threads the caller's tool name so the
  per-rewrite write audits correlate to the correct destructive op.

### Added (M7 — optional Local REST API integration)
- `rest.client.RestClient`: thin httpx wrapper for the Obsidian Local
  REST API plugin. Bearer auth, masked-token `__repr__`, error
  taxonomy (`RestUnavailableError`, `RestAuthError`, `RestError`).
  Defaults to `verify=False` because the plugin uses a self-signed
  certificate for `127.0.0.1`.
- `rest.detector.RestAvailabilityDetector`: 60 s TTL availability
  cache with clock injection. Failures cached as unavailable for the
  same window so a down endpoint isn't hammered.
- `execute_command` MCP tool — REST-only, 2-phase HMAC. Same protocol
  as `delete_note` but the token is bound to the **command id**
  (`target_command`) instead of a vault path. The HMAC includes a
  `p:` / `c:` discriminator so a path target and a command target
  with the same string never collide.
- `OperationToken.target_command` field; `OperationName` Literal
  extended with `"execute_command"`.
- `create_server` accepts an optional `rest_detector` parameter; the
  default builds a `RestClient` + detector when `config.rest_token`
  is set.
- `get_vault_info()` now reflects `detector.is_available()` rather
  than the M1 placeholder `False`.
- `list_tools_capabilities()` manifest gains `execute_command` (kind
  = `destructive`).
- New error codes: `REST_UNAVAILABLE`, `REST_AUTH_FAILED`, `REST_ERROR`.

### Fixed (M7.5 — code review hardening)
- `OBSIDIAN_REST_URL` / `AppConfig.rest_url` is now refused unless its
  host is loopback (`127.0.0.1`, `localhost`, `[::1]`). The
  `verify=False` posture is only safe on loopback; pointing the client
  at a remote host would expose the bearer token to whoever answered.
  Track `M7-03` to add a CA-bundle option in v0.2.
- `execute_command` now consumes the confirmation token BEFORE
  checking REST availability. A replayed token whose REST went down
  between phases would otherwise mask the security-relevant
  `INVALID_CONFIRMATION_TOKEN` with a transient `REST_UNAVAILABLE`.
- `_validate_command_id` rejects `\x1e` (the HMAC field separator)
  to keep the encoding unambiguous until the broader length-prefixed
  scheme tracked in M6-01 lands.

### Hardening (M8)
- `fs.snapshot.snapshot_for_destruction` now asserts the resolved
  destination stays under `snapshot_root` before copying. Defence in
  depth on top of the VaultPath sandbox.
- `_FIELD_SEP` documentation tightened — the invariant that path and
  command targets reject the separator is now spelled out.
- Snapshot uniqueness stress test bumped from 5 → 100 successive calls.
- `OperationName` Literal trimmed to actually-implemented operations
  (`"batch"` reservation removed).
- `_SNIPPET_MAX_BYTES` renamed to `_SNIPPET_MAX_CHARS` to match its
  semantics.
- `VaultPath` property test bumped from 500 → 1 000 hypothesis
  examples (plan target).
- New round-trip golden-file corpus (`tests/security/test_round_trip_
  golden.py`): 50 synthetic notes asserting `parse_note` +
  `render_note` is byte-identical, covering comments, key ordering,
  quote styles, nested mappings, ISO dates, unicode (NFC), tags,
  flow style, and body invariants.
- `README.md` rewritten: install, configure, Claude Desktop quick
  start, tool catalogue, two-phase confirmation walkthrough,
  security posture summary, links to all sub-docs.
- `docs/v0.1-followups.md` now opens with a v0.1 disposition section
  cataloguing every entry as `done` / `v0.2` / `wontfix` per the
  "implemented or explicitly closed" rule.

[Unreleased]: https://github.com/patrice-bour/obsidian-full-mcp/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/patrice-bour/obsidian-full-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/patrice-bour/obsidian-full-mcp/releases/tag/v0.1.0
