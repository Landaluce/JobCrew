from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def canonical_url(url: str) -> str:
    """Remove tracking/query data so the same listing has one stable identity."""
    value = url.strip()
    if not value:
        return ""
    parts = urlsplit(value)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def job_id(job: dict[str, Any]) -> str:
    source = canonical_url(str(job.get("url", "")))
    if not source:
        fields = (job.get("company", ""), job.get("title", ""), job.get("location", ""))
        source = "|".join(re.sub(r"\s+", " ", str(item).strip().lower()) for item in fields)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
