# SPDX-License-Identifier: Apache-2.0
"""Tavily web search HTTP client — the concrete `WebSearch` used outside tests.

`tavily_search_factory` builds a function matching `core.WebSearch` that
POSTs to Tavily's `/search` endpoint with `include_answer: true` and
renders the answer plus top results (title + URL) as a single text block,
ready for injection into a task's user message. `core.py` decides WHICH
queries get searched (only a task's declared `web_queries` — see the
security invariant documented there); this module only knows how to run
one query against Tavily.
"""

from __future__ import annotations

from typing import Any

import httpx

from refresh_executor.core import WebSearch

_TAVILY_URL = "https://api.tavily.com/search"
_MAX_RESULTS = 5


def tavily_search_factory(
    api_key: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> WebSearch:
    """Build a `WebSearch` posting to Tavily's `/search` endpoint.

    `transport` is exposed so tests can inject `httpx.MockTransport`
    against a fake server instead of hitting a real network endpoint.
    """
    client = httpx.Client(transport=transport)

    def search(query: str) -> str:
        response = client.post(
            _TAVILY_URL,
            json={"api_key": api_key, "query": query, "include_answer": True},
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        lines = [f"Query: {query}"]
        answer = data.get("answer")
        if answer:
            lines.append(f"Answer: {answer}")
        for result in data.get("results", [])[:_MAX_RESULTS]:
            title = result.get("title", "")
            url = result.get("url", "")
            lines.append(f"- {title}: {url}")
        return "\n".join(lines)

    return search
