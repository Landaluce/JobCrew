from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .identity import job_id
from .models import ApplicationPackage


def _as_dicts(packages: Sequence[Any]) -> list[dict[str, Any]]:
    return [item.to_dict() if isinstance(item, ApplicationPackage) else item for item in packages]


def _without_timestamp(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "updated_at"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def save_packages(
    packages: Sequence[Any],
    path: str | Path,
    *,
    merge_existing: bool = False,
    removed_ids: Iterable[str] = (),
) -> None:
    """Write packages to ``path`` (atomically, via a .tmp rename).

    Default (replace) semantics match the original behavior. With
    ``merge_existing=True`` the current file is the base: rows already on disk
    that are unchanged are kept byte-for-byte, changed rows are resolved
    last-writer-wins by ``updated_at`` (a genuinely newer edit on disk beats a
    stale in-memory copy), disk-only rows survive, and ``removed_ids`` are
    deleted. This stops a whole-file rewrite from clobbering a concurrent
    writer (dashboard edits vs. a crew.py run).
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    incoming = _as_dicts(packages)

    if not merge_existing or not destination.exists():
        payload = incoming
    else:
        existing = load_packages(destination)
        removed = set(removed_ids)
        merged: list[dict[str, Any]] = []
        merged_by_id: dict[str, dict[str, Any]] = {}

        def row_id(row: dict[str, Any]) -> str:
            return str(row.get("job_id") or "")

        def append(row: dict[str, Any]) -> None:
            merged.append(row)
            key = row_id(row)
            if key:  # legacy rows without a job ID are kept but not mergeable
                merged_by_id[key] = row

        for row in existing:
            if row_id(row) not in removed:
                append(row)
        for row in incoming:
            key = row_id(row)
            if key in removed:
                continue
            current = merged_by_id.get(key)
            if current is None:
                append(row)
                continue
            if _without_timestamp(current) == _without_timestamp(row):
                continue  # unchanged copy — keep the on-disk row untouched
            # Content differs: prefer the newer edit. Ties go to the caller's
            # copy (it is the one performing a real change right now).
            current_ts = current.get("updated_at") or ""
            incoming_ts = row.get("updated_at") or ""
            if incoming_ts >= current_ts:
                row["updated_at"] = _now_iso()
                position = next(i for i, item in enumerate(merged) if item is current)
                merged[position] = row
                merged_by_id[key] = row
        payload = merged

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


def package_from_job(
    job: dict[str, Any], resume_path: str, resume_hash: str, cover_letter: str = ""
) -> ApplicationPackage:
    return ApplicationPackage(
        job_id=job_id(job), job=job, cover_letter=cover_letter,
        resume_path=resume_path, resume_hash=resume_hash,
    )


def find_duplicate_groups(packages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group packages that share a job ID (i.e. the same canonical URL).

    Repeated searches re-create packages for jobs that are still open; those
    duplicates live side by side in ``application_packages.json``. Returns
    only groups with more than one member, preserving file order.
    """
    by_job_id: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for package in packages:
        package_id = str(package.get("job_id") or "")
        if not package_id:
            continue
        if package_id not in by_job_id:
            order.append(package_id)
        by_job_id.setdefault(package_id, []).append(package)
    return [by_job_id[package_id] for package_id in order if len(by_job_id[package_id]) > 1]


_LIFECYCLE_RANK = {"draft": 0, "approved": 1, "prepared": 2, "submitted": 3}


def _duplicate_keeper(group: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the richest package to keep from a duplicate group.

    Prefers later lifecycle status, then content (cover letter, tailored
    resume, answers), then the earliest-created package on ties.
    """

    def score(package: dict[str, Any]) -> tuple[int, int, str]:
        status_rank = _LIFECYCLE_RANK.get(str(package.get("status")), 0)
        content = sum(1 for key in ("cover_letter", "tailored_resume", "answers") if package.get(key))
        created = str(package.get("created_at") or "")
        return (status_rank, content, created)

    return max(group, key=score)


def dedupe_packages(packages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge duplicate packages, returning (kept_packages, removed_packages).

    Only the ``application_packages`` list is affected; history events are a
    separate stream and are never deleted here.
    """
    removed: list[dict[str, Any]] = []
    for group in find_duplicate_groups(packages):
        keeper = _duplicate_keeper(group)
        removed.extend(package for package in group if package is not keeper)
    if not removed:
        return list(packages), []
    # Preserve the original relative order of the non-duplicate packages.
    duplicate_ids = {id(package) for package in removed}
    kept_all = [package for package in packages if id(package) not in duplicate_ids]
    return kept_all, removed
