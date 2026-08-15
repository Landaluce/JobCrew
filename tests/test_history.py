import json
from pathlib import Path

import pytest

from job_automation.history import ApplicationHistory


def test_history_appends_and_finds_a_record(tmp_path: Path) -> None:
    history = ApplicationHistory(tmp_path / "history.json")
    history.append({"job_id": "job-1", "company": "Acme", "title": "Engineer", "status": "draft"})

    assert history.find_by_job_id("job-1")["company"] == "Acme"


def test_history_recovers_from_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("not json", encoding="utf-8")
    assert ApplicationHistory(path).records() == []


def test_history_rejects_unknown_status(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        ApplicationHistory(tmp_path / "history.json").append({"status": "teleported"})


def test_history_writes_valid_json_atomically(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    history = ApplicationHistory(path)
    history.append({"status": "submitted", "job": {"title": "Engineer"}})
    assert json.loads(path.read_text(encoding="utf-8"))[0]["status"] == "submitted"
