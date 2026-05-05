# M7 implementation brief — optional Local REST API integration

## Goal

Detect Obsidian's Local REST API plugin if it is running, and surface
that capability to MCP clients. M7 is **additive**: every tool that
exists today MUST keep working untouched when the REST API is absent.

This is a small milestone (the plan estimated 0.5 day) but it expands
the threat surface — `execute_command` is a new vector for an LLM to
trigger arbitrary Obsidian behaviour. We mitigate with the same
2-phase HMAC protocol as M6.

## What's already in place

- `AppConfig.rest_url` defaults to `https://127.0.0.1:27124`.
- `AppConfig.rest_token` defaults to `None`; loaded from
  `OBSIDIAN_REST_TOKEN` via `AppConfig.from_env`.
- `src/obsidian_full_mcp/rest/__init__.py` is an empty placeholder.
- `tools/meta.py:get_vault_info` reports
  `"rest_available": False  # populated by REST detector in M7`.

## Scope (this milestone)

Included:
1. `rest/client.py` — thin `httpx` wrapper with bearer auth + short
   timeouts + masked-token `__repr__`.
2. `rest/detector.py` — caches REST availability with a 60 s TTL.
3. `tools/destructive.py` (extension) — new `execute_command` tool with
   2-phase HMAC confirm. Reuses `ConfirmRegistry` from M6.
4. `tools/meta.py:get_vault_info` — `rest_available` reflects the
   detector's cached state.
5. `server.py` — instantiate the REST client + detector at create-time
   when `rest_token` is set; register `execute_command` unconditionally
   but make it fail-fast with `REST_UNAVAILABLE` when the detector says
   the API isn't reachable.
6. `domain/results.py` — three new `ErrorCode` values
   (`REST_UNAVAILABLE`, `REST_AUTH_FAILED`, `REST_ERROR`).
7. `security/confirm.py` — extend `OperationName` literal to include
   `"execute_command"`.
8. `tools/_base.py:map_exception` — map the new REST exceptions.
9. `docs/security-model.md` — document the REST surface and the
   `verify=False` decision.

Out of scope (deferred to v0.2 followups):
- **Routing `search_notes` to REST `/search/simple/`**. The plan
  promised this but the Obsidian REST search semantics (Dataview-aware
  match objects) differ enough from `tools/search.py`'s envelope that a
  faithful merge would break the current contract. Track as `M7-01`.
- **`resolve_wikilink` REST path**. No clear UX win for v0.1; the
  filesystem-based resolver already exhausts the basename + path-form
  cases. Track as `M7-02`.
- **TLS verification with a user-provided CA bundle**. Loopback +
  bearer token is acceptable for v0.1. Track as `M7-03`.
- **`execute_command` allow-list**. v0.1 ships open: any command id
  that REST accepts is runnable, gated only by 2-phase HMAC. Track as
  `M7-04`.
- **`execute_command` semantic dry-run** (look up the command label
  before execution). Requires a `GET /commands/<id>/` round-trip;
  doable, but adds latency and a second failure mode. Track as
  `M7-05`.

## New module: `rest/client.py`

```python
class RestUnavailableError(Exception):
    """The REST API is unreachable (connection refused, timeout)."""

class RestAuthError(Exception):
    """The REST API answered 401/403."""

class RestError(Exception):
    """Any other REST failure (5xx, malformed response, etc.)."""


class RestClient:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        timeout_seconds: float = 0.5,
        verify_tls: bool = False,
    ) -> None: ...

    def health_check(self) -> bool: ...
    def execute_command(self, command_id: str) -> dict: ...
    def close(self) -> None: ...

    def __repr__(self) -> str: ...   # MUST mask the token
```

Implementation notes:
- `httpx.Client(verify=False)` because the plugin ships a self-signed
  certificate for `127.0.0.1`. Document in `security-model.md`.
- Bearer auth via `Authorization: Bearer <token>` header.
- Convert connection errors (`httpx.ConnectError`,
  `httpx.ReadTimeout`) into `RestUnavailableError`.
- Convert 401/403 into `RestAuthError`.
- Convert 4xx/5xx (non-auth) into `RestError`.
- Never log the token. `__repr__` outputs e.g.
  `RestClient(base_url='https://127.0.0.1:27124', token='***')`.

