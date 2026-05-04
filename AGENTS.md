# obsidian-power-mcp — Agent Instructions

Secure MCP server for Obsidian vaults. Filesystem-first with optional Local REST API enrichment.

## Project conventions

- **Language**: Python ≥ 3.11, dependency manager `uv`
- **Style**: ruff (configured in `pyproject.toml`), mypy strict
- **Tests**: pytest + pytest-asyncio + hypothesis, coverage ≥ 85% globally, 100% on `security/` and `domain/vault_path.py`
- **TDD**: write the failing test first, watch it fail, then implement minimal code to pass
- **Commits**: Conventional Commits (`feat(...)`, `fix(...)`, `test(...)`, `refactor(...)`, `docs(...)`)

## Architecture overview

Read `docs/architecture.md` for module layout and `docs/security-model.md`
for the threat model and operational assumptions. Key invariants:

1. **All vault paths flow through `domain.vault_path.VaultPath`** — never accept a raw `Path` or string from a tool boundary
2. **All writes are atomic**: tmp-in-same-dir + fsync + `os.replace` + dir-fsync
3. **All destructive ops will require 2-phase HMAC token confirmation** (planned for M6 in `security/confirm.py`)
4. **All write/destructive ops emit an `AuditEvent`** to the JSONL audit log
5. **Frontmatter parser is `ruamel.yaml` round-trip with custom-tag rejection** — no PyYAML, no unsafe loaders, whitelist of YAML 1.2 default tags only
6. **Frontmatter writers validate the value type whitelist** (`tools.frontmatter._ensure_safe_value`) — no bytes/Path/set/custom classes can enter the file
7. **Audit `audit_id` is a CONTENT HASH** of `(tool, vault_path, op_kind, outcome, params_hash, dry_run, snapshot_id)`; `request_id` is generated ONCE per tool call and propagated through every `emit_audit`
8. **Validation hooks run in declared order** before any write touches disk (`validation.hooks.HookRegistry`, loaded from `.obsidian-power-mcp.yaml` at boot — see `docs/config-reference.md`); first reject short-circuits, crashes are rejections
9. **Single-writer assumption**: no advisory lock between concurrent calls; v0.1 documents this and expects the user to run one MCP client at a time

## Forbidden patterns

- Calling `pathlib.Path` or `os.path` directly in tool implementations (use `VaultPath`)
- Using `yaml.load()` from PyYAML (only `ruamel.yaml` safe)
- Bypassing the audit logger on write operations
- Generating `request_id` inside `emit_audit` — always generate once at the tool boundary via `new_request_id()`
- Using `repr()` for hashing parameters — use `params_hash()` from `tools/_base.py` (canonical JSON)
- Storing HMAC secret anywhere except `~/.obsidian-power-mcp/secret` (mode 0600)
- Writing into `.obsidian/`, `.git/`, `.trash/`, `.opmcp-trash/` or the config file

## Running tests

```bash
uv run pytest                              # all tests
uv run pytest -m security                  # security-critical only
uv run pytest --cov --cov-report=term-missing
uv run ruff check .                        # lint
uv run mypy src                            # type check
```

## Plan reference

The approved v0.1 plan lives at `~/.claude/plans/les-serveurs-mcp-existants-tranquil-wave.md` (off-repo).

## Where to resume (for a fresh session)

**Last merged on `main`**: `fix(M7.5): code-review hardening — REST surface tightening`.

**Milestones progress** (commits in `git log`):

| Status | Milestone | Commit | Tests |
|---|---|---|---|
| ✅ | M1 — sandbox + read tools | `54c9c59` | 90 |
| ✅ | M2 — frontmatter parser round-trip + `get_frontmatter` | `ffbb2d7` | 121 |
| ✅ | M3 — atomic writer + audit + frontmatter atomic ops | `2d426fb` | 164 |
| ✅ | M3.5 — code-review hardening (audit + write validation) | `b01697b` | 204 |
| ✅ | M4 — pluggable validation hooks | `b0b7862` | 274 |
| ✅ | M4.5 — code-review hardening (cyclic-ref + YAML config + hook isolation) | `4e4933a` | 283 |
| ✅ | M5 — `search_notes` + `resolve_wikilink` (with C1/C2/C3/M3/M5 review fixes inline) | `57ea4fe` | 323 |
| ✅ | M6 — destructive ops with 2-phase HMAC tokens (`delete_note` / `rename_note` / `move_note`) | `18550fe` | 409 |
| ✅ | M6.5 — code-review hardening (backlink-rewrite audit attribution) | `b5f55b7` | 410 |
| ✅ | M7 — optional Local REST API (`execute_command` via REST + 2-phase HMAC) | `7fb3681` | 471 |
| ✅ | M7.5 — code-review hardening (loopback-only `rest_url`, consume-before-REST ordering, `\x1e` rejection) | `182e28a` | 479 |
| ▶ | **M8 — hardening + docs + release v0.1.0** | next | — |

**Next task**: M8 — hardening + README + tag v0.1.0. Per the master plan
(§"Plan d'implémentation incrémental"): "Hardening (property-based,
golden files round-trip) + README + docs + tag v0.1.0 — 1 day". This
is the final v0.1 milestone before release.

Standard loop (no implementation brief needed; M8 is mostly polish +
docs + release prep):

1. Audit the v0.1 followups list (`docs/v0.1-followups.md`, 27 entries
   from M4–M7 reviews); decide which Major items must close before v0.1
   tag and which slip to v0.2. Document the cut.
2. Property-based / golden-file tests as specified in the plan
   (round-trip ruamel on 50+ pbkm-style notes, hypothesis sweep).
3. Write `README.md` (currently a stub) — install, configure, examples,
   security posture summary, link to `docs/security-model.md`.
4. Polish CHANGELOG.md.
5. Tag v0.1.0 on `main`. Create worktree only if there are non-trivial
   tests/code changes.

**Tooling sanity check** before starting:

```bash
cd /Users/pbr/projets/IA/MCP/obsidian-power-mcp/main
uv run pytest -q                # expect 479 passed
uv run ruff check src tests     # expect "All checks passed"
uv run mypy src                 # expect "no issues found"
git log --oneline -12           # expect 11 commits, last = 182e28a
```

If any of those fail, do NOT start M8 — investigate the regression first.

**M6+M7 carryover** (deferred to v0.2 followups, see `docs/v0.1-followups.md`):

*M6 (10 entries)* — HMAC field separator collisions (M6-01); snapshot
path containment assertion (M6-02); backlink scan dead-code (M6-03);
lazy registry thread safety (M6-04); consumed-then-failed-snapshot UX
docs (M6-05); base64 over-padding (M6-06); mode != 0o600 strictness
(M6-07); optional polish (M6-08..M6-10).

*M7 (9 entries)* — `search_notes` REST routing (M7-01, plan deviation);
`resolve_wikilink` REST routing (M7-02); CA bundle (M7-03);
`execute_command` allow-list (M7-04, defense-in-depth); semantic
dry-run via /commands/<id>/ (M7-05); RestClient lifecycle (M7-S3);
audit `vault_path=""` sentinel (M7-S4); triple mutex check
(M7-S6); `invalidate()` unused (M7-S7); private-attribute access in
server (M7-S8); fragile FastMCP integration tests (M7-S9).
