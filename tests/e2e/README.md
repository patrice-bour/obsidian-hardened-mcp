# tests/e2e — End-to-end harness

Subprocess-based harness that drives every tool of `obsidian-power-mcp`
through the **real stdio MCP wire**, on a freshly seeded test vault.

The unit and in-process integration suites (`tests/unit/`,
`tests/integration/`) cover correctness of the tool implementations.
This harness covers what those tests can't: the actual subprocess
launch, the JSON-RPC framing through `stdio_client`, the disk effects
of atomic writes, the `.opmcp-trash/` snapshot directory layout, and
the audit log written to `~/.obsidian-power-mcp/audit/`.

## Run

```bash
uv run python tests/e2e/run_e2e.py
```

Expected output (last lines):

```
TOTAL                        PASS       100/100
```

Exit code is `0` on full pass, `1` if any scenario has at least one
failing step.

## Optional opt-in: REST API with token (S9)

By default S9 only verifies the no-token branch (`execute_command`
returns `rest_unavailable`). To exercise the with-token branch you need
Obsidian running with the Local REST API plugin enabled, then:

```bash
OBSIDIAN_E2E_REST_TOKEN=<your-plugin-bearer-token> \
  uv run python tests/e2e/run_e2e.py
```

The harness opens a second server with `OBSIDIAN_REST_TOKEN` set and
runs the 2-phase confirm against `app:show-release-notes` (a safe,
side-effect-free Obsidian command).

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
| S7 | validation hooks | `.obsidian-power-mcp.yaml` with `iso_date` + `reserved_tags` + `json_schema` blocks invalid writes |
| S8 | audit | JSONL log grows, every entry has the canonical schema |
| S9 | rest api | no-token branch returns `rest_unavailable`; with-token opt-in |

## How it works

```
run_e2e.py
  │
  ├─ seed_vault.seed(.test-vault/)        # 10 synthetic notes
  ├─ open E2EHarness:
  │     spawn `python -m obsidian_power_mcp --vault .test-vault`
  │     stdio_client + ClientSession  ←  full MCP wire
  │
  ├─ run S0..S6, S9 in the same long-lived session
  ├─ run S7 in a second session (restart needed for hooks auto-load)
  └─ run S8 (audit post-condition, file system inspection)
```

## Files

| File | Role |
|---|---|
| `run_e2e.py` | Orchestrator — spawns harness, runs scenarios, prints table |
| `mcp_harness.py` | `E2EHarness` async context manager wrapping `stdio_client` + `ClientSession`; `CallResult` decoder |
| `seed_vault.py` | `seed(target)` produces the 10-note canonical test vault |
| `audit_inspector.py` | Reads `~/.obsidian-power-mcp/audit/<today>.jsonl`, verifies entry shape |
| `scenarios/_assert.py` | `Step`, `ScenarioReport`, expectation helpers |
| `scenarios/sN_*.py` | One module per scenario, all expose `async def run(h) -> ScenarioReport` |

## Why not pytest

Pytest is reserved for fast in-process tests (`testpaths = ["tests"]`,
discovery rule `test_*.py`). The harness spawns a real subprocess per
session; running it on every `pytest` would slow the dev loop. Files
here are deliberately not prefixed with `test_` so pytest skips them.

## Troubleshooting

**`Connection closed` during S7 boot** — the dropped
`.obsidian-power-mcp.yaml` references a schema file the sandbox cannot
reach. Confirm `_schemas/journal.json` exists at the vault root.

**`payload_mismatch` on phase 2** — phase 1 and phase 2 must have
**every** argument identical (the HMAC token is bound to the params
hash). Forgetting `update_backlinks=True` on one of the two calls is a
classic foot-gun.

**Audit assertions fail / no log file** — the server lazily creates
`~/.obsidian-power-mcp/audit/<today>.jsonl` on the first write. If the
run only contained reads, the file may not exist yet. The harness's
S2/S4 always trigger writes, so this should not happen in practice.

**REST with-token loop fails** — Obsidian must be open AND the Local
REST API plugin enabled. The plugin's bearer token is shown in
its settings panel; export it via `OBSIDIAN_E2E_REST_TOKEN`.
