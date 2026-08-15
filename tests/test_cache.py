from pathlib import Path

from job_automation.cache import load_or_parse_resume


def test_resume_cache_is_reused_and_invalidated(tmp_path: Path) -> None:
    resume = tmp_path / "resume.txt"
    cache = tmp_path / "profile.json"
    resume.write_text("Python developer", encoding="utf-8")

    first = load_or_parse_resume(resume, cache)
    second = load_or_parse_resume(resume, cache)

    assert first.source_hash == second.source_hash
    resume.write_text("Python developer and data engineer", encoding="utf-8")
    refreshed = load_or_parse_resume(resume, cache)
    assert refreshed.source_hash != first.source_hash
    assert "data engineer" in refreshed.data["text"]
