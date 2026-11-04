"""Browserbase Search + Fetch adapter for public ID_Q research."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from core.profiler.tools import _readable_page_text, validate_outbound_url


logger = logging.getLogger(__name__)


class BrowserbaseWebSearchTool:
    """Expose Browserbase's Search and Fetch APIs through the local search-tool contract."""

    provider_name = "browserbase"
    api_url = "https://api.browserbase.com"

    def __init__(self, api_key: str | None, *, timeout: float = 10.0, max_bytes: int = 6 * 1024 * 1024):
        self.api_key = str(api_key or "").strip()
        self.timeout = max(1.0, float(timeout))
        self.max_bytes = max(64 * 1024, int(max_bytes))

    async def search(
        self,
        query: str,
        target_url: str | None = None,
        max_results: int = 5,
        **_: Any,
    ) -> dict[str, Any]:
        if not self.api_key:
            return {
                "status": "unavailable",
                "reason": "search_provider_not_configured",
                "provider": self.provider_name,
                "query": str(query)[:200],
            }
        if target_url:
            return await self._fetch(str(query), target_url)
        return await self._search(str(query), max_results)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-BB-API-Key": self.api_key,
        }

    async def _search(self, query: str, max_results: int) -> dict[str, Any]:
        clean_query = " ".join(query.split())[:200]
        if not clean_query:
            return {"status": "error", "reason": "empty_query", "provider": self.provider_name}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.post(
                    f"{self.api_url}/v1/search",
                    headers=self._headers,
                    json={"query": clean_query, "numResults": max(1, min(int(max_results), 25))},
                )
                response.raise_for_status()
                if len(response.content) > self.max_bytes:
                    return {"status": "error", "reason": "provider_response_too_large", "query": clean_query}
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("Browserbase Search returned HTTP %s", exc.response.status_code)
            return {
                "status": "error",
                "reason": f"provider_http_{exc.response.status_code}",
                "provider": self.provider_name,
                "query": clean_query,
            }
        except Exception as exc:
            logger.warning("Browserbase Search request failed: %s", type(exc).__name__)
            return {
                "status": "error",
                "reason": "provider_request_failed",
                "provider": self.provider_name,
                "query": clean_query,
            }

        collected_at = datetime.now(timezone.utc).isoformat()
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for position, item in enumerate(payload.get("results") or []):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            title = str(item.get("title") or "").strip()[:400]
            content_hash = hashlib.sha256(f"{url}\n{title}".encode("utf-8")).hexdigest()
            results.append({
                "source_id": str(item.get("id") or f"web_{content_hash[:20]}")[:200],
                "title": title,
                "snippet": "",
                "url": url,
                "source": url,
                "source_type": "search_result",
                "trust_tier": "unreviewed",
                "verification_status": "unreviewed",
                "query": clean_query,
                "position": position + 1,
                "content_hash": content_hash,
                "published_at": item.get("publishedDate"),
                "author": item.get("author"),
                "collected_at": collected_at,
            })
            if len(results) >= max(1, min(int(max_results), 25)):
                break
        return {
            "status": "success",
            "provider": self.provider_name,
            "request_id": payload.get("requestId"),
            "query": str(payload.get("query") or clean_query),
            "results": results,
            "collected_at": collected_at,
        }

    async def _fetch(self, query: str, target_url: str) -> dict[str, Any]:
        if not await asyncio.to_thread(validate_outbound_url, target_url):
            return {
                "status": "blocked",
                "reason": "unsafe_target_url",
                "provider": self.provider_name,
                "url": target_url[:500],
            }
        try:
            payload: dict[str, Any] = {}
            used_proxy = False
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                for use_proxy in (False, True):
                    response = await client.post(
                        f"{self.api_url}/v1/fetch",
                        headers=self._headers,
                        json={
                            "url": target_url,
                            # Raw is cheaper than Fetch Extract and lets the local bounded
                            # parser apply the same deterministic cleanup as direct fetches.
                            "format": "raw",
                            "allowRedirects": False,
                            "allowInsecureSsl": False,
                            "proxies": use_proxy,
                        },
                    )
                    response.raise_for_status()
                    if len(response.content) > self.max_bytes:
                        return {"status": "unavailable", "reason": "provider_response_too_large", "url": target_url}
                    payload = response.json()
                    upstream_status = int(payload.get("statusCode") or 0)
                    used_proxy = use_proxy
                    if upstream_status != 403 or use_proxy:
                        break
        except httpx.HTTPStatusError as exc:
            logger.warning("Browserbase Fetch returned HTTP %s", exc.response.status_code)
            return {
                "status": "error",
                "reason": f"provider_http_{exc.response.status_code}",
                "provider": self.provider_name,
                "url": target_url,
            }
        except Exception as exc:
            logger.warning("Browserbase Fetch request failed: %s", type(exc).__name__)
            return {
                "status": "error",
                "reason": "provider_request_failed",
                "provider": self.provider_name,
                "url": target_url,
            }
        upstream_status = int(payload.get("statusCode") or 0)
        if not 200 <= upstream_status < 300:
            return {
                "status": "unavailable",
                "reason": f"upstream_http_{upstream_status or 'unknown'}",
                "provider": self.provider_name,
                "url": target_url,
            }
        raw_content = payload.get("content")
        if not isinstance(raw_content, str) or not raw_content.strip():
            return {"status": "unavailable", "reason": "no_readable_page_text", "url": target_url}
        content_type = str(payload.get("contentType") or "")
        encoded = raw_content.encode("utf-8", errors="replace")
        content = _readable_page_text(encoded, content_type)
        if not content:
            return {"status": "unavailable", "reason": "no_readable_page_text", "url": target_url}
        fetched_at = datetime.now(timezone.utc).isoformat()
        return {
            "status": "success",
            "provider": self.provider_name,
            "query": query[:200],
            "url": target_url,
            "status_code": upstream_status,
            "content": content[:8_000],
            "content_hash": hashlib.sha256(encoded).hexdigest(),
            "content_type": content_type,
            "truncated": len(content) > 8_000,
            "total_bytes_read": len(encoded),
            "fetched_at": fetched_at,
            "used_proxy": used_proxy,
        }
