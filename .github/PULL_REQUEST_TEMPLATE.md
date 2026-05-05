## Summary

<!-- One or two sentences. Why does this change exist? -->

## Linked issue

<!-- Closes #123, or "(no issue — explain why)" -->

## Type

- [ ] feat (new capability)
- [ ] fix (bug fix)
- [ ] refactor (no behaviour change)
- [ ] docs
- [ ] test
- [ ] chore

## Checks

- [ ] `uv run pytest -q` — green
- [ ] `uv run python tests/e2e/run_e2e.py` — green
- [ ] `uv run ruff check src tests` — green
- [ ] `uv run mypy src` — green
- [ ] CHANGELOG entry under `## [Unreleased]`
- [ ] Conventional Commit messages, linear history

## Security-sensitive change?

- [ ] Yes — list the `tests/security/` cases that now cover it
- [ ] No

## Notes for the reviewer

<!-- Anything surprising, deferred follow-ups, design alternatives you considered. -->
