# SPDX-License-Identifier: Apache-2.0
"""LiteLLM HTTP client — the concrete `LlmComplete` used outside tests.

`litellm_complete_factory` builds a function matching `core.LlmComplete`
that POSTs to a LiteLLM (or LiteLLM-proxy-compatible) `/chat/completions`
endpoint. Cost is read from the `x-litellm-response-cost` response header
(the LiteLLM proxy convention) — a deployment that doesn't report cost
yields 0.0 rather than raising, since the executor's cost cap only makes
sense as a best-effort guard, not a hard dependency on every backend
emitting the header.
"""

from __future__ import annotations

from typing import Any

import httpx

from refresh_executor.core import LlmComplete

_COST_HEADER = "x-litellm-response-cost"


_DEFAULT_TIMEOUT_S = 120.0


def litellm_complete_factory(
    base_url: str,
    api_key: str,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> LlmComplete:
    """Build an `LlmComplete` posting `{base_url}/chat/completions`.

    `transport` is exposed so tests can inject `httpx.MockTransport`
    against a fake server instead of hitting a real network endpoint.

    `timeout_s` sets both the read and the write/pool timeout; the connect
    timeout is fixed at 10s. httpx's own default (5s total) is far too
    short for LLM completions — local reasoning routes routinely exceed
    it — so this factory always passes an explicit timeout rather than
    relying on the library default.
    """
    client = httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        transport=transport,
        timeout=httpx.Timeout(timeout_s, connect=10.0),
    )

    def complete(route: str, messages: list[dict[str, str]]) -> tuple[str, float]:
        response = client.post("/chat/completions", json={"model": route, "messages": messages})
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        text = str(data["choices"][0]["message"]["content"])
        cost = float(response.headers.get(_COST_HEADER, 0.0))
        return text, cost

    return complete
