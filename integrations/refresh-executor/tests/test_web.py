"""`tavily_search_factory` — Tavily web search HTTP client, tested via `httpx.MockTransport`."""

from __future__ import annotations

import json

import httpx
import pytest

from refresh_executor.web import tavily_search_factory


def _handler(payload: dict[str, object]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handle)


class TestTavilySearchFactory:
    def test_extracts_answer_and_results(self) -> None:
        transport = _handler(
            {
                "answer": "The answer.",
                "results": [
                    {"title": "First", "url": "https://a.example"},
                    {"title": "Second", "url": "https://b.example"},
                ],
            }
        )
        search = tavily_search_factory("key", transport=transport)

        text = search("some query")

        assert "The answer." in text
        assert "First" in text and "https://a.example" in text
        assert "Second" in text and "https://b.example" in text

    def test_missing_answer_omits_answer_line(self) -> None:
        transport = _handler({"results": []})
        search = tavily_search_factory("key", transport=transport)

        text = search("some query")

        assert "Answer:" not in text

    def test_posts_query_and_include_answer_true(self) -> None:
        captured: dict[str, object] = {}

        def handle(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"answer": "x", "results": []})

        transport = httpx.MockTransport(handle)
        search = tavily_search_factory("key", transport=transport)

        search("some query")

        body = captured["body"]
        assert isinstance(body, dict)
        assert body["query"] == "some query"
        assert body["include_answer"] is True
        assert body["api_key"] == "key"

    def test_caps_results_and_query_appears_in_output(self) -> None:
        results = [
            {"title": f"Result {i}", "url": f"https://example.com/{i}"} for i in range(10)
        ]
        transport = _handler({"answer": "", "results": results})
        search = tavily_search_factory("key", transport=transport)

        text = search("bounded query")

        assert "bounded query" in text
        assert "Result 0" in text
        assert "Result 9" not in text


class TestTavilySearchFactoryTimeout:
    def test_default_timeout_is_120s_with_10s_connect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}
        real_client = httpx.Client

        def spy_client(*args: object, **kwargs: object) -> httpx.Client:
            captured.update(kwargs)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", spy_client)
        transport = _handler({"answer": "", "results": []})

        tavily_search_factory("key", transport=transport)

        timeout = captured["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read == 120.0
        assert timeout.connect == 10.0

    def test_timeout_s_is_configurable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}
        real_client = httpx.Client

        def spy_client(*args: object, **kwargs: object) -> httpx.Client:
            captured.update(kwargs)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", spy_client)
        transport = _handler({"answer": "", "results": []})

        tavily_search_factory("key", transport=transport, timeout_s=5.0)

        timeout = captured["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read == 5.0
        assert timeout.connect == 10.0
