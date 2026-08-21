from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .identity import job_id
from .models import ApplicationPackage


def save_packages(packages: list[ApplicationPackage | dict[str, Any]], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = [item.to_dict() if isinstance(item, ApplicationPackage) else item for item in packages]
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(destination)


def load_packages(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Application packages must be a JSON list")
    return payload


def package_from_job(job: dict[str, Any], resume_path: str, resume_hash: str, cover_letter: str = "") -> ApplicationPackage:
    return ApplicationPackage(
        job_id=job_id(job), job=job, cover_letter=cover_letter,
        resume_path=resume_path, resume_hash=resume_hash,
    )
