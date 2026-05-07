# `manage_tags` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `manage_tags` — a dedicated tag-management MCP tool with `add`/`remove`/`replace`/`list` ops — as the second of three v0.3.0 features.

**Architecture:** New entry in `tools/frontmatter.py` running its own parse-mutate-write cycle (mirroring `_mutate_frontmatter` shape but with custom output and skip-on-no-op). Reuses `parse_note`/`render_note`/`atomic_write_text`. Helper module `_tag_ops.py` (or local helpers in `frontmatter.py`) for normalisation + per-op transforms. New `ErrorCode.INVALID_TAG`. No 2-phase HMAC.

**Tech Stack:** Python 3.11+, `uv`, `pytest`, `pytest-asyncio`, `hypothesis`, `pydantic` v2, `ruamel.yaml`, `ruff`, `mypy`.

**Spec reference:** `docs/superpowers/specs/2026-05-07-manage-tags-design.md`

**Branch:** `feature/manage-tags` (worktree at `../worktrees/feat-manage-tags`, already created)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/obsidian_hardened_mcp/domain/results.py` | Modify | Add `INVALID_TAG = "invalid_tag"` to `ErrorCode` |
| `src/obsidian_hardened_mcp/tools/frontmatter.py` | Modify | Add `manage_tags` + private helpers `_normalize_tag`, `_normalize_input_tags`, `_extract_existing_tags`, `_apply_tag_op`, `_compute_tag_delta` |
| `src/obsidian_hardened_mcp/server.py` | Modify | Register the new `@app.tool` (between `merge_frontmatter` and `search_notes`) |
| `src/obsidian_hardened_mcp/tools/meta.py` | Modify | Add `manage_tags` to capabilities manifest |
| `tests/unit/test_tools_frontmatter.py` | Modify | New `TestManageTags` class (20 unit tests) |
| `tests/e2e/scenarios/s3_frontmatter.py` | Modify | 2 new `rep.add` steps |
| `README.md` | Modify | Mention in tools table / capability summary |
| `docs/architecture.md` | Modify | Tag-operations subsection under Frontmatter tools |
| `CHANGELOG.md` | Modify | `### Added` entry under `[Unreleased]` |

---

## Task 1: Worktree confirmation

**Files:** none (verification only)

The worktree at `/Users/pbr/projets/IA/MCP/obsidian-hardened-mcp/worktrees/feat-manage-tags` is already created on `feature/manage-tags` branch (off `origin/main` = `daa24a4`). This task just confirms the implementer is working from the right place.

- [ ] **Step 1: Confirm working directory**

Run: `pwd && git status && git branch --show-current`
Expected:
- Working dir: `/Users/pbr/projets/IA/MCP/obsidian-hardened-mcp/worktrees/feat-manage-tags`
- branch `feature/manage-tags`, clean tree

- [ ] **Step 2: Verify pytest baseline**

