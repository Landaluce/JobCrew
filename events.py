"""Application-history event logging (JSON history + CSV mirror)."""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from typing import Any

from job_automation import ApplicationHistory

HISTORY_JSON = "output/application_history.json"
HISTORY_CSV = "output/application_history.csv"

HISTORY_CSV_FIELDS = [
    "timestamp", "status", "title", "company", "location",
    "url", "source", "score", "site", "error",
]

history_store = ApplicationHistory(HISTORY_JSON)


def ensure_output_dir():
    os.makedirs("output", exist_ok=True)


def load_history() -> list[dict[str, Any]]:
    return history_store.records()


def sync_csv(history: list) -> None:
    ensure_output_dir()
    rows = []
    for e in history:
        job = e.get("job", {})
        details = e.get("details", {})
        rows.append({
            "timestamp": e.get("timestamp", ""),
            "status": e.get("status", ""),
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "url": job.get("url", ""),
            "source": job.get("source", ""),
            "score": job.get("score", ""),
            "site": details.get("site", ""),
            "error": details.get("error", ""),
        })
    # Always rewrite, including a header-only file when history is empty,
    # so stale rows never outlive their events.
    with open(HISTORY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def log_event(
    status: str,
    job: dict[str, Any],
    details: dict[str, Any] | None = None,
) -> None:
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "job": job,
        "details": details or {},
    }

    history_store.append(event)
    sync_csv(history_store.records())