## New module: `rest/detector.py`

```python
class RestAvailabilityDetector:
    def __init__(
        self,
        client: RestClient,
        *,
        ttl_seconds: int = 60,
        clock: Callable[[], datetime] = ...,
    ) -> None: ...

    def is_available(self) -> bool:
        """Returns cached availability; re-probes when the TTL elapses
        or on first call. Health-check failures count as unavailable
        for the full TTL window — we don't hammer the endpoint when it
        is down."""
        ...

    def invalidate(self) -> None:
        """Force a re-probe on the next is_available() call. Reserved
        for callers that observed an in-flight REST failure and want
        the next is_available() to reflect the new reality."""
        ...
```

Storage: two attributes — `_cached: bool | None`, `_checked_at: datetime
| None`. On `is_available()`:
1. If `_cached is not None` and `now - _checked_at < ttl`: return cached.
2. Otherwise probe via `client.health_check()`; cache result + timestamp.
3. Probe exceptions = `False`.

## New tool: `execute_command`

```python
def execute_command(
    config: AppConfig,
    audit: AuditLogger,
    registry: ConfirmRegistry,
    rest_client: RestClient | None,
    detector: RestAvailabilityDetector | None,
    *,
    command_id: str,
    confirm_token: str | None = None,
    dry_run: bool = False,
) -> ToolResult: ...
```

Behaviour:
- **No client / no detector** → `REST_UNAVAILABLE` immediately.
- **Detector says unavailable** → `REST_UNAVAILABLE`.
- `dry_run=True` → preview only (no token, no REST call). The preview
  contains `{"command_id": ..., "would_execute": True}`. Audit emitted
  with `op_kind="destructive"`, `dry_run=True`.
