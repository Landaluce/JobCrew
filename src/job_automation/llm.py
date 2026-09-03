"""Cheap reachability checks for the local LLM server (Ollama).

Used by the CLI (fail fast before a CrewAI run instead of waiting for a
connection error mid-kickoff) and by the dashboard sidebar health panel.
Stdlib only — no crewai or requests dependency.
"""

from __future__ import annotations

import os
from urllib.request import Request, urlopen

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


def ollama_base_url() -> str:
    return (os.getenv("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")


def llm_server_online(base_url: str | None = None, timeout: float = 2.0) -> bool:
    """Return True when the configured LLM server answers /api/tags.

    A short timeout keeps this fast enough for a dashboard rerun; failures
    (server down, wrong URL, model loading) all return False.
    """
    server = (base_url or ollama_base_url()).rstrip("/")
    request = Request(f"{server}/api/tags", method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:
        return False
