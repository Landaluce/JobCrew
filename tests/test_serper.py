"""Tests for the direct Serper search client and the crew.py listing search."""

import json

import pytest

from job_automation.serper import SerperError, serper_search


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object):
        return False

    def read(self) -> bytes:
        return self._payload


def _fake_urlopen(payload: dict):
    def _open(request, timeout=None, context=None):
        return _FakeResponse(payload)

    return _open


def test_serper_search_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    with pytest.raises(SerperError, match="SERPER_API_KEY"):
        serper_search("python jobs", api_key="")


def test_serper_search_parses_organic_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    payload = {
        "organic": [
            {"title": "Python Jobs", "link": "https://www.indeed.com/jobs", "snippet": "s"},
            {"title": "", "link": ""},
            "not-a-dict",
        ]
    }
    monkeypatch.setattr(
        "job_automation.serper.urllib.request.urlopen", _fake_urlopen(payload)
    )
    results = serper_search("python jobs", num=5)
    assert len(results) == 1
    assert results[0]["link"] == "https://www.indeed.com/jobs"
    assert results[0]["title"] == "Python Jobs"


def test_serper_search_wraps_request_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-key")

    def _boom(request, timeout=None, context=None):
        raise OSError("connection refused")

    monkeypatch.setattr("job_automation.serper.urllib.request.urlopen", _boom)
    with pytest.raises(SerperError, match="connection refused"):
        serper_search("python jobs")


def test_search_job_listings_filters_blacklisted_and_dedupes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import crew

    monkeypatch.setattr(
        crew,
        "serper_search",
        lambda query, num=10: [
            {"title": "Real Jobs", "link": "https://www.indeed.com/jobs?q=python"},
            {"title": "Junk", "link": "https://www.example.com/jobs/1"},
            {"title": "Duplicate", "link": "https://www.indeed.com/jobs?q=python"},
        ],
    )
    monkeypatch.setattr(
        crew,
        "is_valid_job_url",
        lambda url, blacklist_path: "example.com" not in url,
    )
    listings = crew.search_job_listings("backend engineer", "Remote", 10)
    assert len(listings) == 1
    assert listings[0]["company"] == "indeed.com"
    assert listings[0]["title"] == "Real Jobs"
    assert listings[0]["source"] == "serper"


def test_search_job_listings_rejects_empty_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import crew

    called = False

    def _search(query, num=10):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(crew, "serper_search", _search)
    assert crew.search_job_listings("   ", "Remote", 10) == []
    assert not called
