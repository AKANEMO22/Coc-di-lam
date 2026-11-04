from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag.browserbase import BrowserbaseWebSearchTool


def _client_returning(payload: dict):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.content = b"{}"
    response.json.return_value = payload
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=response)
    return client


@pytest.mark.asyncio
async def test_browserbase_search_maps_results_without_exposing_key():
    client = _client_returning({
        "requestId": "request-1",
        "query": "FPT backend jobs",
        "results": [{
            "id": "result-1",
            "url": "https://fptsoftware.com/careers/backend",
            "title": "Backend Developer | FPT Software",
            "publishedDate": "2026-10-01T00:00:00Z",
        }],
    })

    with patch("httpx.AsyncClient", return_value=client):
        result = await BrowserbaseWebSearchTool("test-browserbase-secret").search(
            "FPT backend jobs", max_results=3
        )

    assert result["status"] == "success"
    assert result["provider"] == "browserbase"
    assert result["results"][0]["source_id"] == "result-1"
    request = client.post.await_args
    assert request.args[0] == "https://api.browserbase.com/v1/search"
    assert request.kwargs["json"]["numResults"] == 3
    assert request.kwargs["headers"]["X-BB-API-Key"] == "test-browserbase-secret"
    assert "test-browserbase-secret" not in repr(result)


@pytest.mark.asyncio
async def test_browserbase_fetch_returns_bounded_readable_text():
    client = _client_returning({
        "statusCode": 200,
        "contentType": "text/html; charset=utf-8",
        "content": "<html><body><h1>Backend Developer</h1><p>Requirements: Python and PostgreSQL.</p></body></html>",
    })

    with (
        patch("httpx.AsyncClient", return_value=client),
        patch("rag.browserbase.validate_outbound_url", return_value=True),
    ):
        result = await BrowserbaseWebSearchTool("test-browserbase-secret").search(
            "FPT backend", target_url="https://fptsoftware.com/careers/backend"
        )

    assert result["status"] == "success"
    assert result["content"].startswith("Backend Developer")
    request = client.post.await_args
    assert request.args[0] == "https://api.browserbase.com/v1/fetch"
    assert request.kwargs["json"]["format"] == "raw"
    assert request.kwargs["json"]["allowRedirects"] is False


@pytest.mark.asyncio
async def test_browserbase_fetch_retries_once_with_proxy_after_upstream_403():
    blocked = MagicMock()
    blocked.raise_for_status.return_value = None
    blocked.content = b"{}"
    blocked.json.return_value = {"statusCode": 403, "content": "blocked", "contentType": "text/html"}
    success = MagicMock()
    success.raise_for_status.return_value = None
    success.content = b"{}"
    success.json.return_value = {
        "statusCode": 200,
        "content": "<html><body><p>Requirements: Python experience.</p></body></html>",
        "contentType": "text/html",
    }
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(side_effect=[blocked, success])

    with (
        patch("httpx.AsyncClient", return_value=client),
        patch("rag.browserbase.validate_outbound_url", return_value=True),
    ):
        result = await BrowserbaseWebSearchTool("test-browserbase-secret").search(
            "FPT backend", target_url="https://fptsoftware.com/careers/backend"
        )

    assert result["status"] == "success"
    assert result["used_proxy"] is True
    assert client.post.await_count == 2
    assert client.post.await_args_list[0].kwargs["json"]["proxies"] is False
    assert client.post.await_args_list[1].kwargs["json"]["proxies"] is True


@pytest.mark.asyncio
async def test_browserbase_fetch_blocks_unsafe_target_before_provider_call():
    tool = BrowserbaseWebSearchTool("test-browserbase-secret")
    with patch("rag.browserbase.validate_outbound_url", return_value=False):
        result = await tool.search("metadata", target_url="http://127.0.0.1/latest/meta-data")

    assert result["status"] == "blocked"
    assert result["reason"] == "unsafe_target_url"
