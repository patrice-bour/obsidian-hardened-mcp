# obsidian-full-mcp — Agent Instructions

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
8. **Validation hooks run in declared order** before any write touches disk (`validation.hooks.HookRegistry`, loaded from `.obsidian-full-mcp.yaml` at boot — see `docs/config-reference.md`); first reject short-circuits, crashes are rejections
9. **Single-writer assumption**: no advisory lock between concurrent calls; v0.1 documents this and expects the user to run one MCP client at a time

## Forbidden patterns

- Calling `pathlib.Path` or `os.path` directly in tool implementations (use `VaultPath`)
- Using `yaml.load()` from PyYAML (only `ruamel.yaml` safe)
- Bypassing the audit logger on write operations
- Generating `request_id` inside `emit_audit` — always generate once at the tool boundary via `new_request_id()`
- Using `repr()` for hashing parameters — use `params_hash()` from `tools/_base.py` (canonical JSON)
- Storing HMAC secret anywhere except `~/.obsidian-full-mcp/secret` (mode 0600)
- Writing into `.obsidian/`, `.git/`, `.trash/`, `.ofmcp-trash/` or the config file

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

**Last merged on `main`**: `chore(release): v0.1.1` — cosmetic + quality pass on
top of v0.1.0 (repo renamed `power → full`, E2E harness added,
post-publication code review fixes 4/4 MUST + 13/13 SHOULD).
**v0.1.1 tagged.** v0.1.0 tag preserved on `f24827b`.

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
| ✅ | **M8 — hardening + README + CHANGELOG + golden round-trip + v0.1.0 tag** | `f24827b` | 530 |
| 🎉 | **v0.1.0 tagged** | tag `v0.1.0` on `f24827b` | — |
| 🎉 | **v0.1.1 tagged** — E2E harness + repo rename + code-review pass | tag `v0.1.1` (post-publication) | 533 + 101 E2E |

**Next task**: v0.2 — pick up the v0.2 backlog in
`docs/v0.1-followups.md` (36 entries). The master plan covered v0.1;
v0.2 priorities (informally): ripgrep-backed `search_notes` with TTL
index cache (M5-01 + M5-02), `path_routing` built-in hook (M4-01),
`execute_command` allow-list (M7-04), TLS CA bundle (M7-03), and the
`search_notes` REST routing that v0.1 deferred (M7-01). Decide a
proper v0.2 plan + brief before opening the next worktree.

**Sanity check** to confirm a clean v0.1.1 base:

```bash
cd /Users/pbr/projets/IA/MCP/obsidian-full-mcp/main
uv run pytest -q                                    # expect 533 passed
uv run python tests/e2e/run_e2e.py                  # expect 101/101 PASS
uv run ruff check src tests                         # expect "All checks passed"
uv run mypy src                                     # expect "no issues found"
git tag -l                                          # expect 'v0.1.0' and 'v0.1.1'
```

**v0.2 backlog** (M8 audit, full table in `docs/v0.1-followups.md` § v0.1.0 disposition): 4 done, 36 v0.2, 2 wontfix. Top targets when v0.2 opens:

- M5-01 + M5-02 — ripgrep-backed `search_notes` with TTL index cache.
- M4-01 — `path_routing` built-in hook (last of the three planned).
- M7-04 — `execute_command` allow-list (defense in depth).
- M7-03 — TLS CA bundle option (relax loopback constraint).
- M7-01 / M7-02 — REST routing for `search_notes` / `resolve_wikilink`.
- M6-04 — lazy registry init thread safety.
- M4-13 / M4-14 — config hot-reload + clean `ConfigError` exit.
