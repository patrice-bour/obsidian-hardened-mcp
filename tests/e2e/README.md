# tests/e2e — End-to-end harness

Subprocess-based harness that drives every tool of `obsidian-hardened-mcp`
through the **real stdio MCP wire**, on a freshly seeded test vault.

The unit and in-process integration suites (`tests/unit/`,
`tests/integration/`) cover correctness of the tool implementations.
This harness covers what those tests can't: the actual subprocess
launch, the JSON-RPC framing through `stdio_client`, the disk effects
of atomic writes, the `.ohmcp-trash/` snapshot directory layout, and
the audit log written to `~/.obsidian-hardened-mcp/audit/`.

## Run

```bash
uv run python tests/e2e/run_e2e.py
```

Expected output (last lines):

```
TOTAL                        PASS       121/121
```

Exit code is `0` on full pass, `1` if any scenario has at least one
failing step.

### Determinism notes

The `100/100` figure assumes:

- **S9 with-token** is SKIPPED (no `OBSIDIAN_E2E_REST_TOKEN` set). The
  SKIPPED row counts as passing in the totals; setting the env var
  flips it into a real check that depends on Obsidian + Local REST API
  plugin being live.
- **S5 oversize segment** triggers both the server's path validator and
  the OS's per-segment byte limit (255 on macOS / Linux / Windows in
  practice). It can in theory produce different error codes on a
  filesystem with a more permissive limit.
- All other scenarios are deterministic against a freshly seeded
  `.test-vault/`.

## Optional opt-in: REST API with token (S9)

By default S9 only verifies the no-token branch (`execute_command`
returns `rest_unavailable`). To exercise the with-token branch you need
Obsidian running with the Local REST API plugin enabled.

> **Security — don't paste the token inline.** The Local REST API
> bearer token grants write access to your live vault. Most shells
> persist command history (zsh with `SHARE_HISTORY`, bash with the
> default `HISTFILE`), so an inline `OBSIDIAN_E2E_REST_TOKEN=…` ends up
> stored on disk in cleartext. Pick one:
>
> - **direnv** with a gitignored `.envrc` containing `export OBSIDIAN_E2E_REST_TOKEN=…`
> - `read -rs OBSIDIAN_E2E_REST_TOKEN && export OBSIDIAN_E2E_REST_TOKEN`
> - prefix the command with `HISTFILE=/dev/null ` (zsh) or `set +o history` first
>
> The same caveat applies to `OBSIDIAN_REST_TOKEN` if you wire the
> server with REST in your own setup.

Then run:

```bash
uv run python tests/e2e/run_e2e.py
```

(or, accepting the history-leak caveat above, prefix the command with
`OBSIDIAN_E2E_REST_TOKEN=…`).

The harness opens a second server with `OBSIDIAN_REST_TOKEN` set and
runs the 2-phase confirm against `app:show-release-notes` (a safe,
side-effect-free Obsidian command).

## Audit log isolation

By default, the runner sets `OBSIDIAN_AUDIT_DIR` to
`tests/e2e/.runs/audit/` so test runs don't pollute the user's
`~/.obsidian-hardened-mcp/audit/`. Set the env var explicitly to override
(useful for CI tmp paths). The audit inspector honours the same
variable so it always reads what the server wrote.

## Scenarios

