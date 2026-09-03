"""Pure row builders for the dashboard tables.

These functions contain no Streamlit calls so the funnel logic (which events
need attention, days since submission, per-job package notes) can be unit
tested directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from job_automation import job_id

ATTENTION_REASON_MAP = {
    "failed": "Automation failed — review and retry or complete manually",
    "error": "Automation failed — review and retry or complete manually",
}

def _parse_time(value: Any) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when
    except (AttributeError, ValueError):
        return None


def _score(value: Any) -> Any:
    return value if isinstance(value, (int, float)) else None


def attention_rows(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Events that need a human decision (failed/error), newest job first."""
    rows: list[dict[str, Any]] = []
    seen_job_ids: set[str] = set()
    for event_index, event in enumerate(history):
        job = event.get("job", event)
        status = event.get("status", "")
        jid = job_id(job)
        if jid in seen_job_ids:
            continue
        reason = ATTENTION_REASON_MAP.get(status)
        if reason:
            seen_job_ids.add(jid)
            rows.append({
                "reason": reason,
                "status": status,
                "company": job.get("company", "Unknown"),
                "title": job.get("title", "Untitled"),
                "score": _score(job.get("score")),
                "url": job.get("url", ""),
                "timestamp": event.get("timestamp", event.get("created_at", "")),
                "job_id": jid,
                "source": "history",
                "source_index": event_index,
            })
    return rows


def submitted_rows(history: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    """Submitted applications with days-since-submission computed from the event time."""
    reference = now or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for event in history:
        if event.get("status") != "submitted":
            continue
        job = event.get("job", event)
        timestamp = event.get("timestamp", event.get("created_at", ""))
        when = _parse_time(timestamp)
        age_days = (reference - when).days if when else None
        rows.append({
            "company": job.get("company", "Unknown"),
            "title": job.get("title", "Untitled"),
            "score": _score(job.get("score")),
            "job_id": job_id(job),
            "submitted_at": timestamp,
            "days_since_submission": age_days,
            "url": job.get("url", ""),
        })
    return rows


def history_table_rows(
    history: list[dict[str, Any]],
    packages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Flat table rows for the editable History view."""
    package_notes = {p.get("job_id"): p.get("notes", "") for p in (packages or [])}
    rows: list[dict[str, Any]] = []
    for event in history:
        job = event.get("job", event)
        jid = job_id(job)
        rows.append({
            "timestamp": event.get("timestamp", event.get("created_at", "")),
            "status": event.get("status", ""),
            "company": job.get("company", ""),
            "title": job.get("title", ""),
            "score": _score(job.get("score")),
            "job_id": jid,
            "url": job.get("url", ""),
            "notes": (
                event.get("details", {}).get("note")
                or event.get("notes", "")
                or package_notes.get(jid, "")
            ),
        })
    return rows


def funnel_counts(history: list[dict[str, Any]], packages: list[dict[str, Any]]) -> dict[str, int]:
    """Counts backing the clickable metric cards."""
    return {
        "attention": len(attention_rows(history)),
        "pending": sum(package.get("status") == "draft" for package in packages),
        "ready_to_apply": sum(package.get("status") == "approved" for package in packages),
        "submitted": sum(row.get("status") == "submitted" for row in history),
        "history_events": len(history),
    }
