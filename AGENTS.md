# obsidian-power-mcp — Agent Instructions

Secure MCP server for Obsidian vaults. Filesystem-first with optional Local REST API enrichment.

## Project conventions

- **Language**: Python ≥ 3.11, dependency manager `uv`
- **Style**: ruff (configured in `pyproject.toml`), mypy strict
- **Tests**: pytest + pytest-asyncio + hypothesis, coverage ≥ 85% globally, 100% on `security/` and `domain/vault_path.py`
- **TDD**: write the failing test first, watch it fail, then implement minimal code to pass
- **Commits**: Conventional Commits (`feat(...)`, `fix(...)`, `test(...)`, `refactor(...)`, `docs(...)`)

## Architecture overview

Read `docs/architecture.md` for module layout. Key invariants:

1. **All vault paths flow through `domain.vault_path.VaultPath`** — never accept a raw `Path` or string from a tool boundary
2. **All writes are atomic**: tmp-in-same-dir + fsync + `os.replace` + dir-fsync
3. **All destructive ops require 2-phase HMAC token confirmation** (see `security/confirm.py`)
4. **All write/destructive ops emit an `AuditEvent`** to the JSONL audit log
5. **Frontmatter parser is `ruamel.yaml` in safe mode only** — no PyYAML, no unsafe loaders
6. **Validation hooks run in declared order** before any write touches disk

## Forbidden patterns

- Calling `pathlib.Path` or `os.path` directly in tool implementations (use `VaultPath`)
- Using `yaml.load()` from PyYAML (only `ruamel.yaml` safe)
- Bypassing the audit logger on write operations
- Storing HMAC secret anywhere except `~/.obsidian-power-mcp/secret` (mode 0600)
- Writing into `.obsidian/`, `.git/`, `.trash/`, `.opmcp-trash/` or the config file

## Running tests

```bash
uv run pytest                              # all tests
uv run pytest -m security                  # security-critical only
uv run pytest --cov --cov-report=term-missing
uv run ruff check .                        # lint
uv run mypy src                            # type check
```

## Plan reference

The approved v0.1 plan lives at `~/.claude/plans/les-serveurs-mcp-existants-tranquil-wave.md` (off-repo).
