"""Direct Serper.dev search client (stdlib only).

The crew-based search step cannot be used with local models: crewai never
executes the Serper tool call a model emits (the tool call becomes the agent's
final answer), so agents fabricate URLs instead of reporting real results.
Calling the Serper API directly keeps job discovery deterministic — URLs come
from Google organic results, not model output.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.request
from typing import Any

SERPER_ENDPOINT = "https://google.serper.dev/search"

DEFAULT_GL = "us"


class SerperError(RuntimeError):
    """Raised when the Serper API is unavailable or rejects the request."""


def serper_search(
    query: str,
    num: int = 10,
    gl: str = DEFAULT_GL,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Run a web search and return the organic results.

    Each result is ``{"title", "link", "snippet", "position"}``. Raises
    ``SerperError`` when the API key is missing or the request fails.
    """
    key = (api_key or os.environ.get("SERPER_API_KEY", "")).strip()
    if not key:
        raise SerperError(
            "SERPER_API_KEY is not set. Add it to .env (https://serper.dev)"
        )

    body = json.dumps({"q": query, "gl": gl, "num": num}).encode("utf-8")
    request = urllib.request.Request(
        SERPER_ENDPOINT,
        data=body,
        headers={
            "X-API-KEY": key,
            "Content-Type": "application/json",
        },
    )
    # Same relaxed TLS handling as the rest of the pipeline's fetch helpers.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(request, timeout=25, context=ctx) as response:
            payload = json.loads(response.read().decode("utf-8", errors="ignore"))
    except Exception as exc:
        raise SerperError(f"Serper request failed: {exc}") from exc

    results: list[dict[str, Any]] = []
    for item in payload.get("organic", []):
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        if not link:
            continue
        results.append({
            "title": str(item.get("title") or "").strip(),
            "link": link,
            "snippet": str(item.get("snippet") or "").strip()[:300],
            "position": item.get("position"),
        })
    return results
