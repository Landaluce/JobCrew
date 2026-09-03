"""Package status lifecycle rules for JobCrew.

Packages move through a review lifecycle: ``draft`` → ``approved`` →
``prepared`` → ``submitted``. A package may be rejected (or withdrawn,
failed, etc.) at any point, and may be returned to ``draft`` for another
round of human review. Moving forward always requires passing through
``approved`` — auto-submission is deliberately opt-in.

These helpers are pure so both the CLI (``crew.py``) and the dashboard can
share one definition of a legal transition.
"""

from __future__ import annotations

# Forward progression a package must follow.
PACKAGE_LIFECYCLE_FLOW = ("draft", "approved", "prepared", "submitted")

# Terminal states a package can be moved into from anywhere.
TERMINAL_STATUSES = frozenset({"rejected", "withdrawn", "failed", "error"})

# Statuses that only live in the application-history stream, not on packages.
HISTORY_ONLY_STATUSES = frozenset({"interview", "offer", "skipped_invalid_url"})


def normalize_package_status(status: str | None) -> str:
    """Return a canonical package status, defaulting unknown values to draft."""
    value = (status or "draft").strip().lower()
    if value in PACKAGE_LIFECYCLE_FLOW or value in TERMINAL_STATUSES:
        return value
    return "draft"


def requires_approval(status: str) -> bool:
    """Whether a package still needs human approval before it can be applied to."""
    return normalize_package_status(status) not in {"approved", "prepared", "submitted"}


def validate_transition(current: str, new: str) -> bool:
    """Whether moving a package from ``current`` to ``new`` is a legal transition.

    Rules:
    - Staying put is always fine.
    - Any terminal status (rejected/withdrawn/failed/error) may be entered
      from anywhere; terminal statuses are a dead end.
    - A package can always be returned to ``draft`` (review again).
    - Forward movement follows the lifecycle and requires the human
      approval step: draft→approved only; approved→prepared or submitted;
      prepared→submitted. ``submitted`` is a dead end going forward.
    """
    current_status = normalize_package_status(current)
    new_status = normalize_package_status(new)

    if new_status == current_status:
        return True
    if new_status in TERMINAL_STATUSES:
        return True
    if current_status in TERMINAL_STATUSES:
        return False
    if new_status == "draft":
        return True
    if current_status == "draft":
        return new_status == "approved"
    if current_status == "approved":
        return new_status in {"prepared", "submitted"}
    if current_status == "prepared":
        return new_status == "submitted"
    return False
