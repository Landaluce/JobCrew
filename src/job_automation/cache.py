from __future__ import annotations

import json
from pathlib import Path

from .models import ResumeProfile
from .resume import parse_resume, sha256_file


def save_profile(profile: ResumeProfile, cache_path: str | Path) -> None:
    destination = Path(cache_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(profile.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_profile(cache_path: str | Path) -> ResumeProfile | None:
    path = Path(cache_path)

    if not path.is_file():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ResumeProfile(**payload)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def load_or_parse_resume(
    resume_path: str | Path,
    cache_path: str | Path,
) -> ResumeProfile:
    resume_path = Path(resume_path)
    cached = load_profile(cache_path)

    if cached is not None:
        current_hash = sha256_file(resume_path)

        if cached.source_hash == current_hash:
            return cached

    profile = parse_resume(resume_path)
    save_profile(profile, cache_path)
    return profile
