"""S5 — path sandbox: a battery of malicious paths must be rejected
with the appropriate error code at the tool boundary."""

from __future__ import annotations

from mcp_harness import E2EHarness

from ._assert import ScenarioReport

# (path, expected_error_code, label)
# Note: the server's exact code per case may vary slightly. We accept
# either of a small set of "reject" codes per case.
_CASES: list[tuple[str, frozenset[str], str]] = [
    (
        "../escape.md",
        frozenset({"path_escape", "invalid_path"}),
        "parent traversal",
    ),
    (
        "/etc/passwd",
        frozenset({"absolute_path", "invalid_path"}),
        "absolute path",
    ),
    (
        ".obsidian/config.json",
        frozenset({"forbidden_zone", "invalid_path"}),
        "forbidden zone .obsidian/",
    ),
    (
        ".git/HEAD",
        frozenset({"forbidden_zone", "invalid_path"}),
        "forbidden zone .git/",
    ),
    (
        ".opmcp-trash/x.md",
        frozenset({"forbidden_zone", "invalid_path"}),
        "forbidden zone .opmcp-trash/",
    ),
    (
        ".obsidian-power-mcp.yaml",
        frozenset({"forbidden_zone", "invalid_path", "not_a_file"}),
        "config file is reserved",
    ),
    (
        "evil\x00.md",
        frozenset({"invalid_path"}),
        "null byte",
    ),
    (
        ("a" * 256) + ".md",
        frozenset({"invalid_path"}),
        "oversize segment (>255 bytes)",
    ),
]


async def run(h: E2EHarness) -> ScenarioReport:
    rep = ScenarioReport("S5", "path sandbox")

    for path, codes, label in _CASES:
        # Probe through `read_note` (read path) AND `create_note` (write
        # path) — both flow through `VaultPath.from_user`.
        rread = await h.call("read_note", path=path)
        ok = (not rread.ok) and (rread.error_code in codes)
        rep.add(
            f"read_note rejects {label}",
            ok,
            f"got ok={rread.ok} code={rread.error_code} (expected one of {sorted(codes)})",
        )

        rcreate = await h.call(
            "create_note", path=path, content="---\n---\nx\n"
        )
        ok = (not rcreate.ok) and (rcreate.error_code in codes)
        rep.add(
            f"create_note rejects {label}",
            ok,
            f"got ok={rcreate.ok} code={rcreate.error_code} (expected one of {sorted(codes)})",
        )

    return rep
