"""Tests for rest.client — httpx wrapper for the Obsidian Local REST API.

The plugin ships with a self-signed certificate for `127.0.0.1`; we
therefore default `verify=False` and rely on the loopback constraint
plus bearer auth for security. The token MUST never appear in `repr`
or in any exception message.

Tests use `httpx.MockTransport` so we don't depend on a live Obsidian
process — the production code path is the same one httpx serves; the
transport layer is the only seam.
"""

from __future__ import annotations

import httpx
import pytest

from obsidian_hardened_mcp.rest.client import (
    RestAuthError,
    RestClient,
    RestError,
    RestUnavailableError,
)


def _client(handler, token: str | None = "tok") -> RestClient:
    transport = httpx.MockTransport(handler)
    return RestClient(
        "https://127.0.0.1:27124",
        token,
        timeout_seconds=0.5,
        transport=transport,
    )


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_returns_true_on_200(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.headers["Authorization"] == "Bearer tok"
            assert req.method == "GET"
            return httpx.Response(200, json={"status": "OK"})

        assert _client(handler).health_check() is True

    def test_raises_auth_on_401(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorised"})

        with pytest.raises(RestAuthError):
            _client(handler).health_check()

    def test_raises_auth_on_403(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(403)

        with pytest.raises(RestAuthError):
            _client(handler).health_check()

    def test_raises_unavailable_on_connect_error(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(RestUnavailableError):
            _client(handler).health_check()

    def test_raises_unavailable_on_read_timeout(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        with pytest.raises(RestUnavailableError):
            _client(handler).health_check()

    def test_raises_rest_error_on_5xx(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        with pytest.raises(RestError):
            _client(handler).health_check()


# ---------------------------------------------------------------------------
# execute_command
# ---------------------------------------------------------------------------


class TestExecuteCommand:
    def test_posts_to_command_endpoint(self) -> None:
        captured: dict[str, object] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["method"] = req.method
            captured["path"] = req.url.path
            captured["auth"] = req.headers.get("Authorization")
            return httpx.Response(200, json={"ok": True})

        result = _client(handler).execute_command("editor:focus-current")
        assert result == {"ok": True}
        assert captured["method"] == "POST"
        # The plugin uses `/commands/{id}/` (trailing slash). Allow with or
        # without to be robust to plugin version changes; assert the id is
        # in the path.
        assert "editor:focus-current" in captured["path"]  # type: ignore[operator]
        assert captured["auth"] == "Bearer tok"

    def test_returns_empty_dict_on_204_no_content(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(204)

        assert _client(handler).execute_command("foo") == {}

    def test_raises_auth_on_401(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        with pytest.raises(RestAuthError):
            _client(handler).execute_command("foo")

    def test_raises_unavailable_on_connect_error(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nope")

        with pytest.raises(RestUnavailableError):
            _client(handler).execute_command("foo")

    def test_raises_rest_error_on_5xx(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(502, json={"error": "bad gateway"})

        with pytest.raises(RestError):
            _client(handler).execute_command("foo")

    def test_empty_command_id_rejected(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise AssertionError("client must reject before sending")

        with pytest.raises(ValueError, match="command_id"):
            _client(handler).execute_command("")


# ---------------------------------------------------------------------------
# Token masking — never leaked in repr OR error messages
# ---------------------------------------------------------------------------


class TestTokenMasking:
    def test_repr_masks_token(self) -> None:
        client = RestClient(
            "https://127.0.0.1:27124", "secret-bearer-value-do-not-leak"
        )
        text = repr(client)
        assert "secret-bearer-value-do-not-leak" not in text
        assert "127.0.0.1:27124" in text
        # Some indication that the token is present but masked.
        assert "***" in text or "REDACTED" in text or "<masked>" in text

    def test_no_token_repr(self) -> None:
        client = RestClient("https://127.0.0.1:27124", None)
        text = repr(client)
        assert "127.0.0.1:27124" in text

    def test_auth_error_message_does_not_leak_token(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        try:
            _client(handler, token="this-must-stay-private").health_check()
        except RestAuthError as exc:
            assert "this-must-stay-private" not in str(exc)
        else:
            raise AssertionError("expected RestAuthError")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_close_is_idempotent(self) -> None:
        client = RestClient("https://x", "tok")
        client.close()
        # Calling close twice must not raise.
        client.close()

    def test_context_manager(self) -> None:
        with RestClient("https://x", "tok") as client:
            assert isinstance(client, RestClient)
        # Subsequent close is a no-op.
        client.close()
