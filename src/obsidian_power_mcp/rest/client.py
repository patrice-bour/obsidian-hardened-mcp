"""Thin httpx wrapper for the Obsidian Local REST API plugin.

The plugin listens on `https://127.0.0.1:27124` by default and uses a
**self-signed certificate** for the loopback hostname. We accept that
posture (`verify=False`) because:
- The endpoint is loopback only — an attacker that can speak to it
  already has process-level access to the user's machine.
- The bearer token is what actually authenticates the call.

The token MUST NEVER appear in `repr()`, in any exception message, or
in audit logs. Network failures map to a small error taxonomy:
- `RestUnavailableError` — the API isn't reachable (refused, timeout,
  DNS).
- `RestAuthError` — 401 / 403 from the plugin.
- `RestError` — 4xx (other) / 5xx / malformed response.
"""

from __future__ import annotations

from typing import Any

import httpx


class RestUnavailableError(Exception):
    """The REST endpoint is not reachable (connection refused, timeout)."""


class RestAuthError(Exception):
    """The REST endpoint refused the call (401/403)."""


class RestError(Exception):
    """Any other REST failure (5xx, malformed response, unexpected 4xx)."""


class RestClient:
    """Minimal client for the Obsidian Local REST API plugin.

    `transport` is an extension point used by the test suite; production
    callers leave it `None` so httpx uses its default transport.
    """

    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        timeout_seconds: float = 0.5,
        verify_tls: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._token = token
        headers: dict[str, str] = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            verify=verify_tls,
            transport=transport,
            headers=headers,
        )
        self._closed = False

    # -- requests --------------------------------------------------------

    def health_check(self) -> bool:
        """Probe `GET /`. Returns True on 2xx; raises otherwise."""
        try:
            response = self._client.get("/")
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise RestUnavailableError(
                f"REST endpoint unreachable: {exc}"
            ) from exc
        except httpx.RequestError as exc:
            # Other transport-level errors (DNS, network) -> unavailable.
            raise RestUnavailableError(
                f"REST request error: {exc}"
            ) from exc
        self._raise_for_status(response)
        return True

    def execute_command(self, command_id: str) -> dict[str, Any]:
        """POST `/commands/<id>/`. Returns the parsed JSON body, or `{}`
        on a 204. Raises the standard error taxonomy on failure."""
        if not command_id or not command_id.strip():
            raise ValueError("command_id must be a non-empty string")
        path = f"/commands/{command_id}/"
        try:
            response = self._client.post(path)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise RestUnavailableError(
                f"REST endpoint unreachable: {exc}"
            ) from exc
        except httpx.RequestError as exc:
            raise RestUnavailableError(
                f"REST request error: {exc}"
            ) from exc
        self._raise_for_status(response)
        if response.status_code == 204 or not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise RestError(
                f"REST returned non-JSON body for {path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise RestError(
                f"REST returned non-object body for {path}: {type(data).__name__}"
            )
        return data

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._client.close()

    def __enter__(self) -> RestClient:
        return self

    def __exit__(self, *_excinfo: object) -> None:
        self.close()

    def __repr__(self) -> str:
        masked = "***" if self._token else "<no token>"
        return f"RestClient(base_url={self._base_url!r}, token={masked!r})"

    # -- internals -------------------------------------------------------

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise RestAuthError(
                f"REST endpoint rejected credentials (status "
                f"{response.status_code})"
            )
        if response.status_code >= 400:
            raise RestError(
                f"REST endpoint returned status {response.status_code}"
            )
