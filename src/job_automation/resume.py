from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
import hashlib
from pathlib import Path

from .models import ResumeProfile

MAX_RESUME_CHARS = 12_000


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    file_path = Path(path)

    if not file_path.is_file():
        raise FileNotFoundError(f"Resume file not found: {file_path}")

    digest = hashlib.sha256()

    with file_path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)

    return digest.hexdigest()

def extract_text(path: str | Path) -> str:
    file_path = Path(path)
    suffix = file_path.suffix.lower()

    if suffix in {".txt", ".md"}:
        return file_path.read_text(encoding="utf-8")

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError(
                "PDF support requires pypdf. "
                "Install with: pip install 'job-automation-core[pdf]'"
            ) from exc

        reader = PdfReader(str(file_path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    raise ValueError(f"Unsupported resume format: {suffix}")


def normalize_whitespace(text: str) -> str:
    """Collapse spurious single spaces inserted by pypdf between characters."""
    # Target sequences like "s o f t w a r e" (single chars separated by spaces)
    # by detecting runs where most tokens are 1-2 chars long
    text = re.sub(r'(?<!\w)(?:(\w) ){2,}(?=\w)', lambda m: m.group(0).replace(' ', ''), text)
    return re.sub(r'[ \t]+', ' ', text).strip()


def parse_resume(path: str | Path) -> ResumeProfile:
    file_path = Path(path)
    text = normalize_whitespace(extract_text(file_path))
    if len(text) > MAX_RESUME_CHARS:
        text = text[:MAX_RESUME_CHARS]
        print(f"Warning: Resume text truncated to {MAX_RESUME_CHARS} characters for LLM compatibility.")

    return ResumeProfile(
        source_file=str(file_path.resolve()),
        source_hash=sha256_file(file_path),
        extracted_at=datetime.now(timezone.utc).isoformat(),
        data={
            "text": text,
            "filename": file_path.name,
            "extension": file_path.suffix.lower(),
        },
    )
