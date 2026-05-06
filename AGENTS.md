# obsidian-hardened-mcp — Agent Instructions

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
8. **Validation hooks run in declared order** before any write touches disk (`validation.hooks.HookRegistry`, loaded from `.obsidian-hardened-mcp.yaml` at boot — see `docs/config-reference.md`); first reject short-circuits, crashes are rejections
9. **Single-writer assumption**: no advisory lock between concurrent calls; v0.1 documents this and expects the user to run one MCP client at a time

## Forbidden patterns

- Calling `pathlib.Path` or `os.path` directly in tool implementations (use `VaultPath`)
- Using `yaml.load()` from PyYAML (only `ruamel.yaml` safe)
- Bypassing the audit logger on write operations
- Generating `request_id` inside `emit_audit` — always generate once at the tool boundary via `new_request_id()`
- Using `repr()` for hashing parameters — use `params_hash()` from `tools/_base.py` (canonical JSON)
- Storing HMAC secret anywhere except `~/.obsidian-hardened-mcp/secret` (mode 0600)
- Writing into `.obsidian/`, `.git/`, `.trash/`, `.ohmcp-trash/` or the config file

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

**TL;DR for a Claude Code session resuming a long-running thread**:
the project is at **v0.2.0**, ready to flip from private to public on
GitHub. The reference plan for the pre-public-flip work lives at
`/Users/pbr/.claude/plans/avant-de-publier-sur-zippy-simon.md`. Read
that file, then come back here for the milestones / sanity check.
The user (Patrice) is preparing to flip; ask which step to pick up
before doing anything visible (push, gh repo edit, PyPI publish).

**Last merged on `main`**: `chore(release): v0.2.0` — pre-public-flip
baseline. Contains the HMAC honesty pass (doc-only), the README
revamp for non-developers, the auto-cleanup of `.ohmcp-trash/` with
configurable retention policy, and a few extras (`.gitattributes`,
docs coherence post Lot-B). **v0.2.0 tagged.** v0.1.0 (`f24827b`),
v0.1.1, v0.1.2 tags preserved.

**State of the public-flip plan** (per
`/Users/pbr/.claude/plans/avant-de-publier-sur-zippy-simon.md`):

- ✅ PR1 — Lot B (HMAC honesty doc) — commit `d86a608`
- ✅ PR2 — Lot A (README revamp) — commit `cf6bc50`
- ✅ PR3 — Lot C (trash auto-cleanup) — commit `2a6ae2b`
- ✅ Pre-flip extras #1 (`.gitattributes`), #2 (git log audit, clean),
  #4 (docs coherence) — commit `f2c8731`
- ⏳ Pre-flip extra #3 — verify GitHub repo metadata
  (Issues ON / Wiki OFF / Discussions OFF). User deferred this.
- ⏳ The actual flip itself:
  ```bash
  gh repo edit patrice-bour/obsidian-hardened-mcp --visibility public --accept-visibility-change-consequences
  gh api repos/patrice-bour/obsidian-hardened-mcp/branches/main/protection -X PUT --input - <<'JSON'
  { "required_status_checks": null, "enforce_admins": false,
    "required_pull_request_reviews": { "required_approving_review_count": 0,
      "require_code_owner_reviews": false, "dismiss_stale_reviews": false },
    "restrictions": null, "required_linear_history": true,
    "allow_force_pushes": false, "allow_deletions": false }
  JSON
  ```
- ⏳ Post-flip: PyPI publish (the longer-running 1→4 plan from the
  earlier conversation). User has no PyPI account yet — they'll
  create one + 2FA + token, then we wire `uv publish` (token-based
  one-shot first; Trusted Publishers via GitHub Actions later).
- ⏳ Optional v0.3 / mcpvault-parity gaps: `read_multiple_notes`
  (batch read) and `manage_tags` (dedicated tag tool, sugar over
  `merge_frontmatter`).

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
| 🎉 | **v0.1.1 tagged** — E2E harness + repo rename `power → full` + code-review pass | tag `v0.1.1` | 533 + 101 E2E |
| 🎉 | **v0.1.2 tagged** — public-flip prep (SECURITY/CONTRIBUTING/CoC/templates/SPDX/uvx docs) + repo rename `full → hardened` | tag `v0.1.2` | 533 + 101 E2E |
| 🎉 | **v0.2.0 tagged** — HMAC honesty + README revamp + trash auto-cleanup + pre-flip extras | tag `v0.2.0` | 558 + 101 E2E |

**Next task** (in this exact order):

1. Verify GitHub repo metadata (Issues ON / Wiki OFF / Discussions OFF /
   Projects OFF). Pre-flip extra #3 from
   `/Users/pbr/.claude/plans/avant-de-publier-sur-zippy-simon.md`.
2. Flip GitHub repo from private to public + activate branch
   protection on `main` (commands in the `Where to resume` block
   above).
3. PyPI publish (the user must first create a PyPI account + 2FA +
   generate a token; then `UV_PUBLISH_TOKEN=… uv build && uv publish`
   one-shot for now, Trusted Publishers via GitHub Actions later).
4. Optional v0.3 / mcpvault-parity: `read_multiple_notes` (batch
   read), `manage_tags` (dedicated tag tool, sugar over
   `merge_frontmatter`).

**v0.3 backlog** (carried over from v0.1-followups.md): ripgrep-backed
`search_notes` with TTL index cache (M5-01 + M5-02), `path_routing`
built-in hook (M4-01), `execute_command` allow-list (M7-04), TLS CA
bundle (M7-03), `search_notes` REST routing (M7-01), and **M6-11 —
out-of-band confirmation via MCP `Context.elicit()`** (the real fix
for the HMAC coherent-hallucination gap surfaced in v0.2.0).

**Sanity check** to confirm a clean v0.2.0 base:

```bash
cd <repo-root>
uv run pytest -q                                    # expect 558 passed
uv run python tests/e2e/run_e2e.py                  # expect 101/101 PASS
uv run ruff check src tests                         # expect "All checks passed"
uv run mypy src                                     # expect "no issues found"
git tag -l                                          # expect 'v0.1.0', 'v0.1.1', 'v0.1.2', 'v0.2.0'
```

**Detailed backlog** lives in `docs/v0.1-followups.md` (36 v0.2/v0.3
entries from the M8 audit, plus the new M6-11 from the HMAC honesty
pass).
