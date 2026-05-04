"""Read + verify the JSONL audit log emitted by the server.

The server appends one JSON line per write/destructive operation to
`~/.obsidian-full-mcp/audit/YYYY-MM-DD.jsonl`. We don't try to match
the on-disk hash function exactly (the audit_id formula is internal to
`security.audit_logger`); instead we assert format, presence, and
correlation properties:

- file is valid line-delimited JSON
- each line has `audit_id`, `request_id`, `tool`, `op_kind`, `outcome`,
  `vault_path`, `dry_run` keys
- a phase-1 issuance and a phase-2 commit share the same `request_id`
  is NOT guaranteed (each tool boundary calls `new_request_id()` once
  per call), but they share the same `params_hash` value.

For S8 we just count new lines after the run + spot-check a few entries
for shape conformance.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

DEFAULT_AUDIT_DIR = Path.home() / ".obsidian-full-mcp" / "audit"


def today_log_path(audit_dir: Path = DEFAULT_AUDIT_DIR) -> Path:
    today = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    return audit_dir / f"{today}.jsonl"


def line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def read_recent(path: Path, n: int) -> list[dict[str, Any]]:
    """Return the last `n` JSON entries from the audit log. If fewer than
    `n` entries exist, returns all of them."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out[-n:]


_REQUIRED_KEYS = frozenset(
    {
        "audit_id",
        "request_id",
        "tool",
        "op_kind",
        "outcome",
        "vault_path",
        "dry_run",
    }
)


def verify_shape(entry: dict[str, Any]) -> tuple[bool, str]:
    """Return (ok, reason). Doesn't check semantics, just JSON shape."""
    missing = _REQUIRED_KEYS - entry.keys()
    if missing:
        return False, f"missing keys: {sorted(missing)}"
    if entry["op_kind"] not in ("write", "destructive", "read"):
        return False, f"unexpected op_kind: {entry['op_kind']!r}"
    if entry["outcome"] not in ("success", "failure"):
        return False, f"unexpected outcome: {entry['outcome']!r}"
    return True, "ok"
