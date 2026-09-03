"""Tests for the tailored-resume → PDF renderer (src/job_automation/resume_render.py)."""

from __future__ import annotations

from pathlib import Path

from job_automation.resume_render import render_resume_pdf, tailored_pdf_path


def test_tailored_pdf_path_is_stable() -> None:
    assert tailored_pdf_path("abc123") == Path("output/tailored_resumes/abc123_tailored.pdf")


def test_render_produces_a_pdf(tmp_path: Path) -> None:
    text = (
        "# Tailored resume — Engineer at Acme\n"
        "# Job: https://jobs.lever.co/acme/1\n"
        "# Review before using.\n\n"
        "Summary\n"
        "- Python engineer with 5 years of backend experience\n"
        "- Built data pipelines used by 40+ engineers\n"
        "\n"
        "Experience\n"
        "Senior Engineer, Acme — shipped the payments API"
    )
    out = render_resume_pdf(text, tmp_path / "tailored.pdf")
    assert out.exists()
    assert out.stat().st_size > 800
    assert out.read_bytes().startswith(b"%PDF")


def test_render_drops_comment_header_lines(tmp_path: Path) -> None:
    # Comments (# ...) are stripped; the render must still succeed on an
    # empty-but-commented body (e.g. an untouched template).
    out = render_resume_pdf("# only a comment\n# another\n", tmp_path / "empty.pdf")
    assert out.exists()
    assert out.stat().st_size > 500
