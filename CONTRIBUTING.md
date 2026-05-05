# Contributing to `obsidian-hardened-mcp`

Thanks for taking the time to look at this. The project is small and
opinionated — please read the security model and the existing tests
before opening a pull request.

## Before you start

- **Open an issue first** for anything beyond a typo fix. The roadmap is
  tracked in [`docs/v0.1-followups.md`](docs/v0.1-followups.md); changes
  that don't align with the v0.2 priorities risk being closed.
- **Security bugs**: do **not** file a public issue. Follow the private
  reporting channel in [`SECURITY.md`](SECURITY.md).

## Development setup

Requires Python ≥ 3.11 and [`uv`](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/patrice-bour/obsidian-hardened-mcp.git
cd obsidian-hardened-mcp
uv sync
```

Run the in-process suite (≈ 5 s) and the end-to-end harness
(≈ 30 s, real subprocess):

```bash
uv run pytest -q                               # 533 passed
uv run python tests/e2e/run_e2e.py             # 101/101 PASS
uv run ruff check src tests                    # All checks passed!
uv run mypy src                                # no issues found in 35 source files
```

All four must be green before a PR is merged. CI runs the same
commands.

## Branching and commits

- Branch off `main`. Use names like `feat/<scope>`, `fix/<scope>`,
  `docs/<scope>`, `test/<scope>`, `chore/<scope>`.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat(scope): summary`, `fix(scope): summary`, etc. The body should
  explain *why*, not *what*.
- Squash or rebase locally so the final history is linear and each
  commit passes CI on its own.

## Tests are not optional

The project has a security-coverage gate (`security/`,
`domain/vault_path.py`) at 100 %. New code that touches those modules
must keep it there. The ≥ 85 % global threshold is enforced in
`pyproject.toml`.

For new features: write the failing test first
([TDD](https://en.wikipedia.org/wiki/Test-driven_development)), watch
it fail, then implement.

For bug fixes: a regression test that fails on `main` and passes on
your branch is required.

## Style

- `ruff` (configured in `pyproject.toml`) is the formatter and linter.
- `mypy --strict` is the type-checker. New `# type: ignore` comments
  need a justification in the same line.
- Comments document *why* (constraint, invariant, surprising
  behaviour), not *what*.
- All vault paths must flow through `domain.vault_path.VaultPath`.
  Never accept a raw `Path` or `str` at a tool boundary.

## Pull request checklist

- [ ] One logical change per PR. If you find another bug along the
      way, open a separate PR.
- [ ] Conventional Commit messages.
- [ ] All four checks green locally (`pytest`, `ruff`, `mypy`, E2E).
- [ ] CHANGELOG entry under `## [Unreleased]`.
- [ ] Linked to an existing issue (or the PR description explains why
      no issue was needed).
- [ ] Security-sensitive change → mention which `tests/security/`
      cases now cover it, or add new ones.

By contributing you agree your changes ship under the project's
[Apache-2.0 license](LICENSE).
