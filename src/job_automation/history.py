from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .identity import job_id as compute_job_id
from .models import ApplicationRecord

VALID_STATUSES = frozenset({
    "draft", "approved", "rejected", "prepared", "submitted",
    "interview", "offer", "withdrawn", "failed", "skipped_invalid_url",
    "error",
})


def _normalize_status(status: str) -> str:
    """Map legacy statuses to current canonical values."""
    if status == "applied":
        return "submitted"
    if status == "success":
        return "submitted"
    if status == "approved_not_submitted":
        return "approved"
    return status


class ApplicationHistory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

        if not isinstance(payload, list):
            raise ValueError(f"History file must contain a JSON list: {self.path}")

        for record in payload:
            if isinstance(record, dict) and "status" in record:
                record["status"] = _normalize_status(record["status"])
        return payload

    def append(self, record: ApplicationRecord | dict[str, Any]) -> None:
        item = record.to_dict() if isinstance(record, ApplicationRecord) else record
        if not isinstance(item, dict) or not isinstance(item.get("status"), str):
            raise ValueError("History records require a string status")
        if item["status"] not in VALID_STATUSES:
            raise ValueError(f"Unsupported application status: {item['status']}")
        records = self.records()
        records.append(item)
        self.replace(records)

    def replace(self, records: list[dict[str, Any]]) -> None:
        """Atomically replace history after a reviewed edit."""
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(records, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary_path.replace(self.path)

    def find_by_job_id(self, target_job_id: str) -> dict[str, Any] | None:
        """Find the first record matching a job ID.

        Matches either a top-level ``job_id`` field (``ApplicationRecord``
        style) or a ``job`` dict whose canonical URL hashes to the ID
        (the event style written by ``crew.py`` and the dashboard).
        """
        for record in self.records():
            if record.get("job_id") == target_job_id:
                return record
            job = record.get("job")
            if isinstance(job, dict) and compute_job_id(job) == target_job_id:
                return record
        return None
