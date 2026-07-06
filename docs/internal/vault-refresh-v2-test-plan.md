# vault-refresh v2 — manual test plan (executed)

Manual gate for the `refresh_policy: auto` feature (whitelist + `refresh_apply` +
`refresh-executor`). Automated coverage: server unit suite (738 passed),
executor package suite (29 passed), e2e harness 134/134 including S11
(`refresh_apply` wire path). The scenarios below exercise the real CLI binary
against a throwaway sandbox vault, with the local oMLX + LiteLLM proxy stack up.

Environment: macOS (APFS), oMLX on `:8000`, LiteLLM proxy on `:4000`
(routes `local-thinker`, `cloud-*`), executed 2026-07-06.

## Scenarios

- [x] **S1 — dry-run writes nothing** (2026-07-06)
  `refresh-executor --vault <sandbox> --dry-run` on a stale pinned auto note →
  `[skipped] … — dry-run`, total cost $0.0000. Verified: note body unchanged,
  no `.ohmcp-trash/` created, no dashboard note created.

- [x] **S2 — vault-only apply on an accented filename (NFC/NFD)** (2026-07-06)
  Pinned note `01_Notes/Index des notes — résumé.md` (accented, em dash),
  route `local-thinker`. Real run → `[applied]`, cost $0.0000. Verified on disk:
  body replaced by LLM output, `title` preserved, `refresh_last: '2026-07-06'`,
  `refresh_due: '2026-07-13'` (7d recomputed), `refresh_stale: false`;
  snapshot `.ohmcp-trash/20260706T190722Z-686571ed/...` contains the old body;
  report line appended to `01_Notes/_dashboards/Maj automatiques.md`.

- [x] **S3 — cost cap stops billable tasks, vault-only continues** (2026-07-06)
  Mock proxy returning `x-litellm-response-cost: 0.02` per call,
  `max_usd_per_cycle: 0.01`, two cloud tasks + one vault-only task ordered
  after them. Result: first cloud task `[applied] $0.0200`, second cloud task
  `[anomaly] … cost cap reached`, vault-only task `[applied]` past the cap.
  Report note shows ✅/⚠/✅ accordingly. Total cost $0.0400.

- [x] **S4 — per-task error isolation** (2026-07-06)
  With the proxy's upstream cloud key unavailable, cloud tasks returned clean
  per-task anomalies (`HTTPStatusError: 401/429`) while the vault-only task in
  the same cycle was still applied. No crash, exit code 0.

## Known environment notes

- The LiteLLM proxy must inherit exported API keys
  (`set -a; source ~/.config/.ai-api-keys.env; set +a`) for cloud routes.
- First call to a cold local route can exceed the client timeout while the
  model loads; `LITELLM_TIMEOUT_S` (default 120 s) covers warm operation.
