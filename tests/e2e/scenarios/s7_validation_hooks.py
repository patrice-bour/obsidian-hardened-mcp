"""S7 — validation hooks: drop a `.obsidian-hardened-mcp.yaml` at the
vault root, restart the server, and verify the hooks block invalid
writes (iso_date, reserved_tags, json_schema).

Restart is required because v0.1 does not support hot-reload (M4-13).
We open a brand-new harness here.

The `json_schema` hook expects schemas as paths to JSON files inside
the vault (see `docs/config-reference.md` § json_schema). We materialise
`_schemas/journal.json` alongside the config.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_harness import E2EHarness

from ._assert import ScenarioReport, expect_error, expect_ok

_HOOKS_CONFIG = """\
hooks:
  - iso_date
  - reserved_tags:
      forbidden: ["forbidden-tag"]
  - json_schema:
      schemas:
        journal: _schemas/journal.json
"""

_JOURNAL_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["type", "date"],
    "properties": {
        "type": {"const": "journal"},
        "date": {"type": "string", "format": "date"},
    },
}


async def run(vault: Path) -> ScenarioReport:
    rep = ScenarioReport("S7", "validation hooks")

    # 1 — drop config + schema files, then spawn a fresh server so it
    # auto-loads the hooks at boot. Drops live inside the try/finally so
    # a partial failure (e.g., one drop succeeds, the next raises, or the
    # harness fails to start) cannot leave orphan config files in the
    # vault for subsequent runs to trip over.
    config_path = vault / ".obsidian-hardened-mcp.yaml"
    schema_dir = vault / "_schemas"
    schema_path = schema_dir / "journal.json"

    try:
        schema_dir.mkdir(parents=True, exist_ok=True)
        schema_path.write_text(json.dumps(_JOURNAL_SCHEMA), encoding="utf-8")
        config_path.write_text(_HOOKS_CONFIG, encoding="utf-8")
        async with E2EHarness(vault) as h:
            # iso_date — non-ISO date in journal/ -> reject
            bad_date = await h.call(
                "create_note",
                path="journal/2026-12-03.md",
                content="---\ntype: journal\ndate: '2026/12/03'\n---\n\nbody\n",
            )
            ok, why = expect_error(
                bad_date, "validation_failed", where="iso_date reject"
            )
            rep.add("iso_date hook rejects non-ISO date", ok, why)
            rep.add(
                "iso_date reject: file not created",
                not (vault / "journal" / "2026-12-03.md").exists(),
                "file unexpectedly created",
            )

            # reserved_tags — adding 'forbidden-tag' via set_frontmatter_field
            bad_tag = await h.call(
                "set_frontmatter_field",
                path="notes/alpha.md",
                key="tags",
                value=["forbidden-tag", "ok-tag"],
            )
            ok, why = expect_error(
                bad_tag, "validation_failed", where="reserved_tags reject"
            )
            rep.add("reserved_tags hook rejects forbidden-tag", ok, why)

            # json_schema — journal note missing 'date' -> reject
            no_date = await h.call(
                "create_note",
                path="journal/no-date.md",
                content="---\ntype: journal\n---\n\nbody\n",
            )
            ok, why = expect_error(
                no_date, "validation_failed", where="json_schema reject"
            )
            rep.add("json_schema hook rejects missing date", ok, why)

            # Sanity: a fully valid note still goes through.
            good = await h.call(
                "create_note",
                path="journal/2026-12-04.md",
                content="---\ntype: journal\ndate: 2026-12-04\n---\n\ngood\n",
            )
            ok, why = expect_ok(good, where="valid journal")
            rep.add("valid journal note accepted", ok, why)
    finally:
        # Remove config + schema files so subsequent runs see a clean vault.
        if config_path.exists():
            config_path.unlink()
        if schema_path.exists():
            schema_path.unlink()
        if schema_dir.exists() and not any(schema_dir.iterdir()):
            schema_dir.rmdir()

    return rep
