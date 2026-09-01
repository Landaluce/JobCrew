"""Playwright-assisted application flow for JobCrew."""

from __future__ import annotations

import re
from typing import Any

from cli_ui import log_error, log_info, log_success, log_warning
from events import log_event

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


def apply_with_playwright(
    job: dict[str, Any],
    resume_path: str,
    cover_letter: str,
    auto_submit: bool = False,
    review_mode: bool = True,
    verbose: bool = False,
) -> dict[str, Any]:
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("Playwright not installed. Run: pip install playwright && playwright install")

    details = {"site": "unknown", "steps": [], "submitted": False}
    try:
        from playwright_sites import pick_handler
    except Exception:
        pick_handler = None

    application_url = str(job.get("url") or "").strip()

    if not application_url.startswith(("https://", "http://")):
        error_message = (
            "Cannot open application: this job has no valid application URL. "
            f"Received: {application_url!r}"
        )

        log_event(
            "skipped_invalid_url",
            job,
            {"error": error_message},
        )

        raise ValueError(error_message)

    job = {**job, "url": application_url}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                slow_mo=250,
                args=["--start-maximized"],
            )

            context = browser.new_context(
                viewport={"width": 1440, "height": 1000},
            )

            page = context.new_page()
            page.bring_to_front()

            log_info(f"\nOpening application URL:\n{job['url']}\n", verbose)

            response = page.goto(
                job["url"],
                wait_until="domcontentloaded",
                timeout=60_000,
            )

            page.wait_for_timeout(3_000)

            log_info(f"Page title: {page.title()}", verbose)
            log_info(f"Current URL: {page.url}", verbose)
            log_info(f"HTTP status: {response.status if response else 'unknown'}", verbose)

            details["steps"].append("opened_url")

            handler = pick_handler(job["url"]) if pick_handler else None
            if handler:
                handler(page, job, resume_path, cover_letter)
                details["steps"].append("handler_executed")
            else:
                if page.locator('input[type="file"]').count():
                    page.locator('input[type="file"]').first.set_input_files(resume_path)
                    details["steps"].append("resume_uploaded")
                if page.locator('textarea').count():
                    page.locator('textarea').first.fill(cover_letter)
                    details["steps"].append("cover_letter_filled")

            page.screenshot(path="output/review.png", full_page=True)

            manual_status = "prepared"
            if review_mode:
                details["steps"].append("manual_review")
                choice = input(
                    "\nReview the form in the browser before replying here. "
                    "Type [s] if you submitted it manually, [p] (or Enter) to save it as prepared, "
                    "or [c] to cancel: "
                ).strip().lower()
                if choice == "s":
                    manual_status = "submitted"
                elif choice == "c":
                    details["steps"].append("review_cancelled")
                    browser.close()
                    return {**details, "cancelled": True}
                elif choice not in {"", "p"}:
                    log_warning("Unrecognised choice; saving as prepared.", verbose)

            if auto_submit and manual_status != "submitted":
                # Never click a generic `button[type=submit]`: job boards often
                # include hidden search forms (for example, LinkedIn's search bar).
                # Only a visible control with an explicit final-submit label is
                # eligible for automatic submission.
                submit_buttons = page.get_by_role(
                    "button",
                    name=re.compile(r"^\s*(submit application|submit)\s*$", re.IGNORECASE),
                )
                submit_inputs = page.locator('input[type="submit"]:visible')
                submitted = False
                submit_error = None

                for controls in (submit_buttons, submit_inputs):
                    for index in range(controls.count()):
                        control = controls.nth(index)
                        if not control.is_visible() or not control.is_enabled():
                            continue
                        try:
                            control.click(timeout=10_000)
                            submitted = True
                            break
                        except Exception as exc:
                            submit_error = str(exc)
                    if submitted:
                        break

                if submitted:
                    details["steps"].append("submitted")
                    details["submitted"] = True
                    status = "submitted"
                else:
                    details["submit_error"] = submit_error or (
                        "No visible final Submit or Submit application control was found. "
                        "Finish the submission manually in the open browser."
                    )
                    details["steps"].append("manual_submission_required")
                    status = "prepared"
            else:
                status = manual_status

            browser.close()

        log_event(status, job, details)
        log_success(f"Application status: {status}", verbose)
        return details
    except Exception as e:
        details["error"] = str(e)
        log_event("failed", job, details)
        log_error(f"Application failed: {e}", verbose)
        raise
