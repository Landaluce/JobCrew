"""Tests for ATS handler selection; browser interaction is not exercised here."""

import playwright_sites


def test_greenhouse_url_selects_greenhouse_handler() -> None:
    handler = playwright_sites.pick_handler("https://boards.greenhouse.io/acme/jobs/42")
    assert handler is playwright_sites.greenhouse_handler


def test_lever_url_selects_lever_handler() -> None:
    assert playwright_sites.pick_handler("https://jobs.lever.co/acme/senior-engineer") is playwright_sites.lever_handler


def test_ashby_url_selects_ashby_handler() -> None:
    assert playwright_sites.pick_handler("https://jobs.ashbyhq.com/acme/abc-123") is playwright_sites.ashby_handler


def test_workday_urls_select_workday_handler() -> None:
    assert (
        playwright_sites.pick_handler("https://acme.wd12.myworkdayjobs.com/en-US/Engineering/job/42")
        is playwright_sites.workday_handler
    )
    assert playwright_sites.pick_handler("https://acme.workdayjobs.com/job/42") is playwright_sites.workday_handler


def test_unknown_board_falls_back_to_generic() -> None:
    assert playwright_sites.pick_handler("https://careers.acme.example/job/42") is None
    assert playwright_sites.pick_handler("") is None
    assert playwright_sites.pick_handler(None) is None  # type: ignore[arg-type]


def test_url_case_is_ignored() -> None:
    assert (
        playwright_sites.pick_handler("HTTPS://BOARDS.GREENHOUSE.IO/ACME/JOBS/42")
        is playwright_sites.greenhouse_handler
    )
