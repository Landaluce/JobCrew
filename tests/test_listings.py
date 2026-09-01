from pathlib import Path

from job_automation.listings import (
    add_to_blacklist,
    is_blacklisted,
    load_blacklist,
    select_listing_urls,
)


def _result(url: str) -> dict:
    return {"url": url, "title": "", "company": "", "location": ""}


def test_select_dedupes_and_preserves_order() -> None:
    results = [
        _result("https://a.com/jobs"),
        _result("https://a.com/jobs"),
        _result("https://b.com/jobs"),
    ]
    selected = select_listing_urls(results, max_pages=10, max_per_domain=10)
    assert [item["url"] for item in selected] == ["https://a.com/jobs", "https://b.com/jobs"]


def test_known_boards_rank_first() -> None:
    results = [
        _result("https://smallshop.example.com/careers"),
        _result("https://www.linkedin.com/jobs/search?keywords=python"),
    ]
    selected = select_listing_urls(results, max_pages=1, max_per_domain=1)
    assert selected[0]["url"].startswith("https://www.linkedin.com")


def test_per_domain_cap() -> None:
    results = [
        _result("https://linkedin.com/jobs/search?x=1"),
        _result("https://linkedin.com/jobs/search?x=2"),
        _result("https://linkedin.com/jobs/search?x=3"),
        _result("https://indeed.com/jobs?q=python"),
    ]
    selected = select_listing_urls(results, max_pages=10, max_per_domain=2)
    urls = [item["url"] for item in selected]
    assert sum(1 for u in urls if "linkedin.com" in u) == 2
    assert "https://indeed.com/jobs?q=python" in urls


def test_global_cap() -> None:
    results = [_result(f"https://board{i}.com/jobs") for i in range(20)]
    selected = select_listing_urls(results, max_pages=5, max_per_domain=2)
    assert len(selected) == 5


def test_empty_and_blank_urls() -> None:
    results = [{"url": "", "title": ""}, {"title": "no url key"}]
    assert select_listing_urls(results) == []


def test_blacklist_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "blacklist.json"
    add_to_blacklist(path, ["https://dead.example.com/jobs"])
    add_to_blacklist(path, ["https://dead.example.com/jobs"])
    add_to_blacklist(path, ["https://parked.example.com/careers"])

    entries = load_blacklist(path)
    assert entries == ["https://dead.example.com/jobs", "https://parked.example.com/careers"]
    assert not (tmp_path / "blacklist.json.tmp").exists()
    assert is_blacklisted(path, "https://dead.example.com/jobs")
    assert not is_blacklisted(path, "https://alive.example.com/jobs")


def test_blacklist_missing_file(tmp_path: Path) -> None:
    assert load_blacklist(tmp_path / "missing.json") == []
    assert not is_blacklisted(tmp_path / "missing.json", "https://anything.com")


def test_blacklist_dedupes_case_insensitively(tmp_path: Path) -> None:
    path = tmp_path / "blacklist.json"
    add_to_blacklist(path, ["https://Dead.Example.com/jobs"])
    merged = add_to_blacklist(path, ["https://dead.example.com/jobs"])
    assert len(merged) == 1


def test_blacklist_bare_domain_matches_host_and_subdomains(tmp_path: Path) -> None:
    path = tmp_path / "blacklist.json"
    add_to_blacklist(path, ["dead.example.com"])

    assert is_blacklisted(path, "https://dead.example.com/jobs/42")
    assert is_blacklisted(path, "https://careers.dead.example.com/role")
    assert is_blacklisted(path, "http://www.dead.example.com")
    # Similar-looking hosts must not be caught by substring matching
    assert not is_blacklisted(path, "https://notdead.example.com/jobs")
    assert not is_blacklisted(path, "https://dead.example.computer.com/jobs")
    assert not is_blacklisted(path, "https://example.com/jobs")


def test_blacklist_full_url_matches_substring(tmp_path: Path) -> None:
    path = tmp_path / "blacklist.json"
    add_to_blacklist(path, ["https://dead.example.com/jobs/42"])

    assert is_blacklisted(path, "https://dead.example.com/jobs/42?utm_source=x")
    assert not is_blacklisted(path, "https://dead.example.com/careers")


def test_blacklist_empty_url_is_never_blocked(tmp_path: Path) -> None:
    path = tmp_path / "blacklist.json"
    add_to_blacklist(path, ["example.com"])
    assert not is_blacklisted(path, "")
