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

**Last merged on `main`**: `fix(M6.5): code-review hardening — backlink-rewrite audit attribution`.

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
| ▶ | **M7 — optional Local REST API integration** | next | — |
| ⏳ | M8 — hardening + docs + release v0.1.0 | — | — |

**Next task**: implement M7. The plan section to consult is "M7 — Local
REST API enrichment" in `~/.claude/plans/les-serveurs-mcp-existants-tranquil-wave.md`.
Standard loop:

1. Write an `m7-implementation-brief.md` analogue to the M6 brief
   before starting. (M5/M6 both proved the brief-first pattern pays off.)
2. Create worktree `feat/m7-rest`.
3. TDD each piece (failing test → impl → green → repeat).
4. After implementation completes, run an independent code review via
   the `superpowers:code-reviewer` Agent, scoped to the M7 diff only.
5. Fix **Critical / MUST-DO** findings inline in `M7.5`; track
   everything else in [`docs/v0.1-followups.md`](./docs/v0.1-followups.md)
   (already 18 entries from M4–M6 reviews).
6. Merge to `main` via fast-forward, remove the worktree, delete the branch.

**Tooling sanity check** before starting:

```bash
cd /Users/pbr/projets/IA/MCP/obsidian-power-mcp/main
uv run pytest -q                # expect 410 passed
uv run ruff check src tests     # expect "All checks passed"
uv run mypy src                 # expect "no issues found"
git log --oneline -10           # expect 9 commits, last = b5f55b7
```

If any of those fail, do NOT start M7 — investigate the regression first.

**M6 carryover** (deferred to v0.2 followups, see `docs/v0.1-followups.md`):
- M6-01 HMAC field separator collisions (defense-in-depth).
- M6-02 Snapshot path containment assertion.
- M6-03 Backlink scan src→dest remap dead code (post-rename re-scan supersedes).
- M6-04 Lazy registry init thread safety.
- M6-05 Document consumed-then-failed-snapshot tokens are unrecoverable.
- M6-06 `_verify_hmac` over-pads base64 input.
- M6-07 Mode check `mode != 0o600` rejects `0o400`.
- M6-08..M6-10 Optional polish (Literal trim, comment tighten, snapshot stress test).
