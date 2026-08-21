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


def pick_handler(url: str) -> ApplicationHandler | None:
    """
    Return a site-specific handler when one exists.

    Return None to let crew.py use its generic fallback logic.
    Future: add real site-specific handlers here (e.g. Greenhouse
    multi-step forms, Lever custom fields) and return them.
    """
    return None
