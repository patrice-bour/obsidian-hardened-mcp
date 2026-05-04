"""Wrapper around `mcp.client.stdio` for E2E scenarios.

Usage:
    async with E2EHarness(vault) as h:
        result = await h.call("read_note", path="alpha.md")
        assert result.ok

The harness:
- spawns `uv run obsidian-full-mcp --vault <vault>` in a subprocess
- pipes stdio MCP frames through `ClientSession`
- normalises tool results to a tiny dataclass (`.ok`, `.data`, `.error_code`,
  `.error_message`, `.dry_run`, `.audit_id`, `.raw`)

The server module's `__main__` is invoked through the same Python
interpreter that runs the harness, via `python -m obsidian_full_mcp` —
this avoids the cold start of `uv run` for every spawn while still
exercising the real CLI entrypoint.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_STARTUP_TIMEOUT_S = 15.0
_CALL_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class CallResult:
    """Decoded `ToolResult` envelope returned by a tool call."""

    ok: bool
    data: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    dry_run: bool
    audit_id: str | None
    raw: str  # original JSON text — kept for debugging / regex probing

    @classmethod
    def from_text(cls, text: str) -> CallResult:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:  # pragma: no cover
            raise ValueError(f"non-JSON tool output: {text[:200]}") from exc
        err = payload.get("error") or {}
        return cls(
            ok=bool(payload.get("ok")),
            data=payload.get("data"),
            error_code=err.get("code"),
            error_message=err.get("message"),
            dry_run=bool(payload.get("dry_run")),
            audit_id=payload.get("audit_id"),
            raw=text,
        )


class E2EHarness:
    """Async context manager that owns the MCP subprocess + session."""

    def __init__(
        self,
        vault: Path,
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> None:
        self.vault = vault.resolve()
        self.env_overrides = env_overrides or {}
        self._stdio_ctx: Any = None
        self._session_ctx: Any = None
        self.session: ClientSession | None = None
        self.tools: list[dict[str, Any]] = []

    async def __aenter__(self) -> E2EHarness:
        # Keep the parent env (PATH, HOME, terminal locale, virtualenv) and
        # add overrides on top. Crucially, propagate VIRTUAL_ENV so the
        # subprocess uses the same uv-managed venv as the harness.
        env = dict(os.environ)
        env.update(self.env_overrides)

        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "obsidian_full_mcp",
                "--vault",
                str(self.vault),
            ],
            env=env,
        )
        try:
            async with asyncio.timeout(_STARTUP_TIMEOUT_S):
                self._stdio_ctx = stdio_client(params)
                read, write = await self._stdio_ctx.__aenter__()
                self._session_ctx = ClientSession(read, write)
                self.session = await self._session_ctx.__aenter__()
                await self.session.initialize()
                listed = await self.session.list_tools()
                self.tools = [
                    {"name": t.name, "description": t.description}
                    for t in listed.tools
                ]
        except BaseException:
            # Startup hung or raised: tear down whatever was set up so the
            # subprocess can't survive as an orphan.
            await self._cleanup()
            raise
        return self

    async def _cleanup(self) -> None:
        """Best-effort teardown of partial state after a startup failure."""
        try:
            if self._session_ctx is not None:
                await self._session_ctx.__aexit__(None, None, None)
        except BaseException:
            pass
        finally:
            try:
                if self._stdio_ctx is not None:
                    await self._stdio_ctx.__aexit__(None, None, None)
            except BaseException:
                pass

    async def __aexit__(self, *exc: object) -> None:
        # LIFO teardown: enter order was stdio → session, so exit must be
        # session → stdio. The try/finally guarantees the stdio_ctx (which
        # owns the subprocess) is always closed even if session_ctx raises.
        try:
            if self._session_ctx is not None:
                await self._session_ctx.__aexit__(*exc)
        finally:
            if self._stdio_ctx is not None:
                await self._stdio_ctx.__aexit__(*exc)

    async def call(self, tool: str, **arguments: Any) -> CallResult:
        """Call `tool` with the given keyword arguments. Returns a decoded
        `CallResult`. Raises only on transport-level failure."""
        assert self.session is not None
        async with asyncio.timeout(_CALL_TIMEOUT_S):
            resp = await self.session.call_tool(tool, arguments)
        if resp.isError:
            # MCP-level error (e.g., unknown tool, schema violation). Surface
            # the raw text so the scenario can decide what to assert.
            text = _extract_text(resp.content) or "<no content>"
            return CallResult(
                ok=False,
                data=None,
                error_code="mcp_transport_error",
                error_message=text,
                dry_run=False,
                audit_id=None,
                raw=text,
            )
        body = _extract_text(resp.content)
        if body is None:
            raise RuntimeError(
                f"tool {tool} returned empty content: {resp!r}"
            )
        return CallResult.from_text(body)


def _extract_text(content: list[Any]) -> str | None:
    """Pull the first TextContent.text out of a CallToolResult.content list."""
    for item in content:
        # mcp.types.TextContent has type='text' + text=str
        if getattr(item, "type", None) == "text":
            return getattr(item, "text", None)
    return None
