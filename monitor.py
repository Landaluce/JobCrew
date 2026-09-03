"""Report days since submission for submitted applications."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from job_automation import ApplicationHistory


def _event_time(event: dict[str, Any]) -> datetime | None:
    timestamp = event.get("timestamp", event.get("created_at", ""))
    try:
        when = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when
    except (AttributeError, ValueError):
        return None


def submitted_summary(records: list[dict[str, Any]], now: datetime | None = None) -> list[str]:
    """Human-readable lines for submitted applications with their age in days."""
    reference = now or datetime.now(timezone.utc)
    submitted = [r for r in records if r.get("status") == "submitted"]
    if not submitted:
        return []

    lines = [f"Submitted applications ({len(submitted)}):"]
    for event in submitted:
        job = event.get("job", event)
        company = job.get("company", "Unknown")
        title = job.get("title", "Untitled")
        timestamp = event.get("timestamp", event.get("created_at", ""))
        when = _event_time(event)
        if when is None:
            days_str = "unknown age"
        else:
            days_str = f"{(reference - when).days} days ago"
        lines.append(f"- {company} — {title} ({timestamp}) — {days_str}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Report days since application submission")
    parser.add_argument("--history", default="output/application_history.json")
    args = parser.parse_args()

    history = ApplicationHistory(Path(args.history))
    lines = submitted_summary(history.records())
    if lines:
        print("\n".join(lines))
    else:
        print("No submitted applications found.")


if __name__ == "__main__":
    main()
