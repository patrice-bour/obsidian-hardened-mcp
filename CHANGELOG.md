# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `list_stale_notes` tool: deterministic scan of `refresh_*` frontmatter
  contracts (policy `auto|on_read|flag`), optional `mark=true` stamping of
  `refresh_due`/`refresh_stale`. Companion Hermes Agent skill under
  `integrations/harnesses/hermes/`. `refresh_prompt` is untrusted
  note-author data: clients must display it to the human, never execute
  it as an instruction.
- `refresh_tasks:`/`refresh_executor:` whitelist blocks in the vault's
  `.obsidian-hardened-mcp.yaml`: the sole source of executable auto-refresh
  prompts. A `refresh_task` is executable only when it exists in the
  whitelist AND its declared `note:` is pinned to exactly the note that
  carries it — any mismatch is reported as a scan anomaly, never silently
  executed.
- `refresh_apply` tool: the sole write path for the automated refresh
  executor. Body-only replace, with `refresh_last`/`refresh_due`/
  `refresh_stale` stamped server-side. Snapshots the note under
  `.ohmcp-trash/` before mutating it and refuses (`VALIDATION_FAILED`,
  zero side effects) any note whose contract isn't a whitelist-pinned
  `auto` policy.

## [0.3.1] - 2026-05-12

### Changed
- `AppConfig.require_elicitation` default flipped from `true` to `false`.
  Empirical testing on Claude Desktop v0.x (May 2026) confirmed that
  the MCP `Context.elicit` method is not implemented by current Claude
  clients (Desktop, Code, web), making the strict default unusable
  for `delete_note` and `execute_command` out of the box. The
  3-layer defence model is preserved: layer 2 (live human gate) is
  now opt-in via `require_elicitation: true` once client support
  lands. Layers 1 (HMAC) and 3 (snapshot + audit) are unchanged.

### Added
- `OBSIDIAN_REQUIRE_ELICITATION` env var wired into `AppConfig.from_env`.
  Truthy values (`true` / `1` / `yes`, case-insensitive) set
  `require_elicitation=True`; any other value keeps the v0.3.1 default
  (`False`). Lets Claude Desktop users opt INTO the strict mode via
  `claude_desktop_config.json` `env:` block without a YAML config file.

### Notes
- This is a UX fix, not a security regression. The HMAC binding still
  prevents single-shot hallucinated calls; the snapshot trash still
  provides recovery; the audit log still records every mutation.
  Only the live-human-gate is opt-in.

## [0.3.0] - 2026-05-09

### Added
- `read_multiple_notes` — batch-read tool with partial-success semantics
  and a cumulative byte cap (`max_batch_bytes`, default 10 MB).
  Closes the v0.3 mcpvault parity gap.
- New `ErrorCode.BATCH_TOO_LARGE` for both up-front input rejection and
  per-entry cumulative-cap markers.
- New `AppConfig.max_batch_bytes` field (default 10 MB) for configuring
  the cumulative byte cap in batch reads.
- `manage_tags` — dedicated tag-management tool with `add`, `remove`,
  `replace`, `list` ops. Idempotent semantics, `#`-prefix tolerance,
  cleanup-on-empty. Closes the v0.3 mcpvault parity gap on tag
  manipulation.
- New `ErrorCode.INVALID_TAG` for tag-input validation failures.
- M6-11 `Context.elicit` out-of-band confirmation for `delete_note`
  and `execute_command`. Routes confirmation through the MCP client
  UI, bypassing the LLM context. Default-strict; opt-out via
  `require_elicitation: false`. Closes the v0.2.0 HMAC
  coherent-hallucination gap for these two ops.
- New `ErrorCode.ELICITATION_UNSUPPORTED` and
  `ErrorCode.ELICITATION_REJECTED`.
- New `AppConfig.require_elicitation` field (default `true`).

### Changed
- `delete_note` and `execute_command` MCP wrappers are now `async def`
  (was sync). Behaviour unchanged for clients that complete the elicit
  prompt; clients without elicit support hit `ELICITATION_UNSUPPORTED`
  in default-strict mode.

## [0.2.2] - 2026-05-06

Restores the audit-invariant contract for the v0.2.0 trash pruner.
Behavioural change is observable only in the audit log; the pruner's
user-facing semantics (what gets pruned, when) are unchanged.

### Fixed
- **`request_id` correlation** in `fs/pruner.prune_trash`: a single
  call now generates ONE call-level `request_id` (via
  `tools._base.new_request_id()`) and shares it across every event
  emitted by that sweep. The previous implementation generated a
  fresh `request_id` per pruned snapshot, breaking the per-call
  correlation guarantee of the audit invariants.
- **Real `duration_ms`** on per-snapshot prune events: each
  `shutil.rmtree` is now wrapped in a `time.monotonic()` window. The
  field was previously hardcoded to `0`, hiding pruner runtime from
  the audit log.