Run: `uv run pytest -q`
Expected: **582 passed** (baseline after v0.3 #1 merged into `main`).

---

## Task 2: New `ErrorCode.INVALID_TAG` and tag-input validation helpers

**Files:**
- Modify: `src/obsidian_hardened_mcp/domain/results.py`
- Modify: `src/obsidian_hardened_mcp/tools/frontmatter.py`
- Modify: `tests/unit/test_tools_frontmatter.py`

- [x] **Step 1: Add `INVALID_TAG` ErrorCode**

In `src/obsidian_hardened_mcp/domain/results.py`, add to `ErrorCode` (place it near `INVALID_PATH` and `BATCH_TOO_LARGE` to keep input-validation codes grouped):

```python
    INVALID_TAG = "invalid_tag"
```

- [x] **Step 2: Write failing tests for the validation helpers**

Add to `tests/unit/test_tools_frontmatter.py` (top-level, before any existing test classes — these test private helpers so an internal-import idiom is fine):

```python
class TestNormalizeTag:
    def test_strips_hash_prefix(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import _normalize_tag

        assert _normalize_tag("#wip") == "wip"

    def test_strips_whitespace(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import _normalize_tag

        assert _normalize_tag("  wip  ") == "wip"

    def test_strips_hash_then_whitespace(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import _normalize_tag

        assert _normalize_tag(" #wip ") == "wip"

    def test_accepts_hierarchy(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import _normalize_tag

        assert _normalize_tag("project/aaa") == "project/aaa"

    def test_rejects_empty_after_strip(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import (
            _InvalidTagError,
            _normalize_tag,
        )

        import pytest

        with pytest.raises(_InvalidTagError):
            _normalize_tag("#")
        with pytest.raises(_InvalidTagError):
            _normalize_tag("   ")

    def test_rejects_invalid_chars(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import (
            _InvalidTagError,
            _normalize_tag,
        )

        import pytest

        for bad in ("a b", "a\nb", "a\tb", "tag!", "tag?"):
            with pytest.raises(_InvalidTagError):
                _normalize_tag(bad)

    def test_rejects_leading_or_trailing_slash(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import (
            _InvalidTagError,
            _normalize_tag,
        )

        import pytest

        for bad in ("/wip", "wip/", "/wip/"):
            with pytest.raises(_InvalidTagError):
                _normalize_tag(bad)
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_tools_frontmatter.py::TestNormalizeTag -v`
Expected: 7 FAIL with `ImportError` (helpers not yet defined).

- [ ] **Step 4: Implement the helpers**

In `src/obsidian_hardened_mcp/tools/frontmatter.py`, add these helpers near the top of the "Internals" block (after `class _UnsafeValueError(ValueError):` around line 302):

```python
import re

_TAG_RE = re.compile(r"^[A-Za-z0-9_./-]+$")


class _InvalidTagError(ValueError):
    """Internal sentinel raised by `_normalize_tag` and friends. Caller
    converts this to `ErrorCode.INVALID_TAG`."""


def _normalize_tag(raw: str) -> str:
    """Trim whitespace, strip a single leading '#', then validate.

    Raises `_InvalidTagError` if the result is empty, has invalid chars
    (anything outside [A-Za-z0-9_./-]), or starts/ends with '/'.
    """
    s = raw.strip()
    if s.startswith("#"):
        s = s[1:].strip()
    if not s:
        raise _InvalidTagError(
            f"tag {raw!r} invalid: empty after '#' strip and trim"
        )
    if not _TAG_RE.match(s):
        raise _InvalidTagError(
            f"tag {s!r} invalid: must match [A-Za-z0-9_./-]+"
        )
    if s.startswith("/") or s.endswith("/"):
        raise _InvalidTagError(
            f"tag {s!r} invalid: must not start or end with '/'"
        )
    return s
```

Place the `import re` at the top of the file with the other stdlib imports if not already there.

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tools_frontmatter.py::TestNormalizeTag -v`
Expected: 7 PASS.

- [x] **Step 6: Lint + mypy + full suite**

Run:
```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
```

Expected: all clean. **589 passed** (582 + 7 new).

- [x] **Step 7: Commit**

```bash
git add src/obsidian_hardened_mcp/domain/results.py src/obsidian_hardened_mcp/tools/frontmatter.py tests/unit/test_tools_frontmatter.py
git commit -m "feat(frontmatter): tag normalisation helpers + INVALID_TAG error code

Preparation for manage_tags (v0.3 #2).

- _normalize_tag: trims whitespace, strips a single leading '#',
  validates against [A-Za-z0-9_./-]+, refuses leading/trailing '/'.
- _InvalidTagError: internal sentinel mapped to INVALID_TAG by
  manage_tags (next commit).
- New ErrorCode.INVALID_TAG."
```

Also include the plan file with all 7 of Task 2's checkboxes ticked.

---

## Task 3: Skeleton `manage_tags` + `op="list"`

**Files:**
- Modify: `src/obsidian_hardened_mcp/tools/frontmatter.py`
- Modify: `tests/unit/test_tools_frontmatter.py`

The skeleton handles input validation (op + tags shape), reads the existing tags, and dispatches `op="list"` immediately. Other ops raise NotImplementedError for now.

- [x] **Step 1: Write failing skeleton tests**

Add to `tests/unit/test_tools_frontmatter.py` after `TestNormalizeTag`:

```python
class TestManageTags:
    @pytest.fixture
    def config(self, tmp_vault: Path) -> AppConfig:
        return AppConfig(vault_root=tmp_vault)

    @pytest.fixture
    def audit(self, tmp_path: Path) -> AuditLogger:
        return AuditLogger(tmp_path / "audit")

    # --- Input validation ---

    def test_add_with_empty_tags_rejected(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(config, audit, "01_Notes/sample.md", "add", [])
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_TAG

    def test_add_with_none_tags_rejected(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(config, audit, "01_Notes/sample.md", "add", None)
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_TAG

    def test_remove_with_empty_tags_rejected(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(config, audit, "01_Notes/sample.md", "remove", [])
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_TAG

    def test_invalid_tag_chars_rejected(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(
            config, audit, "01_Notes/sample.md", "add", ["a b"]
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_TAG

    # --- list op ---

    def test_list_empty_when_no_tags_key(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(config, audit, "01_Notes/sample.md", "list")
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == []
        assert result.data["path"] == "01_Notes/sample.md"

    def test_list_returns_existing_tags(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        (tmp_vault / "01_Notes" / "tagged.md").write_text(
            "---\ntags:\n  - wip\n  - draft\n---\nbody\n"
        )
        result = manage_tags(config, audit, "01_Notes/tagged.md", "list")
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["wip", "draft"]

    def test_list_emits_no_audit(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        audit_dir = tmp_path / "audit"
        logger = AuditLogger(audit_dir)
        _ = manage_tags(config, logger, "01_Notes/sample.md", "list")
        # No emission expected.
        assert (not audit_dir.exists()) or not list(audit_dir.glob("*.jsonl"))

    def test_list_rejects_non_list_tags(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        (tmp_vault / "01_Notes" / "csv.md").write_text(
            "---\ntags: a, b, c\n---\nbody\n"
        )
        result = manage_tags(config, audit, "01_Notes/csv.md", "list")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.MALFORMED_FRONTMATTER
```

Add the imports needed at the top of the test file if not already there:
```python
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_tools_frontmatter.py::TestManageTags -v`
Expected: 8 FAIL with `ImportError: cannot import name 'manage_tags'`.

- [x] **Step 3: Implement skeleton + list op**

In `src/obsidian_hardened_mcp/tools/frontmatter.py`, near the existing top-level `@tool_call`-decorated functions, add (after `merge_frontmatter` is a good slot):

```python
TagOp = Literal["add", "remove", "replace", "list"]


@tool_call
def manage_tags(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    op: TagOp,
    tags: list[str] | None = None,
    *,
    hooks: HookRegistry | None = None,
    dry_run: bool = False,
) -> ToolResult:
    """Add, remove, replace, or list tags in a note's YAML frontmatter.

    `op="add"`: idempotent — duplicates dropped silently.
    `op="remove"`: silent no-op for tags that aren't present; if the
      result is empty, the `tags:` key is removed from the frontmatter.
    `op="replace"`: wholesale set; pass `tags=[]` to clear.
    `op="list"`: read-only, no audit emission.

    Input tags are normalised: leading '#' stripped, whitespace trimmed,
    validated against `^[A-Za-z0-9_./-]+$` with no leading/trailing '/'.

    Note: `cumulative_bytes` is irrelevant here; this tool is per-note.
    """
    # Up-front input validation.
    if op in ("add", "remove") and (tags is None or not tags):
        return ToolResult.failure(
            ErrorCode.INVALID_TAG,
            f"op={op!r} requires non-empty tags",
        )

    # Normalise input tags (raises _InvalidTagError on bad shape).
    if tags is None:
        normalized: list[str] = []
    else:
        try:
            normalized = _dedupe_in_order(_normalize_tag(t) for t in tags)
        except _InvalidTagError as exc:
            return ToolResult.failure(ErrorCode.INVALID_TAG, str(exc))

    started = time.monotonic()
    request_id = new_request_id()

    # Resolve path + read existing.
    try:
        vp = VaultPath.from_user(path, config.vault_root)
    except Exception as exc:
        return map_exception(exc)
    if not vp.absolute.exists():
        return ToolResult.failure(
            ErrorCode.NOT_FOUND, f"file not found: {vp.relative}"
        )

    try:
        existing_text = read_text(vp, max_size_bytes=config.max_file_size_bytes)
        parsed = parse_note(existing_text)
        existing_tags = _extract_existing_tags(parsed.frontmatter)
    except _MalformedTagsFieldError as exc:
        return ToolResult.failure(ErrorCode.MALFORMED_FRONTMATTER, str(exc))
    except Exception as exc:
        return map_exception(exc)

    # list op: pure read.
    if op == "list":
        return ToolResult.success(
            data={
                "path": str(vp.relative),
                "tags": list(existing_tags),
            }
        )

    # add/remove/replace: implemented in later tasks.
    raise NotImplementedError(f"op={op!r} not yet implemented")


def _dedupe_in_order(items: object) -> list[str]:
    """Iterate over `items`, returning a new list with duplicates removed,
    preserving first-occurrence order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


class _MalformedTagsFieldError(ValueError):
    """Raised when an existing 'tags:' field is not a list of strings."""


def _extract_existing_tags(fm: CommentedMap | None) -> list[str]:
    """Return existing tags as a `list[str]`, or `[]` if absent.

    Raises `_MalformedTagsFieldError` if `tags:` exists but is not a list
    of strings.
    """
    if fm is None or "tags" not in fm:
        return []
    raw = fm["tags"]
    if not isinstance(raw, list):
        raise _MalformedTagsFieldError(
            "existing 'tags:' field is not a list of strings"
        )
    if not all(isinstance(t, str) for t in raw):
        raise _MalformedTagsFieldError(
            "existing 'tags:' field is not a list of strings"
        )
    return list(raw)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tools_frontmatter.py::TestManageTags -v`
Expected: 8 PASS.

- [x] **Step 5: Lint + mypy + full suite**

Run:
```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
```

Expected: clean. **597 passed** (589 + 8 new).

- [x] **Step 6: Commit**

```bash
git add src/obsidian_hardened_mcp/tools/frontmatter.py tests/unit/test_tools_frontmatter.py
git commit -m "feat(frontmatter): scaffold manage_tags with op='list' implemented

Up-front validation: empty/None tags rejected for add/remove with
INVALID_TAG. Bad-shape input tags rejected via _normalize_tag.
Existing 'tags:' that is not a list-of-strings rejected with
MALFORMED_FRONTMATTER (no silent CSV migration).

list op is fully implemented and read-only (no audit emission).
add/remove/replace raise NotImplementedError until next commits."
```

Also include the plan file with all 6 of Task 3's checkboxes ticked.

---

## Task 4: `op="add"` implementation

**Files:**
- Modify: `src/obsidian_hardened_mcp/tools/frontmatter.py`
- Modify: `tests/unit/test_tools_frontmatter.py`

- [x] **Step 1: Write failing tests for `add`**

Add to `TestManageTags` (in `test_tools_frontmatter.py`):

```python
    def test_add_to_empty_creates_tags_key(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(
            config, audit, "01_Notes/sample.md", "add", ["wip"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["wip"]
        assert result.data["added"] == ["wip"]
        assert result.data["removed"] == []
        assert result.data["op"] == "add"

    def test_add_dedupe_silent(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        (tmp_vault / "01_Notes" / "tagged.md").write_text(
            "---\ntags:\n  - a\n---\nbody\n"
        )
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "add", ["a", "b"]
        )
        assert result.ok
        assert result.data is not None
        # 'a' was already present; only 'b' is new.
        assert result.data["tags"] == ["a", "b"]
        assert result.data["added"] == ["b"]
        assert result.data["removed"] == []

    def test_add_preserves_existing_order_then_new(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        (tmp_vault / "01_Notes" / "tagged.md").write_text(
            "---\ntags:\n  - z\n  - a\n---\nbody\n"
        )
        # Existing order is [z, a]. Adding [m, b] yields [z, a, m, b].
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "add", ["m", "b"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["z", "a", "m", "b"]

    def test_add_hash_prefix_stripped(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(
            config, audit, "01_Notes/sample.md", "add", ["#wip"]
        )
        assert result.ok
        assert result.data is not None
        # '#' stripped on input.
        assert result.data["tags"] == ["wip"]

    def test_add_no_change_when_all_already_present(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        path = tmp_vault / "01_Notes" / "tagged.md"
        path.write_text("---\ntags:\n  - a\n  - b\n---\nbody\n")
        mtime_before = path.stat().st_mtime_ns
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "add", ["a", "b"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["a", "b"]
        assert result.data["added"] == []
        # mtime unchanged on no-op (skip-disk-write optimisation).
        assert path.stat().st_mtime_ns == mtime_before
```

- [x] **Step 2: Run new tests to verify they fail**

Run: `uv run pytest tests/unit/test_tools_frontmatter.py::TestManageTags -k "add" -v`
Expected: 5 FAIL — current skeleton raises `NotImplementedError`.

- [x] **Step 3: Implement add op**

Replace the `raise NotImplementedError(f"op={op!r} not yet implemented")` line at the end of `manage_tags` with the full mutate-write cycle:

```python
    # Compute the new tag list per op.
    if op == "add":
        new_tags = list(existing_tags)
        for t in normalized:
            if t not in new_tags:
                new_tags.append(t)
    elif op == "remove":
        raise NotImplementedError("op='remove' lands in Task 5")
    elif op == "replace":
        raise NotImplementedError("op='replace' lands in Task 6")
    else:  # pragma: no cover - exhaustive Literal
        return ToolResult.failure(
            ErrorCode.INVALID_TAG, f"unknown op {op!r}"
        )

    added = [t for t in new_tags if t not in existing_tags]
    removed = [t for t in existing_tags if t not in new_tags]
    params_hash_value = params_hash(path, op, normalized)

    # Skip disk write on no-op (mtime stability).
    if new_tags == existing_tags:
        audit_id = emit_audit(
            audit,
            request_id=request_id,
            tool="manage_tags",
            op_kind="write",
            vault_path=str(vp.relative),
            outcome="success",
            started=started,
            params_hash=params_hash_value,
            dry_run=dry_run,
        )
        return ToolResult(
            ok=True,
            data={
                "path": str(vp.relative),
                "request_id": request_id,
                "op": op,
                "tags": new_tags,
                "added": added,
                "removed": removed,
            },
            dry_run=dry_run,
            audit_id=audit_id,
        )

    # Build new frontmatter.
    new_fm = (
        copy.deepcopy(parsed.frontmatter)
        if parsed.frontmatter is not None
        else CommentedMap()
    )
    if not new_tags:
        if "tags" in new_fm:
            del new_fm["tags"]
    else:
        new_fm["tags"] = new_tags

    new_parsed = ParsedNote(
        frontmatter=(new_fm if new_fm else None),
        body=parsed.body,
    )
    new_content = render_note(new_parsed)

    # Hooks run on the post-write state.
    if hooks is not None:
        try:
            run_validation_hooks(
                hooks,
                HookContext(
                    path=vp,
                    new_frontmatter=(
                        None if not new_fm else to_plain_dict(dict(new_fm))
                    ),
                    new_body=parsed.body,
                    operation="manage_tags",
                ),
            )
        except Exception as exc:
            return map_exception(exc)

    if dry_run:
        audit_id = emit_audit(
            audit,
            request_id=request_id,
            tool="manage_tags",
            op_kind="write",
            vault_path=str(vp.relative),
            outcome="success",
            started=started,
            params_hash=params_hash_value,
            dry_run=True,
        )
        return ToolResult(
            ok=True,
            data={
                "path": str(vp.relative),
                "request_id": request_id,
                "op": op,
                "tags": new_tags,
                "added": added,
                "removed": removed,
                "new_content": new_content,
            },
            dry_run=True,
            audit_id=audit_id,
        )

    from obsidian_hardened_mcp.fs.writer import atomic_write_text

    try:
        atomic_write_text(vp, new_content)
    except Exception as exc:
        return map_exception(exc)

    audit_id = emit_audit(
        audit,
        request_id=request_id,
        tool="manage_tags",
        op_kind="write",
        vault_path=str(vp.relative),
        outcome="success",
        started=started,
        params_hash=params_hash_value,
        dry_run=False,
    )
    return ToolResult(
        ok=True,
        data={
            "path": str(vp.relative),
            "request_id": request_id,
            "op": op,
            "tags": new_tags,
            "added": added,
            "removed": removed,
        },
        audit_id=audit_id,
    )
```

- [x] **Step 4: Run add tests**

Run: `uv run pytest tests/unit/test_tools_frontmatter.py::TestManageTags -k "add" -v`
Expected: 5 PASS.

- [x] **Step 5: Lint + mypy + full suite**

Run:
```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
```

Expected: clean. **602 passed** (597 + 5 new).

- [x] **Step 6: Commit**

```bash
git add src/obsidian_hardened_mcp/tools/frontmatter.py tests/unit/test_tools_frontmatter.py
git commit -m "feat(frontmatter): manage_tags op='add' with skip-on-no-op

Idempotent add: input tags are normalised, deduped against existing,
appended preserving existing-order-then-new order. If no actual
change (all input tags already present), skip the disk write to keep
mtime stable; audit still emits.

Hook validation runs against the post-write state; dry_run honoured
identically to set_frontmatter_field."
```

Also include the plan file with all 6 of Task 4's checkboxes ticked.

---

## Task 5: `op="remove"` implementation

**Files:**
- Modify: `src/obsidian_hardened_mcp/tools/frontmatter.py`
- Modify: `tests/unit/test_tools_frontmatter.py`

- [x] **Step 1: Write failing tests for `remove`**

Add to `TestManageTags`:

```python
    def test_remove_existing_tag(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        (tmp_vault / "01_Notes" / "tagged.md").write_text(
            "---\ntags:\n  - a\n  - b\n  - c\n---\nbody\n"
        )
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "remove", ["b"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["a", "c"]
        assert result.data["removed"] == ["b"]
        assert result.data["added"] == []

    def test_remove_absent_tag_silent_noop(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        path = tmp_vault / "01_Notes" / "tagged.md"
        path.write_text("---\ntags:\n  - a\n---\nbody\n")
        mtime_before = path.stat().st_mtime_ns
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "remove", ["does-not-exist"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["a"]
        assert result.data["removed"] == []
        # No-op: mtime unchanged.
        assert path.stat().st_mtime_ns == mtime_before

    def test_remove_all_drops_tags_key(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags
        from obsidian_hardened_mcp.frontmatter import parse_note

        (tmp_vault / "01_Notes" / "tagged.md").write_text(
            "---\ntags:\n  - a\n  - b\n---\nbody\n"
        )
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "remove", ["a", "b"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == []
        assert result.data["removed"] == ["a", "b"]

        # Verify on disk: 'tags:' key is gone (not 'tags: []').
        text = (tmp_vault / "01_Notes" / "tagged.md").read_text()
        parsed = parse_note(text)
        assert parsed.frontmatter is None or "tags" not in parsed.frontmatter

    def test_remove_with_no_tags_key_noop(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        # sample.md has no frontmatter at all in the seed vault.
        result = manage_tags(
            config, audit, "01_Notes/sample.md", "remove", ["wip"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == []
        assert result.data["removed"] == []
```

- [x] **Step 2: Run new tests to verify they fail**

Run: `uv run pytest tests/unit/test_tools_frontmatter.py::TestManageTags -k "remove" -v`
Expected: 4 FAIL — `op='remove'` raises `NotImplementedError`.

- [x] **Step 3: Implement remove op**

Find the `elif op == "remove":` branch in `manage_tags` and replace its `raise NotImplementedError(...)` with:

```python
    elif op == "remove":
        new_tags = [t for t in existing_tags if t not in normalized]
```

The rest of the function (skip-on-no-op check, frontmatter rebuild including the `if not new_tags: del fm["tags"]` branch, hooks, dry_run, write, audit) is already in place from Task 4.

- [x] **Step 4: Run remove tests**

Run: `uv run pytest tests/unit/test_tools_frontmatter.py::TestManageTags -k "remove" -v`
Expected: 4 PASS.

- [x] **Step 5: Full suite**

Run: `uv run pytest -q`
Expected: **606 passed** (602 + 4 new).

- [x] **Step 6: Commit**

```bash
git add src/obsidian_hardened_mcp/tools/frontmatter.py tests/unit/test_tools_frontmatter.py
git commit -m "feat(frontmatter): manage_tags op='remove' with cleanup-on-empty

Silent no-op for absent tags. When the resulting list is empty, the
'tags:' key is removed from the frontmatter entirely (no 'tags: []').
mtime stable when nothing actually changes."
```

Also include the plan file with all 6 of Task 5's checkboxes ticked.

---

## Task 6: `op="replace"` implementation

**Files:**
- Modify: `src/obsidian_hardened_mcp/tools/frontmatter.py`
- Modify: `tests/unit/test_tools_frontmatter.py`

- [ ] **Step 1: Write failing tests for `replace`**

Add to `TestManageTags`:

```python
    def test_replace_overwrites_full_list(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        (tmp_vault / "01_Notes" / "tagged.md").write_text(
            "---\ntags:\n  - a\n  - b\n---\nbody\n"
        )
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "replace", ["x", "y"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["x", "y"]
        assert sorted(result.data["added"]) == ["x", "y"]
        assert sorted(result.data["removed"]) == ["a", "b"]

    def test_replace_empty_drops_tags_key(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags
        from obsidian_hardened_mcp.frontmatter import parse_note

        (tmp_vault / "01_Notes" / "tagged.md").write_text(
            "---\ntags:\n  - a\n---\nbody\n"
        )
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "replace", []
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == []
        assert result.data["removed"] == ["a"]

        text = (tmp_vault / "01_Notes" / "tagged.md").read_text()
        parsed = parse_note(text)
        assert parsed.frontmatter is None or "tags" not in parsed.frontmatter

    def test_replace_same_list_is_noop(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        path = tmp_vault / "01_Notes" / "tagged.md"
        path.write_text("---\ntags:\n  - a\n  - b\n---\nbody\n")
        mtime_before = path.stat().st_mtime_ns
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "replace", ["a", "b"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["a", "b"]
        assert result.data["added"] == []
        assert result.data["removed"] == []
        assert path.stat().st_mtime_ns == mtime_before
```

- [ ] **Step 2: Run replace tests to verify they fail**

Run: `uv run pytest tests/unit/test_tools_frontmatter.py::TestManageTags -k "replace" -v`
Expected: 3 FAIL — `op='replace'` raises `NotImplementedError`.

- [ ] **Step 3: Implement replace op**

Find the `elif op == "replace":` branch in `manage_tags` and replace its `raise NotImplementedError(...)` with:

```python
    elif op == "replace":
        new_tags = list(normalized)
```

(The downstream logic — skip-on-no-op, cleanup-on-empty, hooks, write, audit — is already in place.)

- [ ] **Step 4: Run replace tests**

Run: `uv run pytest tests/unit/test_tools_frontmatter.py::TestManageTags -k "replace" -v`
Expected: 3 PASS.

- [ ] **Step 5: Full suite**

Run: `uv run pytest -q`
Expected: **609 passed** (606 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add src/obsidian_hardened_mcp/tools/frontmatter.py tests/unit/test_tools_frontmatter.py
git commit -m "feat(frontmatter): manage_tags op='replace' (wholesale set, [] clears)

Replace = set tags = normalised input list. tags=[] drops the 'tags:'
key entirely (parity with remove-everything). Skip-on-no-op honoured.
added/removed reflect the symmetric difference."
```

Also include the plan file with all 6 of Task 6's checkboxes ticked.

---

## Task 7: Cross-cutting tests (round-trip, dry-run, hooks)

**Files:**
- Modify: `tests/unit/test_tools_frontmatter.py`

- [ ] **Step 1: Add the cross-cutting tests**

Add to `TestManageTags`:

```python
    def test_round_trip_preserves_other_fields(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        # Frontmatter with multiple keys and a comment.
        original = (
            "---\n"
            "title: My Note\n"
            "# important\n"
            "date: 2026-05-04\n"
            "tags:\n"
            "  - old\n"
            "---\n"
            "body content\n"
        )
        path = tmp_vault / "01_Notes" / "rich.md"
        path.write_text(original)

        result = manage_tags(
            config, audit, "01_Notes/rich.md", "add", ["new"]
        )
        assert result.ok

        after = path.read_text()
        # Other fields and the comment must survive.
        assert "title: My Note" in after
        assert "# important" in after
        assert "date: 2026-05-04" in after
        assert "body content" in after
        # tags now has both old and new
        assert "old" in after and "new" in after

    def test_dry_run_no_disk_write(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        path = tmp_vault / "01_Notes" / "tagged.md"
        original = "---\ntags:\n  - a\n---\nbody\n"
        path.write_text(original)
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "add", ["b"], dry_run=True
        )
        assert result.ok
        assert result.dry_run is True
        # File on disk MUST NOT have changed.
        assert path.read_text() == original

    def test_hook_violation_rejected(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags
        from obsidian_hardened_mcp.validation.hooks import (
            HookContext,
            HookRegistry,
            HookViolationError,
        )

        class _RejectAlways:
            name = "reject-always"

            def __call__(self, ctx: HookContext) -> None:
                raise HookViolationError("rejected by test hook")

        hooks = HookRegistry([_RejectAlways()])
        result = manage_tags(
            config, audit, "01_Notes/sample.md", "add", ["wip"], hooks=hooks
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.VALIDATION_FAILED
```

- [ ] **Step 2: Run new tests**

Run: `uv run pytest tests/unit/test_tools_frontmatter.py::TestManageTags -k "round_trip or dry_run or hook" -v`
Expected: 3 PASS (these exercise behaviour already implemented in Task 4).

- [ ] **Step 3: Full suite**

Run: `uv run pytest -q`
Expected: **612 passed** (609 + 3 new). Total `TestManageTags` = 20 tests as planned.

- [ ] **Step 4: Lint + mypy**

Run:
```bash
uv run ruff check src tests
uv run mypy src
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_tools_frontmatter.py
git commit -m "test(frontmatter): cross-cutting coverage for manage_tags

Round-trip preservation (other fields + comments survive a tag edit),
dry_run does not touch disk, hook violations propagate as
VALIDATION_FAILED."
```

Also include the plan file with all 5 of Task 7's checkboxes ticked.

---

## Task 8: Server registration + meta + E2E

**Files:**
- Modify: `src/obsidian_hardened_mcp/server.py`
- Modify: `src/obsidian_hardened_mcp/tools/meta.py`
- Modify: `tests/e2e/scenarios/s3_frontmatter.py`

- [ ] **Step 1: Wire the tool into the MCP server**

In `src/obsidian_hardened_mcp/server.py`, update the existing imports from `tools.frontmatter` to include `manage_tags`:

```python
from obsidian_hardened_mcp.tools.frontmatter import (
    delete_frontmatter_field as _delete_frontmatter_field_impl,
)
from obsidian_hardened_mcp.tools.frontmatter import (
    get_frontmatter as _get_frontmatter_impl,
)
from obsidian_hardened_mcp.tools.frontmatter import (
    manage_tags as _manage_tags_impl,
)
from obsidian_hardened_mcp.tools.frontmatter import (
    merge_frontmatter as _merge_frontmatter_impl,
)
from obsidian_hardened_mcp.tools.frontmatter import (
    set_frontmatter_field as _set_frontmatter_field_impl,
)
```

(Match the existing alphabetical import style; ruff will fix order if needed.)

Then add the registration block right after the `merge_frontmatter` block:

```python
    @app.tool(
        description=(
            "Add, remove, replace, or list tags in a note's YAML "
            "frontmatter. Idempotent: 'add' dedupes silently, 'remove' "
            "no-ops on absent tags, empty result drops the 'tags:' key. "
            "Input '#tag' is normalised to 'tag'."
        )
    )
    def manage_tags(
        path: str,
        op: str,
        tags: list[str] | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        return _manage_tags_impl(
            config,
            audit_logger,
            path,
            op,  # type: ignore[arg-type]
            tags,
            hooks=hooks,
            dry_run=dry_run,
        )
```

The `# type: ignore[arg-type]` is because MCP can't easily narrow `str` to `Literal["add","remove","replace","list"]`; the inner function will reject unknown ops via the existing fallthrough.

- [ ] **Step 2: Update `meta.py` capabilities manifest**

In `src/obsidian_hardened_mcp/tools/meta.py`, find the list of known tools (similar to what was added for `read_multiple_notes` in v0.3 #1) and add an entry for `manage_tags`. The exact dict shape depends on the file; mirror what was added for `read_multiple_notes` in commit `5a3cfc0`. Read the file first to see the format.

- [ ] **Step 3: Sanity-check unit + S0 smoke**

Run: `uv run pytest -q`
Expected: 612 PASS still.

Run: `uv run python tests/e2e/run_e2e.py`
Expected: All scenarios PASS, with S0 (smoke / capabilities) green. If S0 fails because `manage_tags` is missing from the capabilities manifest, fix `meta.py` and re-run.

- [ ] **Step 4: Add E2E scenarios to `s3_frontmatter.py`**

Read `tests/e2e/scenarios/s3_frontmatter.py` first to understand the existing `rep.add(...)` style. Then append two new test groups (or as many `rep.add` calls as needed, matching existing per-scenario style):

```python
    # --- manage_tags happy path ---
    result = await h.call_tool(
        "manage_tags",
        {"path": "notes/test.md", "op": "add", "tags": ["wip"]},
    )
    rep.add(
        "manage_tags add returns ok",
        result.ok is True,
        f"got {result}",
    )
    rep.add(
        "manage_tags add returns expected tags",
        (result.data or {}).get("tags") == ["wip"],
        f"got tags={(result.data or {}).get('tags')!r}",
    )

    list_result = await h.call_tool(
        "manage_tags",
        {"path": "notes/test.md", "op": "list"},
    )
    rep.add(
        "manage_tags list returns ok",
        list_result.ok is True,
        f"got {list_result}",
    )
    rep.add(
        "manage_tags list reflects prior add",
        (list_result.data or {}).get("tags") == ["wip"],
        f"got tags={(list_result.data or {}).get('tags')!r}",
    )

    # --- manage_tags remove drops the 'tags:' key when empty ---
    remove_result = await h.call_tool(
        "manage_tags",
        {"path": "notes/test.md", "op": "remove", "tags": ["wip"]},
    )
    rep.add(
        "manage_tags remove returns ok",
        remove_result.ok is True,
        f"got {remove_result}",
    )
    rep.add(
        "manage_tags remove leaves empty tags",
        (remove_result.data or {}).get("tags") == [],
        f"got tags={(remove_result.data or {}).get('tags')!r}",
    )
```

Note: adapt the path (`"notes/test.md"`) to whatever the seed vault provides — read `tests/e2e/seed_vault.py` to confirm.

- [ ] **Step 5: Run E2E**

Run: `uv run python tests/e2e/run_e2e.py`
Expected: 109 + 6 = **115 PASS** (or more depending on how the existing scenario file groups; just verify all green).

- [ ] **Step 6: Commit**

```bash
git add src/obsidian_hardened_mcp/server.py src/obsidian_hardened_mcp/tools/meta.py tests/e2e/scenarios/s3_frontmatter.py
git commit -m "feat(server): register manage_tags tool + E2E scenarios

Adds @app.tool registration, capabilities manifest entry (S0 smoke
gate), and 6 new rep.add E2E steps covering add → list → remove."
```

Also include the plan file with all 6 of Task 8's checkboxes ticked.

---

## Task 9: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: README — bump tool count + add tool description**

Open `README.md`. The line that says "**19 tools**" (in the section titled something like "What it can do") needs to become "**20 tools**".

If there's a tools listing or capability summary nearby, add a short line for `manage_tags`:

```markdown
- `manage_tags` — add/remove/replace/list tags in a note's frontmatter, with idempotent semantics and `#`-prefix tolerance.
```

If the README only has prose (no table), insert a sentence in the most natural paragraph.

- [ ] **Step 2: docs/architecture.md — add tag-ops subsection**

Open `docs/architecture.md`. Find the "Tools" section and the "Frontmatter operations" or similar subsection. Append:

```markdown
### `manage_tags`

Dedicated tag primitive for the `tags:` frontmatter field. Supports
four ops: `add` (idempotent), `remove` (silent no-op for absent tags),
`replace` (wholesale set, `[]` clears), and `list` (read-only, no
audit). Input tags are normalised: leading `#` stripped, whitespace
trimmed, validated against `^[A-Za-z0-9_./-]+$`, no leading/trailing
`/`. When the resulting list is empty (after `remove` or
`replace=[]`), the `tags:` key is removed from the frontmatter
entirely. Reuses `_mutate_frontmatter`'s parse/render/atomic-write
machinery for round-trip preservation.
```

If the existing section uses a different heading style (e.g., `####` instead of `###`), adapt.

- [ ] **Step 3: CHANGELOG**

In `CHANGELOG.md`, under `[Unreleased]` `### Added`, append:

```markdown
- `manage_tags` — dedicated tag-management tool with `add`, `remove`,
  `replace`, `list` ops. Idempotent semantics, `#`-prefix tolerance,
  cleanup-on-empty. Closes the v0.3 mcpvault parity gap on tag
  manipulation.
- New `ErrorCode.INVALID_TAG` for tag-input validation failures.
```

If `[Unreleased]` does not exist (it should, from v0.3 #1), check that `[0.3.0]` exists or create one.

- [ ] **Step 4: Sanity**

Run: `uv run pytest -q`
Expected: 612 PASS still (docs don't affect tests).

- [ ] **Step 5: Commit**

```bash
git add README.md docs/architecture.md CHANGELOG.md
git commit -m "docs: document manage_tags (v0.3 #2)

README tool-count bump (19 → 20) + capability summary,
architecture.md tag-operations subsection, CHANGELOG entry."
```

Also include the plan file with all 5 of Task 9's checkboxes ticked.

---

## Task 10: Pre-merge audit + push + PR

**Files:** verification only

- [ ] **Step 1: Final mechanical checks**

Run from the worktree dir:
```bash
uv run pytest -q
uv run python tests/e2e/run_e2e.py
uv run ruff check src tests
uv run mypy src
```

Expected:
- 612 unit PASS
- E2E green (115 or whatever S3 totals to)
- ruff clean
- mypy clean

- [ ] **Step 2: Push the branch**

Run: `git push -u origin feature/manage-tags`

- [ ] **Step 3: Open the PR**

Run:
```bash
gh pr create --title "feat(tools): manage_tags (v0.3 #2)" --body "$(cat <<'EOF'
## Summary

Second of three v0.3.0 features. Implements `manage_tags`, the
dedicated tag-management tool. Builds on the spec in #7.

## Behaviour

- **Four ops**: `add`, `remove`, `replace`, `list`.
- **Idempotent**: `add` dedupes silently; `remove` no-ops on absent
  tags; `replace` is wholesale set.
- **Cleanup on empty**: removing the last tag (or `replace=[]`) drops
  the `tags:` key entirely (no `tags: []`).
- **`#` strip**: input `"#wip"` is normalised to `"wip"`.
- **Charset validation**: `^[A-Za-z0-9_./-]+$`, no leading/trailing
  `/`.
- **Strict shape check**: existing `tags:` must be a list of strings;
  CSV / scalar shapes refuse with `MALFORMED_FRONTMATTER` (no silent
  migration).
- **Skip on no-op**: when the operation produces no actual change,
  the disk write is skipped (mtime stable); audit still emits.
- **`list` op**: read-only, no audit emission (parity with
  `read_note` / `get_frontmatter`).

## API additions

- New `ErrorCode.INVALID_TAG = "invalid_tag"`
- New tool `manage_tags(path, op, tags=None, dry_run=False)`

## Tests

- **20 new unit tests** covering validation, all four ops, round-trip
  preservation, dry-run, hook violations
- **6 new E2E rep.add steps** in S3 (add → list → remove)
- Pytest baseline: 582 → **612** PASS
- E2E baseline: 109 → 115 PASS (or as registered)

## Files

- `src/obsidian_hardened_mcp/tools/frontmatter.py` — new `manage_tags` + helpers
- `src/obsidian_hardened_mcp/domain/results.py` — new `INVALID_TAG`
- `src/obsidian_hardened_mcp/server.py` — `@app.tool` registration
- `src/obsidian_hardened_mcp/tools/meta.py` — capabilities manifest
- Tests, README, architecture.md, CHANGELOG

## Related PRs

- #7 (`docs/spec-manage-tags`) — design rationale

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Confirm PR opened**

Run: `gh pr view --json url,number,state | head`
Expected: PR `OPEN` on `feature/manage-tags` with the title and body just provided.

---

## Self-Review

**1. Spec coverage**

| Spec section | Task |
|---|---|
| Up-front validation (op + tags) | Task 3 |
| Tag normalisation (strip `#`, regex) | Task 2 |
| Existing `tags:` shape check | Task 3 (`_extract_existing_tags`) |
| `add` semantics | Task 4 |
| `remove` semantics | Task 5 |
| `replace` semantics | Task 6 |
| `list` semantics | Task 3 |
| Output schema (write ops) | Task 4 (added/removed/op fields) |
| Output schema (list) | Task 3 |
| New `ErrorCode.INVALID_TAG` | Task 2 |
| Audit emission rules | Task 4 (write path), Task 3 (list = no audit) |
| No 2-phase HMAC | All write tasks (no token plumbing introduced) |
| Skip on no-op | Task 4 (the optimisation lives in the shared post-Task-4 code path) |
| 20 unit tests | Tasks 2 (7) + 3 (8) + 4 (5) + 5 (4) + 6 (3) + 7 (3) = 30 actually. Wait — Task 2's 7 tests are for the helper, not for `manage_tags` itself. Spec lists 20 unit tests for `TestManageTags`. Recount: Task 3 (8) + Task 4 (5) + Task 5 (4) + Task 6 (3) + Task 7 (3) = 23 in `TestManageTags`. Plus 7 in `TestNormalizeTag`. Total 30 new unit tests. The spec was conservative; the plan adds slightly more coverage and that's fine. |
| 2 E2E | Task 8 (6 rep.add steps grouped as 2 logical scenarios) |
| Cross-cutting docs | Task 9 |

**2. Placeholder scan** — no TBDs. The `# type: ignore[arg-type]` in Task 8 step 1 is documented inline.

**3. Type consistency** — function names, `TagOp` literal, ErrorCode value `"invalid_tag"` all consistent across tasks.

**4. Pytest baseline math** — 582 (post v0.3 #1) → 589 (Task 2) → 597 (Task 3) → 602 (Task 4) → 606 (Task 5) → 609 (Task 6) → 612 (Task 7). 612 final.