| ID | Title | Coverage |
|---|---|---|
| S0 | smoke | server boots, 18 tools registered, vault info matches |
| S1 | read | `list_notes`, `read_note`, `get_frontmatter`, `search_notes` (combined / fulltext / frontmatter / type filter), `resolve_wikilink` |
| S2 | write | `create_note` / `update_note` / `append_to_note` / `patch_note` (dry-run + real, count mismatch, count=0) |
| S3 | frontmatter | `set_frontmatter_field`, `delete_frontmatter_field`, `merge_frontmatter` (shallow + deep), round-trip preservation of comments and quote styles |
| S4 | destructive | `delete_note` / `rename_note` / `move_note` 2-phase + backlink rewrite, token tampering, token reuse |
| S5 | path sandbox | 8 malicious paths × 2 entrypoints (read + create) — all rejected |
| S6 | yaml safety | non-default YAML tag in frontmatter rejected by the parser |
| S7 | validation hooks | `.obsidian-hardened-mcp.yaml` with `iso_date` + `reserved_tags` + `json_schema` blocks invalid writes |
| S8 | audit | JSONL log grows, every entry has the canonical schema |
| S9 | rest api | no-token branch returns `rest_unavailable`; with-token opt-in |
| S10 | vault-refresh | `list_stale_notes` scan finds the seeded stale contract; `mark=true` stamps it; second `mark=true` run is idempotent |
| S11 | refresh_apply | apply OK on the seeded pinned auto note (body replaced, `refresh_last` advanced, snapshot present); apply refused (`VALIDATION_FAILED`) on a flag-policy note |

## How it works

```
run_e2e.py
  │
  ├─ seed_vault.seed(.test-vault/)        # 12 synthetic notes
  ├─ open E2EHarness:
  │     spawn `python -m obsidian_hardened_mcp --vault .test-vault`
  │     stdio_client + ClientSession  ←  full MCP wire
  │
  ├─ run S0..S6, S10, S11, S9 in the same long-lived session
  ├─ run S7 in a second session (restart needed for hooks auto-load)
  └─ run S8 (audit post-condition, file system inspection)
```

## Files

| File | Role |
|---|---|
| `run_e2e.py` | Orchestrator — spawns harness, runs scenarios, prints table |
| `mcp_harness.py` | `E2EHarness` async context manager wrapping `stdio_client` + `ClientSession`; `CallResult` decoder |
| `seed_vault.py` | `seed(target)` produces the 10-note canonical test vault |
| `audit_inspector.py` | Reads `<OBSIDIAN_AUDIT_DIR or ~/.obsidian-hardened-mcp/audit/>/<today>.jsonl`, verifies entry shape |
| `scenarios/_assert.py` | `Step`, `ScenarioReport`, expectation helpers |
| `scenarios/sN_*.py` | One module per scenario, all expose `async def run(h) -> ScenarioReport` |

## Why not pytest

Pytest is reserved for fast in-process tests (`testpaths = ["tests"]`,
discovery rule `test_*.py`). The harness spawns a real subprocess per
session; running it on every `pytest` would slow the dev loop. Files
here are deliberately not prefixed with `test_` so pytest skips them.

## Troubleshooting

**`Connection closed` during S7 boot** — the dropped
`.obsidian-hardened-mcp.yaml` references a schema file the sandbox cannot
reach. Confirm `_schemas/journal.json` exists at the vault root.

**`payload_mismatch` on phase 2** — phase 1 and phase 2 must have
**every** argument identical (the HMAC token is bound to the params
hash). Forgetting `update_backlinks=True` on one of the two calls is a
classic foot-gun.

**Audit assertions fail / no log file** — the server lazily creates
the audit log (under `OBSIDIAN_AUDIT_DIR`, falling back to
`~/.obsidian-hardened-mcp/audit/`) on the first write. If the run only
contained reads, the file may not exist yet. The harness's S2/S4
always trigger writes, so this should not happen in practice.

**REST with-token loop fails** — Obsidian must be open AND the Local
REST API plugin enabled. The plugin's bearer token is shown in
its settings panel; export it via `OBSIDIAN_E2E_REST_TOKEN`.

## Destructive op coverage

E2E scenarios for `delete_note` and `execute_command` cover Phase 1
(token issuance) and dry-run paths only. The Phase 2 elicit flow
(M6-11) requires an elicit-capable client (Claude Desktop, Claude
Code) to render the confirmation dialog. Phase 2 is therefore tested
at the wrapper level via `tests/integration/test_server_elicit.py`
with mocked `Context.elicit`.
