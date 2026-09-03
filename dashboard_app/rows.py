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

# Applications older than this (no response logged) deserve a nudge.
FOLLOWUP_AFTER_DAYS = 7
# Any later event with one of these statuses counts as "responded" and stops the nudge.
RESPONSE_STATUSES = {"interview", "offer", "rejected", "withdrawn", "follow-up"}


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


def followup_rows(history: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    """Submitted applications that are old enough to nudge and have no response yet.

    Uses each job's most recent submission as the anchor, then looks for any
    later event with a response status (interview, offer, rejection, withdrawal,
    or a logged follow-up). Oldest submissions first — those need attention most.
    """
    reference = now or datetime.now(timezone.utc)
    events_by_job: dict[str, list[dict[str, Any]]] = {}
    for event in history:
        jid = job_id(event.get("job", event))
        events_by_job.setdefault(jid, []).append(event)

    rows: list[dict[str, Any]] = []
    for jid, events in events_by_job.items():
        submitted_events = [e for e in events if e.get("status") == "submitted"]
        if not submitted_events:
            continue
        latest = max(
            submitted_events,
            key=lambda e: _parse_time(e.get("timestamp", e.get("created_at", ""))) or reference,
        )
        submitted_when = _parse_time(latest.get("timestamp", latest.get("created_at", "")))
        if submitted_when is None:
            continue
        age_days = (reference - submitted_when).days
        if age_days < FOLLOWUP_AFTER_DAYS:
            continue
        responded = any(
            e.get("status") in RESPONSE_STATUSES
            and (_parse_time(e.get("timestamp", e.get("created_at", ""))) or submitted_when)
            >= submitted_when
            for e in events
            if e is not latest
        )
        if responded:
            continue
        job = latest.get("job", latest)
        rows.append({
            "company": job.get("company", "Unknown"),
            "title": job.get("title", "Untitled"),
            "score": _score(job.get("score")),
            "url": job.get("url", ""),
            "job_id": jid,
            "submitted_at": latest.get("timestamp", latest.get("created_at", "")),
            "days_since_submission": age_days,
            "job": job,
        })
    rows.sort(key=lambda row: row["days_since_submission"], reverse=True)
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
