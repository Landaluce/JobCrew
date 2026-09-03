from pathlib import Path

from job_automation.packages import load_packages, package_from_job, save_packages


def test_application_package_round_trip(tmp_path: Path) -> None:
    package = package_from_job(
        {"company": "Acme", "title": "Engineer", "location": "Remote"},
        "resume.pdf", "abc", "Dear Acme,",
    )
    destination = tmp_path / "packages.json"
    save_packages([package], destination)
    loaded = load_packages(destination)
    assert loaded[0]["cover_letter"] == "Dear Acme,"
    assert loaded[0]["status"] == "draft"
    assert loaded[0]["tailored_resume"] == ""


def test_tailored_resume_round_trip(tmp_path: Path) -> None:
    package = package_from_job(
        {"company": "Acme", "title": "Engineer", "location": "Remote"},
        "resume.pdf", "abc", "",
    )
    package.tailored_resume = "Summary: Python engineer...\n- Built APIs"
    destination = tmp_path / "packages.json"
    save_packages([package], destination)
    loaded = load_packages(destination)
    assert "Built APIs" in loaded[0]["tailored_resume"]


def test_legacy_packages_without_tailored_field_load_fine(tmp_path: Path) -> None:
    """Packages written before tailored resumes existed stay readable."""
    import json

    path = tmp_path / "packages.json"
    path.write_text(json.dumps([{"job_id": "x1", "job": {"title": "Dev"}, "status": "approved"}]), encoding="utf-8")
    loaded = load_packages(path)
    assert loaded[0].get("tailored_resume", "") == ""
