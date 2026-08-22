"""Report days since submission for submitted applications."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from job_automation import ApplicationHistory


def main() -> None:
    parser = argparse.ArgumentParser(description="Report days since application submission")
    parser.add_argument("--history", default="output/application_history.json")
    args = parser.parse_args()

    history = ApplicationHistory(Path(args.history))
    records = history.records()

    submitted = [r for r in records if r.get("status") in {"submitted", "success"}]

    if not submitted:
        print("No submitted applications found.")
        return

    now = datetime.now(timezone.utc)
    print(f"Submitted applications ({len(submitted)}):")
    for event in submitted:
        job = event.get("job", event)
        company = job.get("company", "Unknown")
        title = job.get("title", "Untitled")
        timestamp = event.get("timestamp", event.get("created_at", ""))
        try:
            when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            days = (now - when).days
            days_str = f"{days} days ago"
        except (TypeError, ValueError, AttributeError):
            days_str = "unknown age"
        print(f"- {company} — {title} ({timestamp}) — {days_str}")


if __name__ == "__main__":
    main()
