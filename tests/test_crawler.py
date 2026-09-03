"""Tests for the crawler's pure URL-matching and filtering logic.

These tests never touch the network or launch a browser; they exercise the
same helpers the Playwright crawl loop uses to decide which links count as
job postings.
"""

from pathlib import Path

from crawler import (
    is_valid_job_url,
    link_domain_is_acceptable,
    matches_job_posting_url,
)


class TestMatchesJobPostingUrl:
    def test_known_board_patterns(self) -> None:
        assert matches_job_posting_url("https://boards.greenhouse.io/acme/jobs/42")
        assert matches_job_posting_url("https://jobs.lever.co/acme/senior-engineer")
        assert matches_job_posting_url("https://jobs.ashbyhq.com/acme/6f0b2e8f-4a21-4b77-9a0c-3e0d6f9f0c1e")
        assert matches_job_posting_url("https://acme.wd12.myworkdayjobs.com/en-US/Engineering/job/Remote/42")
        assert matches_job_posting_url("https://www.linkedin.com/jobs/view/123456")
        assert matches_job_posting_url("https://www.indeed.com/viewjob?jk=abcdef")
        assert matches_job_posting_url("https://jobs.smartrecruiters.com/Acme/1234-title")
        assert matches_job_posting_url("https://www.workable.com/j/ABC123")
        assert matches_job_posting_url("https://www.remoteok.com/remote-jobs/senior-python")

    def test_generic_path_patterns(self) -> None:
        assert matches_job_posting_url("https://acme.example.com/jobs/view/42")
        assert matches_job_posting_url("https://acme.example.com/job/42")
        assert matches_job_posting_url("https://acme.example.com/careers/current-openings")
        assert matches_job_posting_url("https://acme.example.com/position/backend-engineer")
        assert not matches_job_posting_url("https://acme.example.com/positions")  # plural index page

    def test_navigation_and_home_pages_do_not_match(self) -> None:
        assert not matches_job_posting_url("https://acme.example.com/")
        assert not matches_job_posting_url("https://acme.example.com/about")
        assert not matches_job_posting_url("https://acme.example.com/login")
        assert not matches_job_posting_url("https://acme.example.com/privacy")
        assert not matches_job_posting_url("")
        assert not matches_job_posting_url(None)  # type: ignore[arg-type]


class TestLinkDomainIsAcceptable:
    def test_same_domain_accepted(self) -> None:
        assert link_domain_is_acceptable("https://careers.acme.com/job/1", "https://careers.acme.com/jobs")

    def test_subdomain_relationship_accepted(self) -> None:
        assert link_domain_is_acceptable("https://jobs.acme.com/job/1", "https://www.acme.com/careers")

    def test_known_job_board_cross_domain_accepted(self) -> None:
        assert link_domain_is_acceptable("https://boards.greenhouse.io/acme/jobs/42", "https://acme.example.com/careers")

    def test_unrelated_cross_domain_rejected(self) -> None:
        assert not link_domain_is_acceptable("https://spyware.example.net/track?u=1", "https://acme.example.com/careers")

    def test_parked_domain_rejected(self) -> None:
        assert not link_domain_is_acceptable("https://acme.hugedomains.com/job/1", "https://acme.example.com/careers")

    def test_empty_href_rejected(self) -> None:
        assert not link_domain_is_acceptable("", "https://acme.example.com/careers")


class TestIsValidJobUrl:
    def test_valid_url(self, tmp_path: Path) -> None:
        assert is_valid_job_url("https://boards.greenhouse.io/acme/jobs/42", str(tmp_path / "blacklist.json"))

    def test_empty_url_invalid(self, tmp_path: Path) -> None:
        assert not is_valid_job_url("", str(tmp_path / "blacklist.json"))
        assert not is_valid_job_url(None, str(tmp_path / "blacklist.json"))  # type: ignore[arg-type]

    def test_invalid_example_domains(self, tmp_path: Path) -> None:
        assert not is_valid_job_url("https://example.com/jobs/42", str(tmp_path / "blacklist.json"))
        assert not is_valid_job_url("https://localhost/jobs/42", str(tmp_path / "blacklist.json"))

    def test_parked_domains_invalid(self, tmp_path: Path) -> None:
        assert not is_valid_job_url("https://dead.example.hugedomains.com/jobs/42", str(tmp_path / "blacklist.json"))

    def test_blacklisted_url_invalid(self, tmp_path: Path) -> None:
        blacklist = tmp_path / "blacklist.json"
        blacklist.write_text('["dead.example.com"]', encoding="utf-8")
        assert not is_valid_job_url("https://dead.example.com/jobs/42", str(blacklist))
        assert is_valid_job_url("https://aliveco.example/jobs/42", str(blacklist))