- **Phase 1** (`confirm_token=None`, not dry_run):
  - Validate `command_id` is a non-empty ASCII string (no traversal,
    no whitespace).
  - Compute `payload_hash = params_hash("execute_command", command_id)`.
  - Issue token via `registry.issue(operation="execute_command",
    target=<placeholder VaultPath>, payload_hash=...)`.
  - Audit (dry_run=True), return token + preview.
  - Note on `target`: `OperationToken.target` is currently a
    `VaultPath`, but `execute_command` has no path. **Decision**:
    extend the dataclass with `target_command: str | None = None`
    (default None preserves M6's API). `__post_init__` validates that
    **exactly one** of `target` / `target_command` is set. The HMAC
    input includes whichever is non-None, prefixed by a `p:` / `c:`
    discriminator so a path target and a command target with the same
    string can never collide. `consume()` gains a parallel
    `expected_target_command: str | None = None` parameter; callers
    pass whichever applies. Existing M6 callers (delete/rename/move)
    do not change.
- **Phase 2** (token provided):
  - Re-validate `command_id`.
  - `registry.consume(...)` against the same payload hash.
  - Re-check detector availability (it may have changed).
  - Call `rest_client.execute_command(command_id)`.
  - Audit (op_kind=destructive, dry_run=False, snapshot_id=None — we
    don't snapshot before a remote command).

`OperationName` literal extended:
```python
OperationName = Literal[
    "delete_note", "rename_note", "move_note", "execute_command", "batch"
]
```

## Wiring (`server.py`)

Signature: `create_server(config, *, hooks=None, registry=None,
rest_detector=None)`. The `rest_detector` parameter is new. Default
behaviour:

```python
rest_client: RestClient | None = None
if rest_detector is None and config.rest_token is not None:
    rest_client = RestClient(
        config.rest_url, config.rest_token, timeout_seconds=0.5
    )
    rest_detector = RestAvailabilityDetector(rest_client, ttl_seconds=60)
elif rest_detector is not None:
    # Tests inject a fake detector and (optionally) a fake client; pull
    # the client from the detector so the destructive tool can call it.
    rest_client = getattr(rest_detector, "_client", None)
```

Tool registration:
- `execute_command` is **always registered**. If REST is unavailable
  at call time, the tool returns `REST_UNAVAILABLE`.
- `get_vault_info`'s `rest_available` reflects
  `rest_detector.is_available()` at the moment of the call.

## Manifest entry

```python
{
    "name": "execute_command",
    "kind": "destructive",
    "description": (
        "Execute a named Obsidian command via the Local REST API. "
        "Requires the plugin to be running. Two-phase HMAC confirm "
        "(same protocol as delete_note)."
    ),
}
```

## Tests to write (TDD)

### `rest/client.py` — `tests/unit/test_rest_client.py`
- Health check returns True on 200.
- Health check raises `RestAuthError` on 401/403.
- Health check raises `RestUnavailableError` on connection refused /
  timeout.
- `__repr__` masks the token.
- `execute_command` issues `POST /commands/{id}/` with bearer header.
- `execute_command` raises `RestError` on 5xx.
- `close()` releases the underlying httpx client.
- httpx behaviour mocked via `respx` (already a transitive dep) or
  `httpx.MockTransport`.

### `rest/detector.py` — `tests/unit/test_rest_detector.py`
- First call probes the client.
- Subsequent calls within TTL use the cache (no second probe).
- After TTL, the next call re-probes.
- Probe exception → `False`, cached for the full TTL.
- `invalidate()` forces a re-probe on the next call.
- Clock injection used throughout — no `time.sleep` in tests.

### `tools/destructive.py:execute_command` — `tests/unit/test_tools_execute_command.py`
- No detector → `REST_UNAVAILABLE`.
- Detector says unavailable → `REST_UNAVAILABLE`.
- `dry_run=True` → preview, no REST call, no token.
- Phase 1 → token + preview; REST client untouched.
- Phase 2 with valid token → REST `execute_command` called once.
- Phase 2 with stale token → `EXPIRED_CONFIRMATION_TOKEN`.
- Phase 2 with swapped command_id → `PAYLOAD_MISMATCH`.
- Replay phase 2 → `INVALID_CONFIRMATION_TOKEN`.
- REST raises `RestUnavailableError` mid-execute → audit failure +
  `REST_UNAVAILABLE`.
- REST raises `RestAuthError` → `REST_AUTH_FAILED`.

### Server integration — `tests/integration/test_server.py`
- `get_vault_info` returns `rest_available=False` when no token.
- `get_vault_info` returns `rest_available=True` when injected detector
  reports available.
- `execute_command` is in `list_tools_capabilities` always.
- End-to-end: phase 1 → phase 2 with stubbed REST detector + client
  delivers the REST POST.

### Confirm registry — `tests/unit/test_confirm_registry.py`
- New: token issued for `execute_command` carries `target_kind="command"`
  and `command_id` instead of a `VaultPath`.
- Consume rejects mismatched `target_kind`.

## Threat-model implications (security-model.md additions)

- **`verify=False`** — accepted because the endpoint is loopback and
  requires bearer auth. An attacker with loopback + token already has
  process control. Document as a known posture, not an oversight.
- **Token never logged** — `__repr__` masks it; `httpx` request
  recorders are never enabled.
- **`execute_command` is destructive** — same 2-phase HMAC as
  `delete_note`. The audit trail records every phase-1 issuance and
  phase-2 commit (snapshot_id is None — there is nothing to snapshot
  before a remote command).
- **No allow-list yet** — v0.1 ships open. Track `M7-04` as a v0.2
  hardening task.

## Suggested commit shape

```
feat(M7): optional Local REST API integration

- rest/client.py: httpx wrapper, bearer auth, masked token, error
  taxonomy (RestUnavailableError / RestAuthError / RestError).
- rest/detector.py: 60s TTL availability cache with clock injection.
- tools/destructive.py: new execute_command tool, 2-phase HMAC reuses
  ConfirmRegistry. OperationName literal extended.
- security/confirm.py: OperationToken target made optional for
  command-typed tokens; new target_kind discriminator.
- create_server: optional rest_detector parameter; client built from
  config.rest_token when present.
- get_vault_info now reflects detector.is_available().
- New ErrorCode: REST_UNAVAILABLE, REST_AUTH_FAILED, REST_ERROR.

search_notes / resolve_wikilink REST routing deferred to v0.2 (M7-01,
M7-02). TLS CA bundle (M7-03) and execute_command allow-list (M7-04)
likewise.

Co-Authored-By: Claude ...
```
