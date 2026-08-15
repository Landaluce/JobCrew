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
