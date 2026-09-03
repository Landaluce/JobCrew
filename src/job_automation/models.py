from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ResumeProfile:
    source_file: str
    source_hash: str
    extracted_at: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApplicationRecord:
    job_id: str
    company: str
    title: str
    status: str
    url: str | None = None
    notes: str | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApplicationPackage:
    """A reviewable, self-contained application prepared for one job."""

    job_id: str
    job: dict[str, Any]
    cover_letter: str
    resume_path: str
    resume_hash: str
    answers: dict[str, str] = field(default_factory=dict)
    status: str = "draft"
    notes: str = ""
    tailored_resume: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
