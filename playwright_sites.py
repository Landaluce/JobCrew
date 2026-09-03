"""Site-specific form-filling handlers for the Playwright apply flow.

Every handler is conservative: it only uploads the resume and fills the
cover letter (and nothing personal). None of them ever submit an application —
auto-submission is decided by ``applier.py`` only when ``--auto-submit`` is
passed. Add new ATS adapters here and register them in ``pick_handler``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - keep the module importable without playwright
    from playwright.sync_api import Page

ApplicationHandler = Callable[..., None]


def _upload_resume(page_or_frame: Any, resume_path: str) -> bool:
    """Upload the resume to the first file input reachable on a page/frame."""
    file_inputs = page_or_frame.locator('input[type="file"]')
    if file_inputs.count() and resume_path:
        file_inputs.first.set_input_files(resume_path)
        return True
    return False


def _fill_cover_letter(page_or_frame: Any, cover_letter: str) -> bool:
    """Fill the first visible textarea with the cover letter."""
    if not cover_letter:
        return False
    textareas = page_or_frame.locator("textarea")
    for index in range(textareas.count()):
        area = textareas.nth(index)
        if area.is_visible():
            area.fill(cover_letter)
            return True
    # Fall back to the first textarea even if visibility cannot be determined.
    if textareas.count():
        textareas.first.fill(cover_letter)
        return True
    return False


def generic_handler(
    page: Page,
    job: dict[str, Any],
    resume_path: str,
    cover_letter: str,
) -> None:
    """Best-effort generic form filling; never submits an application."""
    _upload_resume(page, resume_path)
    _fill_cover_letter(page, cover_letter)


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
    _upload_resume(target, resume_path)
    _fill_cover_letter(target, cover_letter)


def _reveal_lever_form(page: Page) -> None:
    """Open Lever's application form.

    Modern Lever postings render the posting page without any form fields;
    the file input and textarea only exist once the "APPLY FOR THIS JOB"
    link (which points at ``<posting>/apply``) has been opened. This helper
    is a no-op when the form is already present.
    """
    if page.locator('input[type="file"]').count():
        return
    apply_link = page.locator("a[href$='/apply']").first
    if apply_link.count():
        try:
            apply_link.click(timeout=10_000)
            page.wait_for_timeout(3_000)
            return
        except Exception:
            pass
    # Anchor is missing or a redirect happened; go straight to the apply URL.
    current = page.url or ""
    if current.endswith("/apply"):
        return
    try:
        page.goto(current.rstrip("/") + "/apply", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2_000)
    except Exception:
        pass


def lever_handler(
    page: Page,
    job: dict[str, Any],
    resume_path: str,
    cover_letter: str,
) -> None:
    """Lever posting pages; never submits an application.

    Lever's application form is only present on the ``<posting>/apply`` page
    (reached by clicking "APPLY FOR THIS JOB"), so the handler reveals the
    form first, then uploads the resume and fills a cover-letter textarea in
    the main frame or an embedded jobs-board iframe.
    """
    _reveal_lever_form(page)
    _upload_resume(page, resume_path)
    if not _fill_cover_letter(page, cover_letter):
        for frame in page.frames:
            if frame != page.main_frame and _fill_cover_letter(frame, cover_letter):
                break
    for frame in page.frames:
        if frame != page.main_frame:
            _upload_resume(frame, resume_path)


def workday_handler(
    page: Page,
    job: dict[str, Any],
    resume_path: str,
    cover_letter: str,
) -> None:
    """Best-effort Workday handling; never submits an application.

    Workday application forms are notoriously buried under nested iframes.
    This handler scans every reachable frame (same- and cross-origin) for a
    resume file input and a cover-letter textarea and fills whatever it finds,
    leaving navigation and review to the human in the opened browser.
    """
    for frame in [page.main_frame, *page.frames]:
        try:
            _upload_resume(frame, resume_path)
            _fill_cover_letter(frame, cover_letter)
        except Exception:
            # A frame may refuse interaction (cross-origin or mid-load); the
            # human reviewer still has the open browser for those fields.
            continue


def ashby_handler(
    page: Page,
    job: dict[str, Any],
    resume_path: str,
    cover_letter: str,
) -> None:
    """Best-effort Ashby handling; never submits an application.

    Ashby embeds its board in a shadow-DOM application; Playwright locators
    pierce open shadow roots, so the file input and textarea are usually
    reachable straight from the main frame.
    """
    _upload_resume(page, resume_path)
    if not _fill_cover_letter(page, cover_letter):
        for frame in page.frames:
            if frame != page.main_frame and _fill_cover_letter(frame, cover_letter):
                break


_HANDLERS: dict[str, ApplicationHandler] = {
    "greenhouse.io": greenhouse_handler,
    "lever.co": lever_handler,
    "ashbyhq.com": ashby_handler,
    "myworkdayjobs.com": workday_handler,
    "workdayjobs.com": workday_handler,
}


def pick_handler(url: str) -> ApplicationHandler | None:
    """
    Return a site-specific handler when one exists.

    Return None to let crew.py use its generic fallback logic.
    """
    lowered = (url or "").lower()
    for marker, handler in _HANDLERS.items():
        if marker in lowered:
            return handler
    return None
