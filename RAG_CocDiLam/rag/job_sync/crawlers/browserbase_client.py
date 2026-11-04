"""Browserbase API client for fetching and searching web content."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger("app.job_sync.browserbase")

BROWSERBASE_API_KEY = os.environ.get("BROWSERBASE_API_KEY", "bb_live_j2y9DFpeZIeZZDz3H3BZdH5snEE")
API_BASE_URL = "https://api.browserbase.com"


class BrowserbaseClient:
    """HTTP Client interacting with Browserbase Cloud Platform."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or BROWSERBASE_API_KEY).strip()
        self.headers = {
            "X-BB-API-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def fetch(self, url: str, timeout: float = 30.0) -> str:
        """Fetch real rendered page content via Browserbase Cloud Fetch API.
        Bypasses standard client IP blocking and headers validation.
        """
        if not self.api_key:
            logger.warning("Browserbase API key is missing. Skipping Browserbase Fetch.")
            return ""

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{API_BASE_URL}/v1/fetch",
                    headers=self.headers,
                    json={"url": url},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get("content", "")
                    logger.info(f"Browserbase Fetch succeeded for {url} ({len(content)} bytes)")
                    return content
                else:
                    logger.warning(f"Browserbase Fetch returned HTTP {resp.status_code} for {url}")
                    return ""
        except Exception as exc:
            logger.warning(f"Browserbase Fetch error for {url}: {exc}")
            return ""

    async def search(self, query: str, num_results: int = 10, timeout: float = 20.0) -> List[Dict[str, Any]]:
        """Search web pages using Browserbase Search API."""
        if not self.api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{API_BASE_URL}/v1/search",
                    headers=self.headers,
                    json={"query": query, "numResults": max(1, min(num_results, 25))},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("results", [])
                else:
                    logger.warning(f"Browserbase Search returned HTTP {resp.status_code} for '{query}'")
                    return []
        except Exception as exc:
            logger.warning(f"Browserbase Search error: {exc}")
            return []

    async def create_session(self, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Start a real cloud browser session on Browserbase."""
        if not self.api_key:
            return None
        payload: Dict[str, Any] = {}
        if project_id:
            payload["projectId"] = project_id
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{API_BASE_URL}/v1/sessions",
                    headers=self.headers,
                    json=payload,
                )
                if resp.status_code in (200, 201):
                    return resp.json()
                logger.warning(f"Browserbase Create Session returned HTTP {resp.status_code}")
                return None
        except Exception as exc:
            logger.warning(f"Browserbase Create Session error: {exc}")
            return None


# Global shared instance
browserbase_client = BrowserbaseClient()
