"""Smoke integration test for the FastMCP server wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.rest.detector import RestAvailabilityDetector
from obsidian_hardened_mcp.server import create_server


@pytest.fixture
def config(tmp_vault: Path, tmp_path: Path) -> AppConfig:
    # Redirect audit_dir AND secret_file into the per-test tmp tree so we
    # never pollute the user's `~/.obsidian-hardened-mcp/` directory.
    return AppConfig(
        vault_root=tmp_vault,
        audit_dir=tmp_path / "audit",
        secret_file=tmp_path / "secret",
    )


def test_create_server_returns_fastmcp_instance(config: AppConfig) -> None:
    server = create_server(config)
    assert isinstance(server, FastMCP)
    assert server.name == "obsidian-hardened-mcp"


@pytest.mark.asyncio
async def test_registered_tools_match_capabilities_manifest(
    config: AppConfig,
) -> None:
    """The MCP-exposed tool names MUST match the manifest from
    `list_tools_capabilities` so clients can rely on it."""
    server = create_server(config)
    registered = {t.name for t in await server.list_tools()}
    expected = {
        "read_note",
        "list_notes",
        "get_frontmatter",
        "get_vault_info",
        "list_tools_capabilities",
    }
    assert expected <= registered


@pytest.mark.asyncio
async def test_read_note_tool_is_callable_through_mcp(
    config: AppConfig,
) -> None:
    """End-to-end: the MCP `read_note` tool returns the note content."""
    server = create_server(config)
    raw = await server.call_tool("read_note", {"path": "01_Notes/sample.md"})
    # call_tool returns a (content, structured) tuple in the MCP SDK; we
    # just need to confirm the call succeeded and yielded the expected text.
    assert "# Sample" in str(raw)


@pytest.mark.asyncio
async def test_list_notes_tool_is_callable_through_mcp(
    config: AppConfig,
) -> None:
    server = create_server(config)
    raw = await server.call_tool("list_notes", {"folder": None, "limit": 200})
    assert "01_Notes/sample.md" in str(raw)


@pytest.mark.asyncio
async def test_get_vault_info_tool_is_callable_through_mcp(
    config: AppConfig,
) -> None:
    server = create_server(config)
    raw = await server.call_tool("get_vault_info", {})
    assert "obsidian-hardened-mcp" in str(raw)


@pytest.mark.asyncio
async def test_list_tools_capabilities_tool_is_callable_through_mcp(
    config: AppConfig,
) -> None:
    server = create_server(config)
    raw = await server.call_tool("list_tools_capabilities", {})
    assert "read_note" in str(raw)
    assert "get_frontmatter" in str(raw)


@pytest.mark.asyncio
async def test_get_frontmatter_tool_is_callable_through_mcp(
    config: AppConfig, tmp_vault: Path
) -> None:
    (tmp_vault / "01_Notes" / "fm.md").write_text(
        "---\ntitle: MCP\n---\nBody\n"
    )
    server = create_server(config)
    raw = await server.call_tool("get_frontmatter", {"path": "01_Notes/fm.md"})
    assert "MCP" in str(raw)


@pytest.mark.asyncio
async def test_server_auto_loads_validation_config_from_vault(
    config: AppConfig, tmp_vault: Path
) -> None:
    """`create_server` (with no explicit `hooks=`) reads
    `<vault_root>/.obsidian-hardened-mcp.yaml` at boot and applies the hooks
    to write tools."""
    (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text("hooks:\n  - iso_date\n")

    server = create_server(config)
    raw = await server.call_tool(
        "create_note",
        {"path": "01_Notes/bad.md", "content": "---\ndate: tomorrow\n---\n"},
    )
    text = str(raw)
    assert "validation_failed" in text or "iso_date" in text
    assert not (tmp_vault / "01_Notes" / "bad.md").exists()


@pytest.mark.asyncio
async def test_server_with_explicit_empty_registry_skips_validation(
    config: AppConfig, tmp_vault: Path
) -> None:
    """Passing `hooks=HookRegistry([])` overrides the auto-load."""
    from obsidian_hardened_mcp.validation.hooks import HookRegistry

    (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text("hooks:\n  - iso_date\n")
    server = create_server(config, hooks=HookRegistry([]))

    await server.call_tool(
        "create_note",
        {"path": "01_Notes/bad.md", "content": "---\ndate: tomorrow\n---\n"},
    )
    assert (tmp_vault / "01_Notes" / "bad.md").exists()


# ---------------------------------------------------------------------------
# Destructive tools — server-level 2-phase confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destructive_tools_listed_in_capabilities(
    config: AppConfig,
) -> None:
    server = create_server(config)
    raw = await server.call_tool("list_tools_capabilities", {})
    text = str(raw)
    assert "delete_note" in text
    assert "rename_note" in text
    assert "move_note" in text
    assert "destructive" in text


@pytest.mark.asyncio
async def test_destructive_tools_are_registered_through_mcp(
    config: AppConfig,
) -> None:
    server = create_server(config)
    registered = {t.name for t in await server.list_tools()}
    assert {"delete_note", "rename_note", "move_note"} <= registered


@pytest.mark.asyncio
async def test_delete_note_two_phase_through_mcp(
    config: AppConfig, tmp_vault: Path
) -> None:
    """End-to-end: phase 1 returns a token, file untouched; phase 2 with
    that token deletes the file."""
    # Use an explicit registry so the server doesn't bootstrap the secret
    # from disk for this isolated assertion.
    # require_elicitation=False: this test exercises the HMAC flow via
    # server.call_tool() which has no real request context for ctx.elicit.
    from obsidian_hardened_mcp.config import AppConfig as _AppConfig
    from obsidian_hardened_mcp.security.confirm import ConfirmRegistry

    hmac_cfg = _AppConfig(
        vault_root=config.vault_root, require_elicitation=False
    )
    registry = ConfirmRegistry(secret=b"k" * 32)
    server = create_server(hmac_cfg, registry=registry)

    # Phase 1: no token.
    raw1 = await server.call_tool(
        "delete_note", {"path": "01_Notes/sample.md"}
    )
    text1 = str(raw1)
    assert "confirm_token" in text1
    # Source still in place.
    assert (tmp_vault / "01_Notes" / "sample.md").exists()

    # Extract the token (look for a base64url string of length 86).
    import re

    match = re.search(r"[A-Za-z0-9_-]{86}", text1)
    assert match is not None, f"no token found in phase-1 output: {text1[:200]}"
    token = match.group(0)

    # Phase 2: same path + token.
    raw2 = await server.call_tool(
        "delete_note",
        {"path": "01_Notes/sample.md", "confirm_token": token},
    )
    assert "snapshot_id" in str(raw2)
    # File removed; snapshot kept.
    assert not (tmp_vault / "01_Notes" / "sample.md").exists()


@pytest.mark.asyncio
async def test_delete_without_token_does_not_mutate_through_mcp(
    config: AppConfig, tmp_vault: Path
) -> None:
    from obsidian_hardened_mcp.security.confirm import ConfirmRegistry

    registry = ConfirmRegistry(secret=b"k" * 32)
    server = create_server(config, registry=registry)
    await server.call_tool("delete_note", {"path": "01_Notes/sample.md"})
    # File preserved without phase 2.
    assert (tmp_vault / "01_Notes" / "sample.md").exists()


@pytest.mark.asyncio
async def test_default_create_server_lazily_bootstraps_secret(
    config: AppConfig,
) -> None:
    """`create_server(config)` (no explicit registry) must NOT bootstrap
    the secret unless a destructive tool is actually called."""
    server = create_server(config)
    # Read tool — should not touch the secret file.
    await server.call_tool("read_note", {"path": "01_Notes/sample.md"})
    assert not config.secret_file.exists()


# ---------------------------------------------------------------------------
# REST integration (M7) — execute_command + get_vault_info
# ---------------------------------------------------------------------------


class _FakeRestClient:
    """Stand-in for `RestClient` used in REST integration tests."""

    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy
        self.execute_calls: list[str] = []

    def health_check(self) -> bool:
        if not self.healthy:
            from obsidian_hardened_mcp.rest.client import RestUnavailableError

            raise RestUnavailableError("not running")
        return True

    def execute_command(self, command_id: str) -> dict:
        self.execute_calls.append(command_id)
        return {"executed": command_id}


def _detector_with_state(
    client: _FakeRestClient,
) -> RestAvailabilityDetector:
    return RestAvailabilityDetector(client, ttl_seconds=60)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_vault_info_reports_rest_available_false_by_default(
    config: AppConfig,
) -> None:
    server = create_server(config)
    raw = await server.call_tool("get_vault_info", {})
    text = str(raw)
    assert "'rest_available': False" in text or '"rest_available": false' in text


@pytest.mark.asyncio
async def test_get_vault_info_reports_rest_available_with_detector(
    config: AppConfig,
) -> None:
    detector = _detector_with_state(_FakeRestClient(healthy=True))
    server = create_server(config, rest_detector=detector)
    raw = await server.call_tool("get_vault_info", {})
    text = str(raw)
    assert "'rest_available': True" in text or '"rest_available": true' in text


@pytest.mark.asyncio
async def test_execute_command_listed_in_capabilities(
    config: AppConfig,
) -> None:
    server = create_server(config)
    raw = await server.call_tool("list_tools_capabilities", {})
    text = str(raw)
    assert "execute_command" in text


@pytest.mark.asyncio
async def test_execute_command_returns_unavailable_without_rest_token(
    config: AppConfig,
) -> None:
    # No rest_token set in the fixture -> execute_command short-circuits.
    server = create_server(config)
    raw = await server.call_tool(
        "execute_command", {"command_id": "editor:focus"}
    )
    text = str(raw)
    assert "rest_unavailable" in text


@pytest.mark.asyncio
async def test_execute_command_two_phase_through_mcp(
    config: AppConfig,
) -> None:
    """End-to-end via FastMCP: phase 1 returns a token, phase 2 with the
    same token + matching command_id triggers the REST POST."""
    from obsidian_hardened_mcp.security.confirm import ConfirmRegistry

    client = _FakeRestClient(healthy=True)
    detector = _detector_with_state(client)
    registry = ConfirmRegistry(secret=b"k" * 32)
    server = create_server(
        config, registry=registry, rest_detector=detector
    )

    # Phase 1.
    raw1 = await server.call_tool(
        "execute_command", {"command_id": "editor:focus"}
    )
    text1 = str(raw1)
    assert "confirm_token" in text1
    assert client.execute_calls == []  # no REST call yet

    import re

    match = re.search(r"[A-Za-z0-9_-]{86}", text1)
    assert match is not None
    token = match.group(0)

    # Phase 2.
    raw2 = await server.call_tool(
        "execute_command",
        {"command_id": "editor:focus", "confirm_token": token},
    )
    text2 = str(raw2)
    assert "executed" in text2
    assert client.execute_calls == ["editor:focus"]
