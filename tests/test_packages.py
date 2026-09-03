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


def _plain(job_id_value: str, status: str, **extra: object) -> dict:
    row = {"job_id": job_id_value, "job": {"title": "Dev", "company": "Acme"}, "status": status}
    row.update(extra)
    return row


class TestMergeSave:
    def test_merge_keeps_disk_only_rows_and_appends_new_ones(self, tmp_path: Path) -> None:
        path = tmp_path / "packages.json"
        save_packages([_plain("disk-only", "approved")], path)
        save_packages(
            [_plain("existing", "draft"), _plain("brand-new", "draft")],
            path,
            merge_existing=True,
        )
        ids = [row["job_id"] for row in load_packages(path)]
        assert ids == ["disk-only", "existing", "brand-new"]

    def test_merge_unchanged_row_keeps_on_disk_version(self, tmp_path: Path) -> None:
        path = tmp_path / "packages.json"
        save_packages([_plain("a", "approved", cover_letter="hello")], path)
        save_packages(
            [_plain("a", "approved", cover_letter="hello", updated_at="9999")],
            path,
            merge_existing=True,
        )
        loaded = load_packages(path)
        assert loaded[0]["cover_letter"] == "hello"
        assert "updated_at" not in loaded[0]  # untouched row is not stamped

    def test_merge_newer_edit_wins_over_stale_copy(self, tmp_path: Path) -> None:
        path = tmp_path / "packages.json"
        # The on-disk row is a fresh dashboard edit (stamped), and the caller's
        # in-memory copy is a stale pre-edit snapshot from an older load.
        save_packages(
            [_plain("a", "approved", cover_letter="newer", updated_at="2026-09-01T00:00:00Z")],
            path,
        )
        save_packages(
            [_plain("a", "approved", cover_letter="stale-revert")],  # no updated_at
            path,
            merge_existing=True,
        )
        loaded = load_packages(path)
        assert loaded[0]["cover_letter"] == "newer"  # stale copy lost the tie-break
        assert loaded[0]["updated_at"] == "2026-09-01T00:00:00Z"

    def test_merge_removed_ids_are_deleted(self, tmp_path: Path) -> None:
        path = tmp_path / "packages.json"
        save_packages([_plain("keep", "approved"), _plain("drop", "draft")], path)
        save_packages(
            [_plain("keep", "approved")],
            path,
            merge_existing=True,
            removed_ids=["drop"],
        )
        assert [row["job_id"] for row in load_packages(path)] == ["keep"]

    def test_merge_changed_row_is_stamped_and_wins(self, tmp_path: Path) -> None:
        path = tmp_path / "packages.json"
        save_packages([_plain("a", "approved")], path)
        save_packages([_plain("a", "prepared")], path, merge_existing=True)
        loaded = load_packages(path)
        assert loaded[0]["status"] == "prepared"
        assert "updated_at" in loaded[0]

    def test_default_save_is_replace_not_merge(self, tmp_path: Path) -> None:
        path = tmp_path / "packages.json"
        save_packages([_plain("old", "approved")], path)
        save_packages([_plain("new", "draft")], path)  # no merge flag
        assert [row["job_id"] for row in load_packages(path)] == ["new"]


def test_legacy_packages_without_tailored_field_load_fine(tmp_path: Path) -> None:
    """Packages written before tailored resumes existed stay readable."""
    import json

    path = tmp_path / "packages.json"
    path.write_text(json.dumps([{"job_id": "x1", "job": {"title": "Dev"}, "status": "approved"}]), encoding="utf-8")
    loaded = load_packages(path)
    assert loaded[0].get("tailored_resume", "") == ""
