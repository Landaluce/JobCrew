from __future__ import annotations

from typing import Any, Callable

from playwright.sync_api import Page


ApplicationHandler = Callable[[Page, dict[str, Any], str, str], None]


def generic_handler(
    page: Page,
    job: dict[str, Any],
    resume_path: str,
    cover_letter: str,
) -> None:
    """Best-effort generic form filling; never submits an application."""

    file_inputs = page.locator('input[type="file"]')

    if file_inputs.count() and resume_path:
        file_inputs.first.set_input_files(resume_path)

    textareas = page.locator("textarea")

    if textareas.count() and cover_letter:
        textareas.first.fill(cover_letter)


def greenhouse_handler(
    page: Page,
    job: dict[str, Any],
    resume_path: str,
    cover_letter: str,
) -> None:
    """Conservative Greenhouse form filling; never submits an application.

    Greenhouse renders its form inside an iframe (``grnhse_iframe``), so the
    generic handler would never see the fields. This handler targets that
    frame and only uploads the resume and fills the cover letter — it never
    fills personal data or advances steps, since those need real identity
    values that the pipeline does not hold.
    """

    frames = [f for f in page.frames if f != page.main_frame and "greenhouse" in (f.url or "")]
    target = frames[0] if frames else page.main_frame

    if resume_path:
        file_inputs = target.locator('input[type="file"]')
        if file_inputs.count():
            file_inputs.first.set_input_files(resume_path)

    if cover_letter:
        textareas = target.locator("textarea")
        if textareas.count():
            textareas.first.fill(cover_letter)


def pick_handler(url: str) -> ApplicationHandler | None:
    """
    Return a site-specific handler when one exists.

    Return None to let crew.py use its generic fallback logic.
    """

    if "greenhouse.io" in url:
        return greenhouse_handler

    return None