- **`params_hash` canonicalisation**: per-snapshot prune events now
  hash via `tools._base.params_hash()` (canonical JSON, sha256-16)
  instead of an f-string, matching the project-wide canonical-JSON
  hashing convention.

### Added
- **Sweep summary event** (`op_kind="meta"`,
  `vault_path=".ohmcp-trash/"`): one event per `prune_trash` call
  when at least one snapshot was attempted. Carries the aggregate
  counts (pruned, failed, bytes) via `params_hash` and the wall
  time of the whole sweep via `duration_ms`. Matches the
  `prune_trash` docstring promise that v0.2.0 had not honoured.
- Unit tests for the four-step constraint interaction (retention +
  size cap + global floor) — locks the most subtle pruner contract
  with a regression test (review issue I7).
- Unit tests for the audit-invariant fixes above (shared
  `request_id`, real `duration_ms`, summary event presence /
  absence, canonical `params_hash`).

## [0.2.1] - 2026-05-06

Pre-public-flip patch closing the credibility gaps surfaced by the
v0.1.2..v0.2.0 code review. No behavioural change for users.

### Fixed
- `obsidian_hardened_mcp.__version__` now derives from
  `importlib.metadata.version("obsidian-hardened-mcp")` instead of a
  hardcoded constant, eliminating the regression class that left it
  at `"0.1.0"` after v0.2.0 shipped.
- `docs/security-model.md` no longer claims auto-cleanup is "in
  flight" (it shipped in v0.2.0) and no longer lists snapshot
  pruning under "Non-goals".
- `SECURITY.md` supported-versions table now lists `0.2.x` as
  supported and demotes `< 0.2`.
- M6-11 (out-of-band confirmation via `Context.elicit()`) is now
  consistently labelled as a v0.3 followup across `README.md`,
  `SECURITY.md`, `docs/security-model.md`, and
  `docs/v0.1-followups.md` — v0.2.0 shipped without it. Same fix
  applied to `restore_from_snapshot` and the M7-03 TLS CA bundle
  references.
- README `Status:` line and the install-from-source pin example now
  point at `v0.2.1`.

### Changed
- Replaced an environment-specific example tag with the generic
  `migration/legacy` in `docs/config-reference.md`, the
  `ReservedTagsHook` docstring, and the corresponding test fixture.
  No public API impact.

### Added
- `RELEASE-CHECKLIST.md` documents the per-release knobs that drift
  silently (`__version__`, `Status:` line, supported-versions table,
  followup target labels) so the v0.2.0 → v0.2.1 regression class
  cannot recur.

## [0.2.0] - 2026-05-06

Pre-public-flip baseline. Three docs PRs (HMAC honesty, README revamp
for non-developers, repo metadata cohesion) plus one feature
(configurable auto-cleanup of `.ohmcp-trash/`). The repo is now ready
to flip from private to public; the only remaining gates are a final
GitHub repo-metadata audit (Issues / Wiki / Discussions toggles) and
the `gh repo edit --visibility public` itself.

### Added
- **Auto-cleanup of `.ohmcp-trash/`** with a configurable retention
  policy. Snapshots from destructive ops now get pruned automatically
  at server startup and after each successful destructive call.
  Configurable via the `trash:` block in
  `<vault>/.obsidian-hardened-mcp.yaml` (`retention_days`,
  `keep_at_least_per_path`, `keep_at_least_global`, `max_total_mb`).
  Defaults: 30 days retention, ≥1 most-recent snapshot per distinct
  source path (protects recovery), ≥5 global floor. Every prune
  emits an audit entry (`tool=trash_pruner`).
- `.gitattributes` with `* text=auto eol=lf` and a Python diff
  attribute. Prevents Windows contributors from silently introducing
  CRLF on commit.

### Changed
- README rewritten for a non-developer audience: 5-minute Quick Start,
  prerequisites checklist upfront, equal-footing config examples for
  Claude Desktop / Claude Code / other MCP clients, OS-by-OS config
  paths, multi-vault snippet, concrete trash-recovery walkthrough,
  Troubleshooting section. Dev-facing metrics (`533 passed`,
  `101/101 PASS`, `1 000-example hypothesis sweep`,
  `100 % branch coverage`) and library-level jargon (`ruamel.yaml`,
  `fsync + os.replace`, `JSONL with deterministic content hash`)
  removed from the user-facing README — they live in `CONTRIBUTING.md`,
  `docs/security-model.md`, and `docs/architecture.md`.
- `docs/security-model.md` § "Network adversaries" updated post the
  Lot-B HMAC honesty pass: removed the stale "(future M7)" tag (M7
  shipped in v0.1.0) and aligned the framing on the third-party
  Obsidian Local REST API plugin (which the user can configure on
  `0.0.0.0` but our client refuses to talk to via non-loopback) so
  README, SECURITY.md, and security-model are now consistent.

