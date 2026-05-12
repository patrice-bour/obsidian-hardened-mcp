# Release checklist

Per-release knobs that drift silently if not checked. The v0.2.0
release missed every entry below; the v0.2.1 fix-up promoted this
file from "would-be-nice" to "must run before tagging".

## Before bumping the version

1. **Run the gates locally**:
   ```bash
   uv run pytest -q
   uv run python tests/e2e/run_e2e.py
   uv run ruff check .
   uv run mypy src
   ```
   All four must be green. If any fails, the release is not happening
   today.

2. **Read the diff against the previous tag** end-to-end:
   ```bash
   git diff v<previous>..HEAD -- README.md SECURITY.md CHANGELOG.md docs/
   ```
   You are looking for: stale claims, leftover "todo" markers, and
   any "v<n+1> followup" / "in flight" / "next release" promise that
   the current release is not actually delivering.

## At bump time

3. **`pyproject.toml`** — bump `version`. The `__version__`
   constant in `src/obsidian_hardened_mcp/__init__.py` is derived
   from `importlib.metadata.version("obsidian-hardened-mcp")`, so
   it follows automatically — but verify after `uv sync`:
   ```bash
   uv run python -c "from obsidian_hardened_mcp import __version__; print(__version__)"
   ```

4. **`README.md`**:
   - `> **Status**: vX.Y.Z, …` line near the top.
   - Pin example: `git+https://github.com/.../@vX.Y.Z`.

5. **`SECURITY.md`** — supported-versions table.
   Policy: only the latest minor is supported. Demote the previous
   one to `< X.Y`.

6. **`CHANGELOG.md`**:
   - Move the `[Unreleased]` content into a new `[X.Y.Z] — YYYY-MM-DD`
     section.
   - Re-empty `[Unreleased]`.

## After tagging

7. **Followup-target labels**. If the release shipped without a
   feature it had been promising, **rename the target version** in
   every doc that mentions it:
   - `README.md`
   - `SECURITY.md`
   - `docs/security-model.md`
   - `docs/v0.1-followups.md` (forward-looking entries only — leave
     the dated M8-audit disposition table historical)

   Example: v0.2.0 was supposed to ship M6-11 / `restore_from_snapshot`
   / M7-03 TLS CA bundle. None of the three landed. After tagging,
   bulk-rename "v0.2 followup" / "v0.2 roadmap" → "v0.3 …" wherever
   M6-11 / restore / M7-03 are mentioned.

8. **Internal handoff note** — bump the headline version number and
   insert a one-liner about what landed.

## Push gates

These need explicit user confirmation each time (per the project's
durable workflow preference):

- `git push origin <branch>`
- `git push origin v<X.Y.Z>` (tag push)
- `gh repo edit ...` (visibility, branch protection, settings)
- `uv publish` (PyPI)

Never chain commit + push in one Bash call. Every push is its own
ask.
