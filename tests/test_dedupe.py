"""Tests for package deduplication/merging and blacklist removal helpers."""

from pathlib import Path

from job_automation.listings import add_to_blacklist, load_blacklist, remove_from_blacklist
from job_automation.packages import dedupe_packages, find_duplicate_groups


def _package(job_id: str, status: str = "draft", **content: str) -> dict:
    return {
        "job_id": job_id,
        "job": {"url": f"https://boards.example.com/jobs/{job_id}", "company": "Acme", "title": "Engineer"},
        "status": status,
        "created_at": "2026-08-20T10:00:00Z",
        "cover_letter": "",
        "tailored_resume": "",
        "answers": {},
        **content,
    }


def test_no_duplicates_leaves_list_untouched() -> None:
    packages = [_package("a1"), _package("b2", status="approved")]
    merged, removed = dedupe_packages(packages)
    assert removed == []
    assert merged == packages


def test_duplicate_groups_only_return_multi_member_groups() -> None:
    groups = find_duplicate_groups([_package("a1"), _package("a1"), _package("b2")])
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_dedupe_keeps_later_status_over_earlier() -> None:
    packages = [_package("a1", status="draft"), _package("a1", status="submitted", created_at="2026-08-20T10:00:00Z")]
    merged, removed = dedupe_packages(packages)
    assert len(merged) == 1
    assert merged[0]["status"] == "submitted"
    assert len(removed) == 1
    assert removed[0]["status"] == "draft"


def test_dedupe_keeps_richer_content_on_tie() -> None:
    packages = [
        _package("a1", cover_letter="", created_at="2026-08-20T10:00:00Z"),
        _package("a1", cover_letter="Dear Acme,", created_at="2026-08-21T10:00:00Z"),
    ]
    merged, _ = dedupe_packages(packages)
    assert merged[0]["cover_letter"] == "Dear Acme,"
    assert len(merged) == 1


def test_dedupe_removes_only_duplicate_members_and_preserves_order() -> None:
    packages = [
        _package("a1"),
        _package("b2", status="approved"),
        _package("a1", status="approved"),
        _package("c3"),
    ]
    merged, removed = dedupe_packages(packages)
    assert [p["job_id"] for p in merged] == ["b2", "a1", "c3"]
    assert [p["job_id"] for p in removed] == ["a1"]
    assert merged[1]["status"] == "approved"


def test_blacklist_remove(tmp_path: Path) -> None:
    path = tmp_path / "blacklist.json"
    add_to_blacklist(path, ["dead.example.com", "https://spam.example.com/jobs"])
    remaining = remove_from_blacklist(path, ["DEAD.EXAMPLE.COM"])
    assert remaining == ["https://spam.example.com/jobs"]
    assert load_blacklist(path) == ["https://spam.example.com/jobs"]
    assert not (tmp_path / "blacklist.json.tmp").exists()


def test_blacklist_remove_missing_entry_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "blacklist.json"
    add_to_blacklist(path, ["dead.example.com"])
    remaining = remove_from_blacklist(path, ["never-added.example.com"])
    assert remaining == ["dead.example.com"]


def test_blacklist_remove_missing_file_returns_empty(tmp_path: Path) -> None:
    assert remove_from_blacklist(tmp_path / "missing.json", ["x.com"]) == []
