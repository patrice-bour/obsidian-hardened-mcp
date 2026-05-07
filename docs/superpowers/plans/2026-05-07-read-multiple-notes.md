# `read_multiple_notes` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `read_multiple_notes` — a batch-read MCP tool with partial-success semantics — as the first of three v0.3.0 features.

**Architecture:** New entry in `tools/read.py` reusing `VaultPath` validation and `read_text` per-file. Top-level rejection on bad input; per-path failures stay in `results[i].error`; cumulative byte cap stops iteration. New `ErrorCode.BATCH_TOO_LARGE` and `AppConfig.max_batch_bytes` field. No audit emission (read tool, per `CLAUDE.md` invariant #4).

**Tech Stack:** Python 3.11+, `uv`, `pytest`, `pytest-asyncio`, `hypothesis`, `pydantic` v2, `ruff`, `mypy`.

**Spec reference:** `docs/superpowers/specs/2026-05-07-read-multiple-notes-design.md`

**Branch:** `feature/read-multiple-notes` (executed in `../worktrees/feat-read-multiple-notes`)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/obsidian_hardened_mcp/domain/results.py` | Modify | Add `BATCH_TOO_LARGE = "batch_too_large"` to `ErrorCode` |
| `src/obsidian_hardened_mcp/config.py` | Modify | Add `max_batch_bytes: int = 10 * 1024 * 1024` field + positive validator on `AppConfig` |
| `src/obsidian_hardened_mcp/tools/read.py` | Modify | Add `read_multiple_notes(config, paths)` function |
| `src/obsidian_hardened_mcp/server.py` | Modify | Register the new tool with `@app.tool` (between `list_notes` and `get_frontmatter`) |
| `tests/unit/test_tools_read.py` | Modify | Add `TestReadMultipleNotes` class (15 cases + 1 property test) |
| `tests/unit/test_config.py` | Modify | Add tests for `max_batch_bytes` validation |
| `tests/e2e/scenarios/s1_read.py` | Modify | Add 1-2 E2E cases for batch read |
| `README.md` | Modify | Add row in tools table for `read_multiple_notes` |
| `docs/architecture.md` | Modify | Mention new tool in Read tools section |
| `docs/config-reference.md` | Modify | Document `max_batch_bytes` |
| `CHANGELOG.md` | Modify | `### Added` entry under `[Unreleased]` |

---

## Task 1: Set up worktree and feature branch

**Files:**
- Create: worktree at `../worktrees/feat-read-multiple-notes`
- Create: branch `feature/read-multiple-notes`

- [ ] **Step 1: Verify clean state on main**

Run: `git status && git branch --show-current`
Expected: branch `main`, clean tree.

- [ ] **Step 2: Create the feature branch + worktree**

Run:
```bash
git checkout -b feature/read-multiple-notes
git worktree add ../worktrees/feat-read-multiple-notes feature/read-multiple-notes
git checkout main
cd ../worktrees/feat-read-multiple-notes
```

Expected: Worktree created, branch checked out inside the worktree, main left untouched.

- [ ] **Step 3: Verify pytest baseline still green in the worktree**

Run: `uv run pytest -q`
Expected: `558 passed` (or whatever the current baseline is — same number as on main).

---

## Task 2: New `ErrorCode.BATCH_TOO_LARGE` and `AppConfig.max_batch_bytes`

**Files:**
- Modify: `src/obsidian_hardened_mcp/domain/results.py`
- Modify: `src/obsidian_hardened_mcp/config.py`
- Modify: `tests/unit/test_config.py`

- [x] **Step 1: Write the failing config test**

Add to the `TestSizeLimitsValidation` class in `tests/unit/test_config.py`:

```python
    def test_zero_max_batch_bytes_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(ValidationError):
            AppConfig(vault_root=tmp_vault, max_batch_bytes=0)

    def test_negative_max_batch_bytes_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(ValidationError):
            AppConfig(vault_root=tmp_vault, max_batch_bytes=-1)

    def test_max_batch_bytes_default_is_10mb(self, tmp_vault: Path) -> None:
        cfg = AppConfig(vault_root=tmp_vault)
        assert cfg.max_batch_bytes == 10 * 1024 * 1024

    def test_max_batch_bytes_custom(self, tmp_vault: Path) -> None:
        cfg = AppConfig(vault_root=tmp_vault, max_batch_bytes=5 * 1024 * 1024)
        assert cfg.max_batch_bytes == 5 * 1024 * 1024
```

- [x] **Step 2: Run config tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py -k max_batch_bytes -v`
Expected: 4 tests FAIL with `AttributeError: 'AppConfig' object has no attribute 'max_batch_bytes'` or `ValidationError: extra fields not permitted`.

- [x] **Step 3: Add the config field and validator**

In `src/obsidian_hardened_mcp/config.py`, near the existing `max_batch` field (around line 88), add:

```python
    max_batch_bytes: int = 10 * 1024 * 1024
```

Then near the existing `_max_batch_positive` validator (around line 112), add:

```python
    @field_validator("max_batch_bytes")
    @classmethod
    def _max_batch_bytes_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_batch_bytes must be positive")
        return value
```

- [x] **Step 4: Run config tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py -k max_batch_bytes -v`
Expected: 4 PASS.

- [x] **Step 5: Add `BATCH_TOO_LARGE` to ErrorCode**

In `src/obsidian_hardened_mcp/domain/results.py`, add the new value to `ErrorCode` (between `INVALID_PATH` and `PATH_ESCAPE` — alphabetical-ish; just keep existing order otherwise):

```python
    BATCH_TOO_LARGE = "batch_too_large"
```

Place it after `INVALID_PATH` to keep input-validation codes grouped.

- [x] **Step 6: Run all tests as a sanity check**

Run: `uv run pytest -q`
Expected: 558 + 4 = 562 PASS, 0 FAIL.

- [x] **Step 7: Lint and type-check**

Run:
```bash
uv run ruff check src tests
uv run mypy src
```

Expected: clean.

- [x] **Step 8: Commit**

```bash
git add src/obsidian_hardened_mcp/domain/results.py src/obsidian_hardened_mcp/config.py tests/unit/test_config.py
git commit -m "feat(config): add max_batch_bytes (10MB default) and BATCH_TOO_LARGE error code

Preparation for read_multiple_notes (v0.3 #1). The new field caps the
cumulative bytes a single batch-read call may return; the new ErrorCode
is the marker for both up-front N>max_batch rejection and per-entry cap
exhaustion."
```

---

## Task 3: Tool skeleton with input validation

**Files:**
- Modify: `src/obsidian_hardened_mcp/tools/read.py`
- Modify: `tests/unit/test_tools_read.py`

- [x] **Step 1: Write the failing input-validation tests**

At the bottom of `tests/unit/test_tools_read.py`, add:

```python
class TestReadMultipleNotes:
    def test_empty_paths_rejected(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        result = read_multiple_notes(config, [])
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH
        assert "empty" in result.error.message.lower()

    def test_too_many_paths_rejected(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        # max_batch defaults to 50; pass 51 paths.
        paths = [f"01_Notes/{i}.md" for i in range(config.max_batch + 1)]
        result = read_multiple_notes(config, paths)
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.BATCH_TOO_LARGE
        assert str(config.max_batch) in result.error.message
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_tools_read.py::TestReadMultipleNotes -v`
Expected: 2 FAIL with `ImportError: cannot import name 'read_multiple_notes'`.

- [x] **Step 3: Add the tool skeleton**

In `src/obsidian_hardened_mcp/tools/read.py`, append after `list_notes`:

```python
@tool_call
def read_multiple_notes(config: AppConfig, paths: list[str]) -> ToolResult:
    """Read N notes in one round-trip with partial-success semantics.

    Top-level rejection on empty input or `len(paths) > config.max_batch`.
    Otherwise iterates `paths` in order: per-path failures (path escape,
    not-found, file-too-large, etc.) are stored in `results[i].error`
    rather than aborting the call. If cumulative read bytes exceed
    `config.max_batch_bytes`, iteration stops; remaining paths are marked
    `BATCH_TOO_LARGE`.
    """
    if not paths:
        return ToolResult.failure(ErrorCode.INVALID_PATH, "paths cannot be empty")
    if len(paths) > config.max_batch:
        return ToolResult.failure(
            ErrorCode.BATCH_TOO_LARGE,
            f"{len(paths)} paths exceeds max_batch={config.max_batch}",
        )

    # Iteration body lands in Task 4.
    return ToolResult.success(
        data={"results": [], "cumulative_bytes": 0, "stopped_early": False}
    )
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_tools_read.py::TestReadMultipleNotes -v`
Expected: 2 PASS.

- [x] **Step 5: Lint, type-check, full-suite sanity**

Run:
```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
```

Expected: all clean, 564 PASS.

- [x] **Step 6: Commit**

```bash
git add src/obsidian_hardened_mcp/tools/read.py tests/unit/test_tools_read.py
git commit -m "feat(tools): scaffold read_multiple_notes with input validation

Up-front rejection of empty paths (INVALID_PATH) and N > max_batch
(BATCH_TOO_LARGE). Iteration body and per-path semantics land in the
next commit."
```

---

## Task 4: Iteration with happy paths and order preservation

**Files:**
- Modify: `src/obsidian_hardened_mcp/tools/read.py`
- Modify: `tests/unit/test_tools_read.py`

- [x] **Step 1: Write the failing happy-path tests**

Add to `TestReadMultipleNotes`:

```python
    def test_single_success(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        result = read_multiple_notes(config, ["01_Notes/sample.md"])
        assert result.ok
        assert result.data is not None
        results = result.data["results"]
        assert len(results) == 1
        assert results[0]["path"] == "01_Notes/sample.md"
        assert results[0]["content"] == "# Sample\n"
        assert results[0]["size"] == 9
        assert "error" not in results[0]
        assert result.data["cumulative_bytes"] == 9
        assert result.data["stopped_early"] is False

    def test_all_succeed_preserves_order(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        paths = ["01_Notes/sample.md", "_VAULT.md", "00_Journal/2026-05-04.md"]
        result = read_multiple_notes(config, paths)
        assert result.ok
        assert result.data is not None
        results = result.data["results"]
        assert [r["path"] for r in results] == paths
        assert all("content" in r for r in results)
```

- [x] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/unit/test_tools_read.py::TestReadMultipleNotes -v`
Expected: 2 new tests FAIL (assertion errors — current skeleton returns empty `results`).

- [x] **Step 3: Implement the iteration body**

Replace the `return ToolResult.success(...)` line in `read_multiple_notes` (added in Task 3) with the full iteration:

```python
    results: list[dict[str, Any]] = []
    cumulative_bytes = 0
    stopped_early = False
    cap_hit_at: int | None = None

    for i, raw_path in enumerate(paths):
        if cap_hit_at is not None:
            results.append(
                {
                    "path": raw_path,
                    "error": {
                        "code": ErrorCode.BATCH_TOO_LARGE.value,
                        "message": (
                            f"cumulative size cap reached after index {cap_hit_at}"
                        ),
                    },
                }
            )
            continue

        try:
            vp = VaultPath.from_user(raw_path, config.vault_root)
            content = read_text(vp, max_size_bytes=config.max_file_size_bytes)
        except Exception as exc:
            err = map_exception(exc)
            assert err.error is not None
            results.append(
                {
                    "path": raw_path,
                    "error": {
                        "code": err.error.code.value,
                        "message": err.error.message,
                    },
                }
            )
            continue

        size = len(content.encode("utf-8"))
        results.append({"path": raw_path, "content": content, "size": size})
        cumulative_bytes += size

        if cumulative_bytes > config.max_batch_bytes:
            cap_hit_at = i
            stopped_early = True

    return ToolResult.success(
        data={
            "results": results,
            "cumulative_bytes": cumulative_bytes,
            "stopped_early": stopped_early,
        }
    )
```

Make sure `map_exception` is imported at the top of the file by adjusting the import line to:

```python
from obsidian_hardened_mcp.tools._base import map_exception, tool_call
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_tools_read.py::TestReadMultipleNotes -v`
Expected: 4 PASS.

- [x] **Step 5: Lint and type-check**

Run:
```bash
uv run ruff check src tests
uv run mypy src
```

Expected: clean.

- [x] **Step 6: Commit**

```bash
git add src/obsidian_hardened_mcp/tools/read.py tests/unit/test_tools_read.py
git commit -m "feat(tools): implement read_multiple_notes iteration body

Iterates paths in input order. Per-path failures (caught via
map_exception) become results[i].error; iteration continues. Once
cumulative_bytes > max_batch_bytes after a successful read, remaining
paths are marked BATCH_TOO_LARGE and stopped_early flips to true."
```

---

## Task 5: Per-path error coverage

**Files:**
- Modify: `tests/unit/test_tools_read.py`

These tests should already pass — they exercise the iteration body
written in Task 4. They lock down the contract.

- [x] **Step 1: Add the per-path error tests**

Add to `TestReadMultipleNotes`:

```python
    def test_partial_success_not_found(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        paths = ["01_Notes/sample.md", "01_Notes/missing.md", "_VAULT.md"]
        result = read_multiple_notes(config, paths)
        assert result.ok
        assert result.data is not None
        results = result.data["results"]
        assert "content" in results[0]
        assert results[1]["error"]["code"] == ErrorCode.NOT_FOUND.value
        assert "content" in results[2]

    def test_partial_success_path_escape(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        paths = ["01_Notes/sample.md", "../escape.md"]
        result = read_multiple_notes(config, paths)
        assert result.ok
        assert result.data is not None
        results = result.data["results"]
        assert "content" in results[0]
        assert results[1]["error"]["code"] == ErrorCode.PATH_ESCAPE.value
        assert results[1]["path"] == "../escape.md"

    def test_partial_success_forbidden_zone(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        paths = ["01_Notes/sample.md", ".obsidian/config.json"]
        result = read_multiple_notes(config, paths)
        assert result.ok
        assert result.data is not None
        results = result.data["results"]
        assert "content" in results[0]
        assert results[1]["error"]["code"] == ErrorCode.FORBIDDEN_ZONE.value

    def test_partial_success_file_too_large(
        self, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        # Tight max_file_size_mb so a small file already breaks it.
        cfg = AppConfig(vault_root=tmp_vault, max_file_size_mb=1)
        big = tmp_vault / "01_Notes" / "big.md"
        big.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB > 1 MB cap

        result = read_multiple_notes(
            cfg, ["01_Notes/sample.md", "01_Notes/big.md"]
        )
        assert result.ok
        assert result.data is not None
        results = result.data["results"]
        assert "content" in results[0]
        assert results[1]["error"]["code"] == ErrorCode.FILE_TOO_LARGE.value
```

- [x] **Step 2: Run the tests**

Run: `uv run pytest tests/unit/test_tools_read.py::TestReadMultipleNotes -v`
Expected: all PASS (8 in the class so far).

- [x] **Step 3: Commit**

```bash
git add tests/unit/test_tools_read.py
git commit -m "test(tools): per-path error coverage for read_multiple_notes

NOT_FOUND, PATH_ESCAPE, FORBIDDEN_ZONE, FILE_TOO_LARGE all stay in
results[i].error; iteration continues across them."
```

---

## Task 6: Cumulative byte cap

**Files:**
- Modify: `tests/unit/test_tools_read.py`

- [x] **Step 1: Write the cap-iteration tests**

Add to `TestReadMultipleNotes`:

```python
    def test_cumulative_cap_stops_iteration(self, tmp_vault: Path) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        # 3 files of 6 MB each; 10 MB cap; max_file_size_mb=8 (over the
        # individual cap to allow 6 MB files).
        cfg = AppConfig(
            vault_root=tmp_vault,
            max_file_size_mb=8,
            max_batch_bytes=10 * 1024 * 1024,
        )
        for name in ("a.md", "b.md", "c.md"):
            (tmp_vault / "01_Notes" / name).write_bytes(b"x" * (6 * 1024 * 1024))

        paths = ["01_Notes/a.md", "01_Notes/b.md", "01_Notes/c.md"]
        result = read_multiple_notes(cfg, paths)
        assert result.ok
        assert result.data is not None
        results = result.data["results"]

        assert "content" in results[0]
        assert "content" in results[1]
        assert results[2]["error"]["code"] == ErrorCode.BATCH_TOO_LARGE.value
        assert "after index 1" in results[2]["error"]["message"]
        assert result.data["stopped_early"] is True
        assert result.data["cumulative_bytes"] == 12 * 1024 * 1024

    def test_cumulative_cap_marks_remaining(self, tmp_vault: Path) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        cfg = AppConfig(
            vault_root=tmp_vault,
            max_file_size_mb=8,
            max_batch_bytes=10 * 1024 * 1024,
        )
        for name in ("a.md", "b.md", "c.md", "d.md", "e.md"):
            (tmp_vault / "01_Notes" / name).write_bytes(b"x" * (4 * 1024 * 1024))

        paths = [f"01_Notes/{n}" for n in ("a.md", "b.md", "c.md", "d.md", "e.md")]
        result = read_multiple_notes(cfg, paths)
        assert result.ok
        assert result.data is not None
        results = result.data["results"]

        # First three succeed (4+4+4 = 12 MB > 10 MB cap, stops after #3)
        assert "content" in results[0]
        assert "content" in results[1]
        assert "content" in results[2]
        assert results[3]["error"]["code"] == ErrorCode.BATCH_TOO_LARGE.value
        assert results[4]["error"]["code"] == ErrorCode.BATCH_TOO_LARGE.value
        assert result.data["stopped_early"] is True
        assert result.data["cumulative_bytes"] == 12 * 1024 * 1024

    def test_no_early_stop_when_under_cap(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        result = read_multiple_notes(
            config, ["01_Notes/sample.md", "_VAULT.md", "00_Journal/2026-05-04.md"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["stopped_early"] is False
```

- [x] **Step 2: Run the tests**

Run: `uv run pytest tests/unit/test_tools_read.py::TestReadMultipleNotes -v`
Expected: all PASS (11 in the class).

- [x] **Step 3: Commit**

```bash
git add tests/unit/test_tools_read.py docs/superpowers/plans/2026-05-07-read-multiple-notes.md
git commit -m "test(tools): cumulative cap coverage for read_multiple_notes

Locks down the post-read cap behaviour: the entry that tips the total
over is included; remaining entries marked BATCH_TOO_LARGE; cumulative
total reflects the entry that crossed the line."
```

---

## Task 7: Misc edge cases and property test

**Files:**
- Modify: `tests/unit/test_tools_read.py`

- [ ] **Step 1: Write the remaining unit tests**

Add to `TestReadMultipleNotes`:

```python
    def test_duplicates_allowed(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        result = read_multiple_notes(
            config, ["01_Notes/sample.md", "01_Notes/sample.md"]
        )
        assert result.ok
        assert result.data is not None
        results = result.data["results"]
        assert len(results) == 2
        assert results[0] == results[1]
        assert result.data["cumulative_bytes"] == 18  # 9 + 9

    def test_path_field_preserves_input(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        # Caller passes `./` prefix; we must echo it back even though
        # VaultPath would normalise it away internally.
        result = read_multiple_notes(config, ["./01_Notes/sample.md"])
        assert result.ok
        assert result.data is not None
        assert result.data["results"][0]["path"] == "./01_Notes/sample.md"

    def test_no_audit_event_emitted(
        self, config: AppConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes
        from obsidian_hardened_mcp.security.audit_logger import AuditLogger

        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(
            "obsidian_hardened_mcp.security.audit_logger.DEFAULT_AUDIT_PATH",
            log_path,
            raising=False,
        )
        # Construct a logger pointing at our temp path; if the tool
        # accidentally wrote, the file would exist non-empty.
        logger = AuditLogger(log_path)
        _ = read_multiple_notes(config, ["01_Notes/sample.md"])
        # No emission expected; either the file does not exist or is empty.
        assert (not log_path.exists()) or log_path.stat().st_size == 0
        # Suppress unused-variable lint: logger constructed only to assert
        # the contract that read tools never use it.
        del logger

    def test_cumulative_bytes_field_correct(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        result = read_multiple_notes(
            config, ["01_Notes/sample.md", "_VAULT.md"]
        )
        assert result.ok
        assert result.data is not None
        # "# Sample\n" = 9 bytes, "# Vault root\n" = 13 bytes
        assert result.data["cumulative_bytes"] == 9 + 13
```

- [ ] **Step 2: Run the new tests to verify they pass**

Run: `uv run pytest tests/unit/test_tools_read.py::TestReadMultipleNotes -v`
Expected: 15 PASS.

- [ ] **Step 3: Add the hypothesis property test**

Append at the end of `TestReadMultipleNotes`:

```python
    @hypothesis.given(
        st.lists(
            st.sampled_from(
                ["01_Notes/sample.md", "_VAULT.md", "00_Journal/2026-05-04.md",
                 "01_Notes/missing.md", "../escape.md", ".obsidian/config.json"]
            ),
            min_size=1,
            max_size=10,
        )
    )
    def test_results_length_equals_input_length(
        self, config: AppConfig, paths: list[str]
    ) -> None:
        from obsidian_hardened_mcp.tools.read import read_multiple_notes

        result = read_multiple_notes(config, paths)
        if not result.ok:
            # Up-front rejection (empty / too many) is acceptable; the
            # property only asserts on successful envelopes.
            return
        assert result.data is not None
        assert len(result.data["results"]) == len(paths)
```

Make sure these imports exist at the top of the file (add if missing):

```python
import hypothesis
from hypothesis import strategies as st
```

- [ ] **Step 4: Run the property test**

Run: `uv run pytest tests/unit/test_tools_read.py::TestReadMultipleNotes::test_results_length_equals_input_length -v`
Expected: PASS (default hypothesis runs ~100 examples).

- [ ] **Step 5: Lint, type-check, full-suite check**

Run:
```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
```

Expected: 558 + 4 + 16 = 578 PASS, clean lint, clean mypy.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_tools_read.py
git commit -m "test(tools): edge cases + hypothesis property for read_multiple_notes

Duplicates, path-field echo, no-audit-emission contract, cumulative
total accuracy. Property test asserts len(results) == len(paths)
across random valid input shapes."
```

---

## Task 8: Server registration and E2E scenario

**Files:**
- Modify: `src/obsidian_hardened_mcp/server.py`
- Modify: `tests/e2e/scenarios/s1_read.py`

- [ ] **Step 1: Wire the tool into the MCP server**

In `src/obsidian_hardened_mcp/server.py`, update the import line:

```python
from obsidian_hardened_mcp.tools.read import list_notes as _list_notes_impl
from obsidian_hardened_mcp.tools.read import read_multiple_notes as _read_multiple_notes_impl
from obsidian_hardened_mcp.tools.read import read_note as _read_note_impl
```

Then add the registration block right after the existing `list_notes` registration (around line 174):

```python
    @app.tool(
        description=(
            "Read multiple notes in one batch with partial-success "
            "semantics. Per-path errors live in results[i].error; "
            "cumulative byte cap stops iteration."
        )
    )
    def read_multiple_notes(paths: list[str]) -> ToolResult:
        return _read_multiple_notes_impl(config, paths)
```

- [ ] **Step 2: Inspect the E2E scenario file**

Run: `head -60 tests/e2e/scenarios/s1_read.py`
Expected: Read the existing structure to mimic it (likely uses an `E2EHarness`-style helper to dispatch tool calls and assert).

- [ ] **Step 3: Add E2E scenarios**

Add (or append, depending on the existing structure of `s1_read.py`) two new scenario functions:

```python
async def s1d_read_multiple_notes_happy(harness) -> None:
    """Batch read of three valid notes returns all three in input order."""
    paths = ["01_Notes/sample.md", "_VAULT.md", "00_Journal/2026-05-04.md"]
    result = await harness.call_tool("read_multiple_notes", {"paths": paths})
    assert result["ok"] is True, result
    data = result["data"]
    assert [r["path"] for r in data["results"]] == paths
    assert all("content" in r for r in data["results"])
    assert data["stopped_early"] is False


async def s1e_read_multiple_notes_partial(harness) -> None:
    """Batch read with one missing path returns 2 contents + 1 error."""
    paths = ["01_Notes/sample.md", "01_Notes/missing.md", "_VAULT.md"]
    result = await harness.call_tool("read_multiple_notes", {"paths": paths})
    assert result["ok"] is True, result
    data = result["data"]
    results = data["results"]
    assert "content" in results[0]
    assert results[1]["error"]["code"] == "not_found"
    assert "content" in results[2]
```

Wire these into the scenario list / runner the same way `s1a`, `s1b`, `s1c` are wired (look at the bottom of the existing file or `run_e2e.py` to see the registration pattern).

- [ ] **Step 4: Run the unit suite to confirm registration didn't break anything**

Run: `uv run pytest -q`
Expected: still 578 PASS.

- [ ] **Step 5: Run the E2E suite**

Run: `uv run python tests/e2e/run_e2e.py`
Expected: 101 + 2 = 103 PASS (or 101 PASS + the 2 new ones tied to existing groupings depending on how scenarios.py registers — check the output).

- [ ] **Step 6: Commit**

```bash
git add src/obsidian_hardened_mcp/server.py tests/e2e/scenarios/s1_read.py
git commit -m "feat(server): register read_multiple_notes tool + E2E scenarios

Adds @app.tool registration and two E2E cases (happy 3-note batch +
partial-success 1-not-found). E2E baseline 101 → 103."
```

---

## Task 9: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/config-reference.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update README tools table**

Open `README.md`, find the "Tools" table (the one listing `read_note`, `list_notes`, `get_frontmatter`, etc.). Add a new row right after `list_notes`:

```markdown
| `read_multiple_notes` | Read N notes in one round-trip with partial-success semantics. Per-path errors stay in `results[i].error`; cumulative byte cap stops iteration. |
```

- [ ] **Step 2: Update docs/architecture.md**

Open `docs/architecture.md`, locate the "Read tools" subsection, and add (right after the `list_notes` paragraph):

```markdown
### `read_multiple_notes`

Batch-read primitive. Iterates the input `paths` in order, catching
per-path failures (path escape, not-found, file-too-large, etc.) into
`results[i].error` rather than aborting the call. Top-level rejection
applies to empty inputs and to `len(paths) > config.max_batch`. A
cumulative byte cap (`config.max_batch_bytes`, default 10 MB) stops
iteration once exceeded; remaining entries are marked
`BATCH_TOO_LARGE`. No audit emission (per CLAUDE.md invariant #4 —
write/destructive only).
```

- [ ] **Step 3: Update docs/config-reference.md**

Open `docs/config-reference.md`, locate the table or section listing `max_batch`, and add an entry for the new field. Format should match what's already there (look at `max_batch` for the template). Example:

```markdown
| `max_batch_bytes` | int | `10 * 1024 * 1024` (10 MB) | Cumulative byte cap for `read_multiple_notes`. Once exceeded after a successful read, iteration stops and remaining paths return `BATCH_TOO_LARGE`. |
```

- [ ] **Step 4: Update CHANGELOG**

In `CHANGELOG.md`, under the `[Unreleased]` (or `[0.3.0]`) section's `### Added` subsection, add:

```markdown
- `read_multiple_notes` — batch-read tool with partial-success semantics
  and a cumulative byte cap (`max_batch_bytes`, default 10 MB).
  Closes the v0.3 mcpvault parity gap. (PR #N)
- New `ErrorCode.BATCH_TOO_LARGE` for both up-front input rejection and
  per-entry cumulative-cap markers.
- New `AppConfig.max_batch_bytes` field, YAML-overridable.
```

(Replace `#N` with the actual PR number after you create it in Task 10.)

- [ ] **Step 5: Lint check on docs (markdown if a hook exists)**

Run: `uv run ruff check src tests` (sanity, no docs lint configured by default).

- [ ] **Step 6: Commit**

```bash
git add README.md docs/architecture.md docs/config-reference.md CHANGELOG.md
git commit -m "docs: document read_multiple_notes (v0.3 #1)

README tools table, architecture.md Read tools subsection,
config-reference.md max_batch_bytes entry, CHANGELOG Unreleased.Added."
```

---

## Task 10: Pre-merge audit and PR

**Files:**
- All previously modified files (verification only)

- [ ] **Step 1: Run the pre-merge skill**

Run the slash command `/pre-merge` (or invoke `Skill` with `pre-merge`). This runs:
- Phase 1 — mechanical: ruff, mypy, pytest, build
- Phase 2 — review agents: `feature-dev:code-reviewer` + `code-simplifier` (apply ALL recommendations: MUST + SHOULD + OPTIONAL — don't ask)
- Phase 3 — manual: confirm no secrets, OpenSpec tasks ticked
- Phase 4 — fixtures + manual test plan: generate `docs/manual-test-plan.md`, fill in results table

Expected: all phases green; any reviewer feedback applied as additional commits before proceeding.

- [ ] **Step 2: Final sanity check**

Run:
```bash
uv run pytest -q
uv run python tests/e2e/run_e2e.py
uv run ruff check src tests
uv run mypy src
```

Expected: 578 unit PASS, 103 E2E PASS, ruff clean, mypy clean.

- [ ] **Step 3: Push the branch**

Run: `git push -u origin feature/read-multiple-notes`

- [ ] **Step 4: Open the PR**

Run:
```bash
gh pr create --title "feat(tools): read_multiple_notes (v0.3 #1)" --body "$(cat <<'EOF'
## Summary

First of three v0.3.0 features. Implements `read_multiple_notes`,
the mcpvault-parity batch-read tool. Builds on the spec in PR #5.

## Behaviour

- Top-level rejection: empty paths → `INVALID_PATH`; `N > max_batch`
  → new `BATCH_TOO_LARGE` error.
- Per-path failures (path escape, not-found, forbidden zone,
  file-too-large) live in `results[i].error`. Iteration **continues**.
- Cumulative byte cap (`config.max_batch_bytes`, default 10 MB):
  the entry that tips the total over is included, remaining entries
  marked `BATCH_TOO_LARGE`, `stopped_early=true`.
- No audit emission (read tool — `CLAUDE.md` invariant #4).

## Tests

- 15 unit tests + 1 hypothesis property
- 2 E2E scenarios (happy 3-note batch + partial-success)
- Pytest baseline: 558 → 578
- E2E baseline: 101 → 103

## Files

- `src/obsidian_hardened_mcp/tools/read.py` — new `read_multiple_notes`
- `src/obsidian_hardened_mcp/config.py` — new `max_batch_bytes`
- `src/obsidian_hardened_mcp/domain/results.py` — new `BATCH_TOO_LARGE`
- `src/obsidian_hardened_mcp/server.py` — `@app.tool` registration
- Tests, README, architecture.md, config-reference.md, CHANGELOG

## Related PRs

- PR #4 (`chore/gitignore-secrets`) — should land before this
- PR #5 (`docs/spec-read-multiple-notes`) — design rationale

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Update CHANGELOG with the PR number and amend the docs commit**

After the PR is created, replace `(PR #N)` in `CHANGELOG.md` with the actual PR number, stage the change, then amend the docs commit (we have not pushed it through yet at the time of the PR creation — but if you have, create a new commit instead per `CLAUDE.md` "always create new commits rather than amending"):

```bash
# Create a new commit, do NOT amend the pushed docs commit
git add CHANGELOG.md
git commit -m "docs(changelog): wire PR number for read_multiple_notes"
git push origin feature/read-multiple-notes
```

---

## Self-Review

**1. Spec coverage** — every spec section maps to at least one task:

- Spec § "Input validation" → Task 3 (steps 1, 3)
- Spec § "Iteration semantics" → Task 4 (step 3)
- Spec § "Output schema" → Task 4 (step 3) + Task 7 (step 1) (`cumulative_bytes`, `stopped_early`, `path` echo)
- Spec § "Audit" (no emission) → Task 7 (`test_no_audit_event_emitted`)
- Spec § "New ErrorCode" → Task 2 (step 5)
- Spec § "New config field" → Task 2 (steps 1-4)
- Spec § "Test plan" 1-15 → Tasks 3, 4, 5, 6, 7 (every test from the spec is mapped to an explicit step)
- Spec § "Property test" → Task 7 (step 3)
- Spec § "E2E" → Task 8 (step 3)
- Spec § "Cross-cutting changes" table → Task 9 (every doc file) + Task 8 (server.py)

No gaps.

**2. Placeholder scan** — fixed inline:

- Task 9 step 4 mentions `(PR #N)` — explicitly flagged with replacement instructions in Task 10 step 5
- Task 10 step 1 references `/pre-merge` — that's a project-supported slash command listed in the available skills

**3. Type consistency** — function names checked:

- `read_multiple_notes` consistent across all tasks
- `_read_multiple_notes_impl` import alias matches `_read_note_impl` / `_list_notes_impl` pattern (Task 8 step 1)
- `BATCH_TOO_LARGE` value `"batch_too_large"` matches existing lowercase convention (Task 2 step 5)
- `max_batch_bytes` field name consistent (config + tool + tests)

**4. Pre-existing test count baseline** — all `Expected: ... PASS` numbers add up (558 → 562 after Task 2 → 564 after Task 3 → 566 after Task 4 → 570 after Task 5 → 573 after Task 6 → 578 after Task 7).