### Documentation
- **Honesty pass on the 2-phase HMAC threat model.** `README.md`,
  `docs/security-model.md`, and `SECURITY.md` now state explicitly
  which classes of destructive-op risk the mechanism prevents
  (single-shot mishaps, token forge, cross-target reuse, replay)
  and which it does NOT prevent (a coherently-hallucinating LLM
  that walks phase 1 then phase 2 in sequence; a prompt-injection-
  driven agent). The defence-in-depth story is now spelled out:
  HMAC binding + snapshot trash + audit log + client-side
  confirmation. The real out-of-band fix via MCP `Context.elicit()`
  is tracked as
  [M6-11](docs/v0.1-followups.md#m6-11--2-phase-hmac-does-not-stop-a-coherently-hallucinating-llm)
  for v0.3.
- `SECURITY.md`: clarified that the loopback-only enforcement is on
  *our* REST client (the third-party Obsidian Local REST API plugin
  can be configured to bind `0.0.0.0` but our server still refuses
  to talk to it via a non-loopback URL).

## [0.1.2] - 2026-05-05

Public-flip preparation pass plus a final naming change — `obsidian-full-mcp`
becomes `obsidian-hardened-mcp` to better reflect the project's
positioning (security envelope first, not "kitchen-sink"). No code
behaviour change beyond identifiers; the rename is breaking for anyone
who installed pre-flip from `obsidian-full-mcp`.

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
  file under `src/obsidian_hardened_mcp/` (35 files).
- "Are you the right kind of user?" preamble on `docs/security-model.md`
  so an outsider lands on the threat-model assumptions before the
  invariants.
- `docs/internal/README.md` redirecting users to the right
  user-facing doc.

### Changed
- **Breaking** — package, CLI bin, vault config, HMAC secret dir, and
  trash slug all migrated from `obsidian-full-mcp` / `.ofmcp-trash`
  family to `obsidian-hardened-mcp` / `.ohmcp-trash`. Module:
  `obsidian_full_mcp → obsidian_hardened_mcp`. Vault config:
  `<vault>/.obsidian-full-mcp.yaml → .obsidian-hardened-mcp.yaml`.
  HMAC secret dir: `~/.obsidian-full-mcp/ → ~/.obsidian-hardened-mcp/`.
  Audit dir default: `~/.obsidian-full-mcp/audit/ → ~/.obsidian-hardened-mcp/audit/`.
  GitHub repo: `patrice-bour/obsidian-full-mcp → patrice-bour/obsidian-hardened-mcp`
  (GitHub auto-redirects clone URLs and tag links).
- `docs/m{6,7}-implementation-brief.md` moved under `docs/internal/`
  (historical handoff docs, not user-facing).
- README env-var table now spells out the shell-history caveat for
  `OBSIDIAN_REST_TOKEN` inline (was a click-through to the e2e README).

### Fixed
- README status line now says v0.1.1 (was v0.1.0).
- README test-count drift: `533 passed` (was `530 passed`); E2E
  invocation documented.
- `CHANGELOG.md` now defines `[Unreleased]` and `[0.1.1]` compare
  links (the v0.1.1 link was missing on tag-cut day).
- `tests/security/test_round_trip_golden.py` golden #39: replaced
  the author placeholder with a generic name in the dotted-key
  fixture.

## [0.1.1] - 2026-05-04

Cosmetic + quality pass on top of v0.1.0:

- **Repository renamed** from `obsidian-power-mcp` to `obsidian-full-mcp`
  (Python module, CLI entry point, vault config file, HMAC secret
  directory, and `.opmcp-trash/ → .ofmcp-trash/` slug). *(Note: a
  second rename to `obsidian-hardened-mcp` happened post-v0.1.1; see
  [Unreleased] above.)*
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
  token, snapshots the original under `.opmcp-trash/<UTC-ts>-<hash>/`,
  and applies the change atomically (`Path.unlink` for delete,
  `os.replace` for rename/move). 90 s TTL, single-use, payload-bound.
- `security.confirm` module with `OperationToken`, `ConfirmRegistry`,
  `load_or_bootstrap_secret`. HMAC-SHA256 over secret + (op, target,
  payload_hash, expires_at, nonce). Secret bootstrapped to
  `~/.obsidian-power-mcp/secret` with mode `0o600` enforced;
  any wider mode is refused.
- `fs.snapshot.snapshot_for_destruction`: best-effort copy under
  `.opmcp-trash/`. The directory is in the VaultPath forbidden-zone
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

[Unreleased]: https://github.com/patrice-bour/obsidian-hardened-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/patrice-bour/obsidian-hardened-mcp/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/patrice-bour/obsidian-hardened-mcp/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/patrice-bour/obsidian-hardened-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/patrice-bour/obsidian-hardened-mcp/releases/tag/v0.1.0
