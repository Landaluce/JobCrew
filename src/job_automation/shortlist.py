"""Resilient parsing of LLM job-shortlist output.

Small local models (the default Ollama ``llama3.2:3b``) frequently wrap JSON
in markdown fences, add prose around it, or return slightly malformed JSON.
These helpers recover a ``{"jobs": [...]}`` payload from free-form agent
output without requiring pydantic or crewai, so the recovery logic can be
unit-tested in isolation.
"""

from __future__ import annotations

import json
import re
from typing import Any

JOB_FIELDS = ("title", "company", "location", "url", "score", "rationale")


def _find_balanced_json(text: str) -> dict[str, Any] | None:
    """Return the first parseable JSON object found anywhere in ``text``."""
    start = 0
    while True:
        open_index = text.find("{", start)
        if open_index == -1:
            return None
        depth = 0
        in_string = False
        escaped = False
        for index in range(open_index, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[open_index : index + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break
        start = open_index + 1


def extract_jobs_from_text(text: str) -> list[dict[str, Any]]:
    """Recover a list of job dicts from raw LLM output.

    Accepts either a ``{"jobs": [...]}`` envelope or a bare JSON array of
    job objects, wherever they appear in the text (including inside markdown
    fences or surrounded by prose). Returns ``[]`` when nothing parseable
    exists so callers can fall back to another source.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    payload = _find_balanced_json(text)
    if payload is not None:
        jobs = payload.get("jobs")
        if isinstance(jobs, list):
            return [entry for entry in jobs if isinstance(entry, dict)]
        # Tolerate an envelope like {"job_list": [...]} or {"results": [...]}
        for key in ("job_list", "results", "items", "shortlist"):
            value = payload.get(key)
            if isinstance(value, list):
                return [entry for entry in value if isinstance(entry, dict)]

    # Some models reply with a bare JSON array instead of an object. When the
    # first balanced object was just one entry of such an array (or an object
    # that carried no list), try to parse the enclosing array wholesale.
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            array = json.loads(match.group(0))
            if isinstance(array, list):
                return [entry for entry in array if isinstance(entry, dict)]
        except json.JSONDecodeError:
            pass
    return []


def normalize_job_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Coerce one job dict into clean, typed fields."""
    cleaned: dict[str, Any] = {}
    for field in JOB_FIELDS:
        value = entry.get(field)
        if field == "score":
            try:
                cleaned[field] = min(100.0, max(0.0, float(value or 0.0)))
            except (TypeError, ValueError):
                cleaned[field] = 0.0
        else:
            cleaned[field] = str(value or "").strip() if value is not None else ""
    return cleaned


def well_formed_job(entry: dict[str, Any]) -> bool:
    """A usable job entry must at least carry a URL and a title or company."""
    url = str(entry.get("url") or "").strip()
    title = str(entry.get("title") or "").strip()
    company = str(entry.get("company") or "").strip()
    if not url:
        return False
    if url.lower() in {"https://example.com", "http://example.com", "n/a", "null", "none"}:
        return False
    return bool(title or company)


def recover_jobs_from_text(text: str) -> list[dict[str, Any]]:
    """Best-effort full recovery: parse, clean, and keep usable entries."""
    return [normalize_job_entry(entry) for entry in extract_jobs_from_text(text) if well_formed_job(entry)]
