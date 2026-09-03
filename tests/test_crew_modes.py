"""Tests for crew.py's run-mode defaulting: a bare invocation (resume/query/location
with no mode flag) must default to search instead of silently doing nothing."""

import argparse

import crew


def _args(**overrides: object) -> argparse.Namespace:
    values = {
        "search": False,
        "apply_existing": False,
        "generate_cover": None,
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
    assert crew.default_run_mode(_args(generate_resume="abc")).search is False
    assert crew.default_run_mode(_args(add_package="https://x.dev/job/1")).search is False
    assert crew.default_run_mode(_args(full_cycle=True)).search is False
    assert crew.default_run_mode(_args(job_id="abc")).search is False
