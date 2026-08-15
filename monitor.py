"""Report applications that need a follow-up without changing their status."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from job_automation import ApplicationHistory


def main() -> None:
    parser = argparse.ArgumentParser(description="List applications that may need follow-up")
    parser.add_argument("--history", default="output/application_history.json")
    parser.add_argument("--after-days", type=int, default=7, help="Flag submitted applications older than this")
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.after_days)
    due = []
    for event in ApplicationHistory(Path(args.history)).records():
        if event.get("status") not in {"submitted", "applied", "success"}:
            continue
        timestamp = event.get("timestamp", event.get("created_at", ""))
        try:
            when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if when <= cutoff:
            job = event.get("job", event)
            due.append(f"{job.get('company', 'Unknown')} — {job.get('title', 'Untitled')} ({timestamp})")

    if due:
        print("Follow-up candidates:")
        print("\n".join(f"- {item}" for item in due))
    else:
        print("No follow-up candidates found.")


if __name__ == "__main__":
    main()
