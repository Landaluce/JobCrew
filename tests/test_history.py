import json
from pathlib import Path

import pytest

from job_automation.history import VALID_STATUSES, ApplicationHistory


def test_history_appends_and_finds_a_record(tmp_path: Path) -> None:
    history = ApplicationHistory(tmp_path / "history.json")
    history.append({"job_id": "job-1", "company": "Acme", "title": "Engineer", "status": "draft"})

    assert history.find_by_job_id("job-1")["company"] == "Acme"


def test_history_finds_record_by_job_dict_identity(tmp_path: Path) -> None:
    """Events written by crew.py store the job under a 'job' key, not a top-level job_id."""
    from job_automation import job_id

    job = {"url": "https://boards.greenhouse.io/acme/jobs/42?gh_src=tracking", "title": "Engineer", "company": "Acme"}
    history = ApplicationHistory(tmp_path / "history.json")
    history.append({"status": "submitted", "job": job})

    found = history.find_by_job_id(job_id(job))
    assert found is not None
    assert found["job"]["company"] == "Acme"
    assert history.find_by_job_id("does-not-exist") is None


def test_history_recovers_from_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("not json", encoding="utf-8")
    assert ApplicationHistory(path).records() == []


def test_history_rejects_unknown_status(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        ApplicationHistory(tmp_path / "history.json").append({"status": "teleported"})


def test_history_rejects_legacy_applied_status(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        ApplicationHistory(tmp_path / "history.json").append({"status": "applied"})


def test_history_normalizes_legacy_applied_on_read(tmp_path: Path) -> None:
    """Legacy 'applied' status in stored JSON is normalized to 'submitted'."""
    path = tmp_path / "history.json"
    path.write_text(json.dumps([{"status": "applied", "job": {"title": "Engineer"}}]), encoding="utf-8")
    records = ApplicationHistory(path).records()
    assert records[0]["status"] == "submitted"


def test_applied_not_in_valid_statuses() -> None:
    assert "applied" not in VALID_STATUSES


def test_history_normalizes_legacy_success_on_read(tmp_path: Path) -> None:
    """Legacy 'success' status in stored JSON is normalized to 'submitted'."""
    path = tmp_path / "history.json"
    path.write_text(json.dumps([{"status": "success", "job": {"title": "Engineer"}}]), encoding="utf-8")
    records = ApplicationHistory(path).records()
    assert records[0]["status"] == "submitted"


def test_history_rejects_legacy_success_status(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        ApplicationHistory(tmp_path / "history.json").append({"status": "success"})


def test_success_not_in_valid_statuses() -> None:
    assert "success" not in VALID_STATUSES


def test_history_normalizes_legacy_approved_not_submitted_on_read(tmp_path: Path) -> None:
    """Legacy 'approved_not_submitted' status in stored JSON becomes 'approved'."""
    path = tmp_path / "history.json"
    path.write_text(
        json.dumps([{"status": "approved_not_submitted", "job": {"title": "Engineer"}}]),
        encoding="utf-8",
    )
    records = ApplicationHistory(path).records()
    assert records[0]["status"] == "approved"


def test_history_rejects_legacy_approved_not_submitted_status(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        ApplicationHistory(tmp_path / "history.json").append({"status": "approved_not_submitted"})


def test_approved_not_submitted_not_in_valid_statuses() -> None:
    assert "approved_not_submitted" not in VALID_STATUSES


def test_history_writes_valid_json_atomically(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    history = ApplicationHistory(path)
    history.append({"status": "submitted", "job": {"title": "Engineer"}})
    assert json.loads(path.read_text(encoding="utf-8"))[0]["status"] == "submitted"
