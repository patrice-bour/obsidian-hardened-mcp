# Design — M6-11 `Context.elicit()` out-of-band confirmation (v0.3.0)

**Status**: approved
**Date**: 2026-05-08
**Tracking**: v0.3 task #3 (real fix for HMAC coherent-hallucination gap)
**Author**: brainstorming session 2026-05-08

## Context and motivation

The v0.2.0 honesty pass surfaced a residual gap in the 2-phase HMAC
confirmation: a coherently-hallucinating LLM can walk both phases
(token round-trips through its own context). The cryptographic
binding only stops a *single-shot* mishap; it does not stop an agent
that consciously chains the two calls.

The mitigation, already documented as v0.3 plan in
`docs/v0.1-followups.md` (entry M6-11), is to wrap Phase 2 with
`Context.elicit` — an MCP capability that routes a confirmation
prompt **through the client UI** (Claude Desktop, Claude Code) rather
than through the LLM context. The user's response is provided to the
server directly by the client; the LLM cannot fabricate it.

This is the third and final v0.3.0 feature, alongside
`read_multiple_notes` (PR #6) and `manage_tags` (PR #8).

## Non-goals

- Not a replacement for the 2-phase HMAC. Both layers ship together
  (defense in depth: crypto binding + live human gate + snapshot
  recovery).
- Not yet rolled out to `rename_note` and `move_note`. Those remain
  HMAC-only in v0.3.0 (their snapshot-restore path makes them lower
  risk; v0.3.x can extend the pattern).
- Not a UX framework. The elicit message is plain text + a single
  boolean field. Richer dialogs (typed echo, free-form reason) are
  v0.3.x candidates.
- Not a replacement for Phase 1. Phase 1 still issues the HMAC token
  on the first call; the elicit fires only on Phase 2.

## Architecture

### Defense-in-depth stack

```
┌────────────────────────────────────────────────────┐
│ Phase 2 wrapper (async, server.py):                │
│   1. Detect Phase 2 (token != None && !dry_run)    │
│   2. If scope-included op: await ctx.elicit(...)   │
│   3. If accept       → delegate to impl (Phase 2)  │
│   4. If reject       → ELICITATION_REJECTED        │
│   5. If unsupported  →                             │
│        - require_elicitation=True (default) → fail │
│        - require_elicitation=False → fall through  │
└────────────────────────────────────────────────────┘
                       │
                       ▼
┌────────────────────────────────────────────────────┐
│ Impl (sync, tools/destructive.py) — UNCHANGED:     │
│   Phase 2 = consume HMAC + snapshot + atomic write │
└────────────────────────────────────────────────────┘
```

### Implementation choices

- **Wrapper async** for `delete_note` and `execute_command` only.
  `rename_note` and `move_note` stay sync (out of v0.3.0 scope).
- **Impl unchanged**: 213 existing test hits on destructive ops are
  unaffected.
- **Sync impl + async wrapper composition**: the wrapper does
  `await ctx.elicit(...)` then calls the sync impl. FastMCP supports
  async tool functions natively.

### Why this closes the LLM gap

`ctx.elicit` is a **client-side capability**. The server sends an
elicitation request; the MCP client (Claude Desktop, Claude Code, or
any compliant client) renders a UI dialog to the human user. The
user's accept/reject decision is sent back to the server **directly
through the MCP transport**, bypassing the LLM. The LLM only sees
`result.action` post-decision — it cannot synthesise an "accept"
that the user did not actually click.

A coherently-hallucinating LLM that walks Phase 1 → Phase 2 with the
correct HMAC token is now stopped at the elicit gate: even if it
hallucinated both phases, the actual user must accept the dialog
before the destructive call proceeds.

## API

### New `ErrorCode`s

```python
class ErrorCode(StrEnum):
    ...
    ELICITATION_UNSUPPORTED = "elicitation_unsupported"
    ELICITATION_REJECTED = "elicitation_rejected"
```

- `ELICITATION_UNSUPPORTED`: client does not implement
  `Context.elicit` (or it raised an unexpected error). Returned in
  strict mode (`require_elicitation=True`, default).
- `ELICITATION_REJECTED`: client called elicit successfully, but the
  user clicked Reject (or `data.confirm == False`). The HMAC token
  is **not consumed** (the impl is never called).

### New `AppConfig` field

```python
require_elicitation: bool = True
```

YAML-overridable. Default secure (strict). Setting to `False` lets
deployments without an elicit-capable client opt out: those
deployments fall back to HMAC-only mode and accept the residual
coherent-hallucination risk knowingly.

### Wrapper signature change

`server.py`:

```python
@app.tool(...)
async def delete_note(
    path: str,
    confirm_token: str | None = None,
    dry_run: bool = False,
    ctx: Context = ...,  # injected by FastMCP at call time
) -> ToolResult:
    ...
```

The wrapper becomes `async def` and adds a `ctx: Context` parameter
that FastMCP injects automatically. Same for `execute_command`.

### Elicit schema and message

Private Pydantic schema (not exposed to the wider API):

```python
class _ConfirmDestructive(BaseModel):
    """User-facing schema for ctx.elicit confirmation prompt."""
    confirm: bool = Field(description="Confirm the destructive operation")
```

Message format:
- `delete_note` → `"Confirm delete on <path>?"`
- `execute_command` → `"Confirm Obsidian command '<command_id>'?"`

The `<path>` is the **raw input string** (not the
`VaultPath`-normalised form). The wrapper does not parse the path
before calling elicit; if the path is invalid (e.g., `../escape.md`),
the impl will reject it later with `PATH_ESCAPE`. The user can
always click Reject if the displayed target looks wrong.

### When does elicit fire?

| Mode | `confirm_token` | `dry_run` | elicit fires? |
|---|---|---|---|
| Phase 1 (issue) | `None` | `False` | ❌ no — Phase 1 doesn't mutate |
| Phase 1 dry-run | `None` | `True` | ❌ no — preview only |
| Phase 2 dry-run | set | `True` | ❌ no — preview only |
| **Phase 2 real** | set | `False` | ✅ **yes** — gate before consume |

## Detection of "client does not support elicit"

The MCP Python SDK's behaviour when a client lacks elicit support is
not yet pinned in this spec; it will be confirmed at implementation
time by reading the SDK source. The wrapper catches **any**
exception from `await ctx.elicit(...)` and routes it to:

- `ELICITATION_UNSUPPORTED` if `require_elicitation=True` (default)
- fall-through to impl if `require_elicitation=False`

If the SDK signals "unsupported" via a result `action` value
(e.g., `"cancel"` or a vendor-specific value) rather than an
exception, the wrapper's accept-check (`action == "accept" and
data.confirm`) will reject any non-accept value. This is captured by
ELICITATION_REJECTED for non-accept paths and by
ELICITATION_UNSUPPORTED only for actual exceptions. The
implementation may need to refine this dispatch after SDK source
inspection — the spec authorises that refinement.

## Output schema

### Reject path

```json
{
  "ok": false,
  "error": {
    "code": "elicitation_rejected",
    "message": "user declined the destructive operation"
  }
}
```

### Unsupported path (strict)

```json
{
  "ok": false,
  "error": {
    "code": "elicitation_unsupported",
    "message": "client does not support Context.elicit: <exc>"
  }
}
```

### Accept path

Same as the existing Phase 2 success envelope (impl is delegated to,
unchanged): `{ok: true, data: {path, request_id, snapshot_id, ...}, audit_id}`.

## Audit

- Phase 1 audit emission is **unchanged** (still emits with
  `dry_run=True` since issuance does not mutate).
- Phase 2 audit emission is **unchanged** (only fires if the impl is
  called, which only happens on elicit accept or
  `require_elicitation=False` fall-through).
- Reject and Unsupported paths do **not** emit Phase 2 audit (no
  mutation occurred). The token is also **not consumed**, so the user
  can retry with a fresh elicit decision.

This means an audit log reader sees:
- Phase 1 issuance (always)
- Phase 2 success (only when user accepted OR opt-out)
- No record of "user rejected" — by design. The server doesn't track
  user UI decisions in its own audit log; the client may log them
  separately.

(Future v0.3.x: emit a separate audit event for rejects, classified
as `op_kind="meta"`. Tracked in `docs/v0.1-followups.md` post-merge.)

## Test plan (TDD)

File: `tests/integration/test_server_elicit.py` (new), plus 1 test in
`tests/unit/test_config.py`.

These are **wrapper-level** tests, not impl-level. They mock the
`Context` object and its `elicit` method.

### Wrapper tests (13)

1. `test_delete_phase1_no_elicit` — Phase 1 call, mock ctx, assert
   `elicit` NOT called.
2. `test_delete_dry_run_no_elicit` — Phase 1 dry-run, assert no
   elicit.
3. `test_delete_phase2_dry_run_no_elicit` — Phase 2 with
   `dry_run=True`, assert no elicit.
4. `test_delete_phase2_elicit_accept` — Phase 2 real, mock
   `elicit` returns `accept` + `confirm=True`, assert mutation
   happens.
5. `test_delete_phase2_elicit_reject` — mock `elicit` returns
   `reject`, assert `ELICITATION_REJECTED`, no mutation, token
   NOT consumed.
6. `test_delete_phase2_elicit_accept_but_confirm_false` — mock
   `elicit` returns `accept` with `data.confirm=False`, assert
   `ELICITATION_REJECTED`.
7. `test_delete_phase2_elicit_unsupported_strict` — mock `elicit`
   raises, default config, assert `ELICITATION_UNSUPPORTED`,
   token NOT consumed.
8. `test_delete_phase2_elicit_unsupported_optout` — mock `elicit`
   raises, `require_elicitation=False`, assert mutation happens
   (HMAC-only fallback).
9. `test_execute_command_phase2_elicit_accept` — mirror of #4 for
   `execute_command`.
10. `test_execute_command_phase2_elicit_reject` — mirror of #5 for
    `execute_command`.
11. `test_rename_note_phase2_no_elicit` — out-of-scope op, assert
    elicit NOT called even at Phase 2.
12. `test_move_note_phase2_no_elicit` — out-of-scope op, assert
    elicit NOT called even at Phase 2.

13. `test_elicit_message_contains_path` — capture the elicit
    `message` argument, assert it contains the path.

### Plus (config)

- `test_require_elicitation_default_true` (in `tests/unit/test_config.py`)
  — config defaults honoured.

### E2E

No new E2E cases. The full elicit flow needs an elicit-capable
client; document this in `tests/e2e/README.md`. Existing E2E
scenarios for destructive ops (Phase 1 + dry-run) are unaffected.

### Coverage target

100 % on the new wrapper logic. Project-wide ≥ 85 % unchanged.

## Cross-cutting changes

| File | Change |
|---|---|
| `src/obsidian_hardened_mcp/config.py` | Add `require_elicitation: bool = True` field |
| `src/obsidian_hardened_mcp/domain/results.py` | Add `ELICITATION_UNSUPPORTED` + `ELICITATION_REJECTED` to `ErrorCode` |
| `src/obsidian_hardened_mcp/server.py` | `delete_note` and `execute_command` wrappers → `async def`, add `ctx: Context` param, add elicit branch |
| `tests/integration/test_server_elicit.py` (new) | 13 wrapper tests + helpers |
| `tests/unit/test_config.py` | 1 test `require_elicitation` |
| `tests/e2e/README.md` | Note about destructive ops needing client elicit support |
| `README.md` | Section "Confirmation flow for destructive ops" updated |
| `docs/security-model.md` | Threat model rewrite: 3 layers of defense |
| `SECURITY.md` | Same rewrite, user-facing |
| `docs/architecture.md` | Async wrapper pattern note |
| `docs/config-reference.md` | Document `require_elicitation` |
| `CHANGELOG.md` | Entry under `[Unreleased].Added` |
| `docs/v0.1-followups.md` | Mark M6-11 as ✅ implemented |

## CLAUDE.md invariants compliance

| # | Invariant | Compliance |
|---|---|---|
| 1 | All vault paths via `VaultPath` | ✅ unchanged (impl is unchanged) |
| 2 | Atomic writes | ✅ unchanged |
| 3 | 2-phase HMAC for destructive ops | ✅ kept; M6-11 adds a layer, doesn't replace |
| 4 | AuditEvent on write/destructive | ✅ unchanged |
| 5 | `ruamel.yaml` round-trip | N/A |
| 6 | Frontmatter writer type whitelist | N/A |
| 7 | `request_id` once per call | ✅ unchanged |
| 8 | Validation hooks before write | ✅ unchanged |
| 9 | Single-writer assumption | ✅ unchanged |

## Threat model rewrite

`docs/security-model.md` and `SECURITY.md` get a new section
replacing the v0.2.0 "honesty pass" caveat:

> ### Defence layers against destructive intent
>
> Three independent layers stop a hallucinated, injected, or
> compromised destructive call from mutating the vault:
>
> 1. **Cryptographic binding (single-shot prevention)** — 2-phase
>    HMAC token, payload-bound, single-use, TTL 90s. A single
>    hallucinated tool call cannot mutate; the server first issues a
>    token bound to the exact call, then requires the same token
>    plus the same payload on a follow-up call.
>
> 2. **Out-of-band confirmation (live human gate)** — `delete_note`
>    and `execute_command` route a confirmation through the MCP
>    client's UI via `Context.elicit`. The user's accept/reject
>    decision bypasses the LLM context entirely; a coherently-
>    hallucinating LLM cannot fabricate it.
>    Disabled with `require_elicitation: false` for clients that
>    do not implement elicit (residual risk: coherent-hallucination
>    bypass; opt-out is explicit).
>
> 3. **Recovery + detection (post-incident)** — All destructive ops
>    snapshot the target file under `.ohmcp-trash/` before mutation;
>    an append-only JSONL audit log records every successful mutation
>    with content-hash `audit_id` and shared `request_id`.
>
> v0.3.0 ships layers 1 + 3 for all destructive ops, layer 2 for
> `delete_note` and `execute_command`. `rename_note` and `move_note`
> have layer 1 + 3 only (lower risk: snapshot-restorable, basename
> rename). Extending layer 2 to those tools is tracked as v0.3.x.

## Risks and mitigations

- **Risk**: SDK behaviour for unsupported clients differs from
  expectation (returns `action="cancel"` instead of raising).
  **Mitigation**: catch both — exceptions become
  `ELICITATION_UNSUPPORTED`, non-accept actions become
  `ELICITATION_REJECTED`. Refine at impl time after reading the
  SDK source.
- **Risk**: a client with elicit support hangs indefinitely on a
  user that ignores the dialog. **Mitigation**: defer to MCP
  framework's per-request timeout. The HMAC token TTL (90s) limits
  staleness regardless.
- **Risk**: a deployment runs `require_elicitation=False`
  unintentionally and loses layer 2. **Mitigation**: default is
  `True`. CHANGELOG notes the flag prominently. README warns
  against silent opt-out.
- **Risk**: backward incompatibility — existing Phase 2 callers
  (any client) now hit the elicit gate. **Mitigation**: documented
  as a v0.3.0 breaking change for clients without elicit support.
  Opt-out path provided via config.

## Out of scope

- Extending elicit to `rename_note` / `move_note` (v0.3.x candidate)
- Typed echo confirmation ("type the path to confirm") — overkill
  for v0.3.0; client UI typically shows target prominently
- Free-form reason field in elicit — adds friction without security
  benefit
- Audit emission for rejected elicits — useful for forensics, but
  the server is not the source of truth for client UI state
- Per-tool override of `require_elicitation` (e.g., always strict
  for `delete_note`, opt-out only for `execute_command`)

These can be addressed in v0.3.x if real users surface the need.
