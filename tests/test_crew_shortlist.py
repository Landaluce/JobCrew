"""Tests for crew.py's shortlist recovery (structured pydantic output, raw-text
fallback, and repair-less failure), using fake task outputs so no LLM or
pydantic instance is required."""

from types import SimpleNamespace

import pytest

import crew


class _DummyJob:
    def __init__(self, **fields: object) -> None:
        self._fields = fields

    def model_dump(self) -> dict:
        return dict(self._fields)


class _DummyJobList:
    """Drop-in replacement for crew.JobList used via monkeypatch."""

    def __init__(self, jobs: list[_DummyJob]) -> None:
        self.jobs = jobs


def _task(pydantic: object = None, raw: str = "") -> SimpleNamespace:
    return SimpleNamespace(pydantic=pydantic, raw=raw)


def _result(*tasks: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(tasks_output=list(tasks))


JOBS = [
    {"title": "Backend Engineer", "company": "Acme", "url": "https://boards.greenhouse.io/acme/jobs/42", "score": 91},
    {"title": "Data Engineer", "company": "Globex", "url": "https://jobs.lever.co/globex/role/7", "score": 78.5},
]


def test_finds_structured_shortlist_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crew, "JobList", _DummyJobList)
    result = _result(
        _task(raw="resume analysis..."),
        _task(pydantic=_DummyJobList([_DummyJob(**job) for job in JOBS]), raw="ranked"),
    )
    shortlist = crew.find_shortlist_in_result(result)
    assert [job["url"] for job in shortlist] == [job["url"] for job in JOBS]


def test_falls_back_to_raw_text_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crew, "JobList", _DummyJobList)
    result = _result(
        _task(raw="resume analysis..."),
        _task(raw='Here you go:\n```json\n{"jobs": ' + str(JOBS).replace("'", '"') + "}\n```"),
    )
    shortlist = crew.find_shortlist_in_result(result)
    assert len(shortlist) == 2
    assert shortlist[0]["company"] == "Acme"


def test_unparseable_output_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crew, "JobList", _DummyJobList)
    result = _result(_task(raw="no jobs here"), _task(raw=""))
    assert crew.find_shortlist_in_result(result) == []


def test_save_shortlist_raises_helpful_error_when_nothing_recovers(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(crew, "JobList", _DummyJobList)
    monkeypatch.setattr(crew, "SHORTLIST_JSON", str(tmp_path / "shortlist.json"))
    with pytest.raises(RuntimeError, match="structured JobList"):
        crew.save_shortlist_from_result(_result(_task(raw="nothing usable")))


def test_save_shortlist_persists_recovered_jobs(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import json

    monkeypatch.setattr(crew, "JobList", _DummyJobList)
    destination = tmp_path / "shortlist.json"
    monkeypatch.setattr(crew, "SHORTLIST_JSON", str(destination))
    raw = '{"jobs": ' + str(JOBS).replace("'", '"') + "}"
    saved = crew.save_shortlist_from_result(_result(_task(raw=raw)))
    assert len(saved) == 2
    assert len(json.loads(destination.read_text(encoding="utf-8"))) == 2
