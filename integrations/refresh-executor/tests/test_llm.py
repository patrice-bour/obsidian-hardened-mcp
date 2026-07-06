"""`litellm_complete_factory` — LiteLLM HTTP client, tested via `httpx.MockTransport`."""

from __future__ import annotations

import json

import httpx
import pytest

from refresh_executor.llm import litellm_complete_factory


def _handler(content: str, *, cost_header: str | None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        headers = {"x-litellm-response-cost": cost_header} if cost_header is not None else {}
        return httpx.Response(
            200,
            headers=headers,
            json={"choices": [{"message": {"content": content}}]},
        )

    return httpx.MockTransport(handle)


class TestLitellmCompleteFactory:
    def test_extracts_text_and_cost_from_header(self) -> None:
        transport = _handler("Refreshed body text.", cost_header="0.0123")
        complete = litellm_complete_factory("http://fake", "key", transport=transport)

        text, cost = complete("local-thinker", [{"role": "user", "content": "hi"}])

        assert text == "Refreshed body text."
        assert cost == pytest.approx(0.0123)

    def test_missing_cost_header_defaults_to_zero(self) -> None:
        transport = _handler("Refreshed body text.", cost_header=None)
        complete = litellm_complete_factory("http://fake", "key", transport=transport)

        _, cost = complete("local-thinker", [{"role": "user", "content": "hi"}])

        assert cost == 0.0

    def test_posts_route_as_model_and_forwards_messages(self) -> None:
        captured: dict[str, object] = {}

        def handle(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

        transport = httpx.MockTransport(handle)
        complete = litellm_complete_factory("http://fake", "key", transport=transport)
        messages = [{"role": "user", "content": "hi"}]

        complete("cloud-x", messages)

        assert captured["body"] == {"model": "cloud-x", "messages": messages}
