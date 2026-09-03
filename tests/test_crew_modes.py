"""Tests for crew.py's run-mode defaulting: a bare invocation (resume/query/location
with no mode flag) must default to search instead of silently doing nothing."""

import argparse

import pytest

import crew


def _args(**overrides: object) -> argparse.Namespace:
    values = {
        "search": False,
        "apply_existing": False,
        "generate_cover": None,
        "generate_cover_all": False,
        "generate_resume": None,
        "add_package": None,
        "full_cycle": False,
        "job_id": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_bare_invocation_defaults_to_search() -> None:
    args = crew.default_run_mode(_args())
    assert args.search is True


def test_explicit_search_is_preserved() -> None:
    args = crew.default_run_mode(_args(search=True))
    assert args.search is True


def test_other_modes_do_not_default_to_search() -> None:
    assert crew.default_run_mode(_args(apply_existing=True)).search is False
    assert crew.default_run_mode(_args(generate_cover="abc")).search is False
    assert crew.default_run_mode(_args(generate_cover_all=True)).search is False
    assert crew.default_run_mode(_args(generate_resume="abc")).search is False
    assert crew.default_run_mode(_args(add_package="https://x.dev/job/1")).search is False
    assert crew.default_run_mode(_args(full_cycle=True)).search is False
    assert crew.default_run_mode(_args(job_id="abc")).search is False


def test_generate_covers_for_approved_only_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    destination = tmp_path / "packages.json"
    monkeypatch.setattr(crew, "PACKAGES_JSON", str(destination))
    monkeypatch.setattr(crew, "create_llm", lambda: object())
    monkeypatch.setattr(
        crew, "generate_cover_letter",
        lambda job, resume_text, llm: f"Letter for {job['title']}",
    )
    crew.save_packages([
        {"job_id": "a", "status": "approved", "cover_letter": "", "job": {"title": "Acme"}},
        {"job_id": "b", "status": "approved", "cover_letter": "existing", "job": {"title": "Globex"}},
        {"job_id": "c", "status": "draft", "cover_letter": "", "job": {"title": "Draft"}},
    ], str(destination))

    generated = crew.generate_covers_for_approved("resume text", verbose=True)
    assert generated == 1

    by_id = {p["job_id"]: p for p in crew.load_packages(str(destination))}
    assert by_id["a"]["cover_letter"] == "Letter for Acme"
    assert by_id["b"]["cover_letter"] == "existing"  # untouched
    assert by_id["c"]["cover_letter"] == ""  # draft untouched


def test_generate_covers_for_approved_none_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    destination = tmp_path / "packages.json"
    monkeypatch.setattr(crew, "PACKAGES_JSON", str(destination))
    monkeypatch.setattr(crew, "create_llm", lambda: object())
    monkeypatch.setattr(
        crew, "generate_cover_letter",
        lambda job, resume_text, llm: "should not run",
    )
    crew.save_packages([
        {"job_id": "a", "status": "approved", "cover_letter": "done", "job": {"title": "Acme"}},
    ], str(destination))

    assert crew.generate_covers_for_approved("resume text", verbose=True) == 0
