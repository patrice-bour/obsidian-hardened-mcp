"""Seed a fresh Obsidian vault with synthetic notes for E2E testing.

The seeded vault is structured to exercise:
- read flows (list, read, frontmatter, search by mode/tag/type, wikilinks)
- write flows (create, update, append, patch, frontmatter atomic ops)
- destructive flows with backlink rewrite (rename + move)
- YAML safety (a tampered note dropped directly so the parser must reject)
- validation hooks (a journal note that violates iso_date)

Layout:
    .test-vault/
        index.md
        notes/alpha.md
        notes/beta.md
        notes/gamma.md
        org/acme.md
        journal/2026-05-04.md
        journal/2026-05-05.md
        frontmatter-rich.md
        to-rename.md
        to-move.md
        unsafe-yaml.md           (only when seed_unsafe=True)

Calling `seed(target, unsafe=False, with_hooks_config=False)` wipes the
target directory and re-creates it from scratch. Idempotent.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# --- Note bodies (no frontmatter) ------------------------------------------

_INDEX_BODY = """\
# Test vault index

Wikilinks for resolution:
- [[alpha]]
- [[beta]]
- [[gamma]]
- [[acme]]
- [[to-rename]]
- [[to-move]]
"""

_ALPHA_BODY = """\
# Alpha

This note contains the magic keyword **needle-foo** for fulltext search.
It links to [[beta]].
"""

_BETA_BODY = """\
# Beta

Beta references [[alpha]] and contains the keyword **needle-bar**.
"""

_GAMMA_BODY = """\
# Gamma

Pattern probe: ABC123-DEF.
"""

_ACME_BODY = """\
# Acme Corp

Organisation note. Body keyword: **needle-org**.
"""

_JOURNAL_OK_BODY = """\
# Journal 2026-05-04

- bullet 1
- bullet 2
"""

_JOURNAL_BAD_BODY = """\
# Journal 2026-05-05

- bullet 1
"""

_FRONTMATTER_RICH_BODY = """\
# Rich frontmatter

Body content for round-trip preservation tests.
"""

_TO_RENAME_BODY = """\
# To rename

References [[to-move]] for backlink-rewrite test.
"""

_TO_MOVE_BODY = """\
# To move

References [[to-rename]] for backlink-rewrite test.
"""

# Unsafe YAML — uses an explicit non-default tag. The parser MUST reject
# this on read. We craft it directly bytes-on-disk because the server
# refuses to write it.
_UNSAFE_YAML_BODY = """\
---
title: Unsafe
weapon: !!python/object/apply:os.system [echo pwned]
---

# Unsafe note
"""

# Frontmatter-rich preserves comments + key order + quote styles.
_FRONTMATTER_RICH_TEXT = """\
---
# top-level comment kept on round-trip
title: "Rich frontmatter"
type: note
tags:
  - foo
  - bar
priority: 1
status: 'draft'  # inline comment after status
nested:
  alpha: 1
  beta: 2
---
""" + _FRONTMATTER_RICH_BODY


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def seed(
    target: Path, *, unsafe: bool = False, with_hooks_config: bool = False
) -> Path:
    """Wipe `target` and seed it with the canonical E2E test vault.

    Returns `target` for chaining.
    """
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    # index
    _write(
        target / "index.md",
        _frontmatter("type: moc\ntitle: Index\n") + _INDEX_BODY,
    )

    # notes/
    _write(
        target / "notes" / "alpha.md",
        _frontmatter("type: note\ntags:\n  - foo\n  - bar\n") + _ALPHA_BODY,
    )
    _write(
        target / "notes" / "beta.md",
        _frontmatter("type: note\ntags:\n  - foo\n") + _BETA_BODY,
    )
    _write(
        target / "notes" / "gamma.md",
        _frontmatter("type: note\ntags:\n  - bar\n") + _GAMMA_BODY,
    )

    # org/
    _write(
        target / "org" / "acme.md",
        _frontmatter("type: organisation\nname: Acme Corp\n") + _ACME_BODY,
    )

    # journal/
    _write(
        target / "journal" / "2026-05-04.md",
        _frontmatter("type: journal\ndate: 2026-05-04\n") + _JOURNAL_OK_BODY,
    )
    _write(
        target / "journal" / "2026-05-05.md",
        _frontmatter("type: journal\ndate: '2026/05/05'\n")
        + _JOURNAL_BAD_BODY,
    )

    # rich frontmatter (special — keep raw to preserve comments/quotes)
    _write(target / "frontmatter-rich.md", _FRONTMATTER_RICH_TEXT)

    # destructive backlink targets
    _write(
        target / "to-rename.md",
        _frontmatter("type: note\n") + _TO_RENAME_BODY,
    )
    _write(
        target / "to-move.md",
        _frontmatter("type: note\n") + _TO_MOVE_BODY,
    )

    if unsafe:
        _write(target / "unsafe-yaml.md", _UNSAFE_YAML_BODY)

    if with_hooks_config:
        _write(target / ".obsidian-hardened-mcp.yaml", _HOOKS_CONFIG)

    return target


def _frontmatter(body: str) -> str:
    """Wrap a YAML body in `---` delimiters with a trailing newline."""
    return "---\n" + body + "---\n\n"


# Validation hooks config dropped only for S7. The schemas come from
# `docs/config-reference.md` § built-in hooks.
_HOOKS_CONFIG = """\
hooks:
  - iso_date
  - reserved_tags:
      forbidden: ["forbidden-tag"]
  - json_schema:
      schemas:
        journal:
          type: object
          required: [type, date]
          properties:
            type: { const: journal }
            date: { type: string, format: date }
"""


def list_seeded_files(target: Path) -> list[str]:
    """Return vault-relative posix paths of all seeded markdown notes,
    sorted for stable assertions."""
    return sorted(
        p.relative_to(target).as_posix()
        for p in target.rglob("*.md")
        if p.is_file()
    )


if __name__ == "__main__":
    # Quick manual probe: `python tests/e2e/seed_vault.py`
    here = Path(__file__).parent
    target = here / ".test-vault"
    seed(target, unsafe=True, with_hooks_config=False)
    print(f"seeded {target}")
    for rel in list_seeded_files(target):
        print(f"  {rel}")
