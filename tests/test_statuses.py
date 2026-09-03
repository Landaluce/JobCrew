"""Tests for the package status lifecycle (draft → approved → prepared → submitted)."""

from job_automation.statuses import (
    HISTORY_ONLY_STATUSES,
    PACKAGE_LIFECYCLE_FLOW,
    TERMINAL_STATUSES,
    normalize_package_status,
    requires_approval,
    validate_transition,
)


def test_lifecycle_flow_order() -> None:
    assert PACKAGE_LIFECYCLE_FLOW == ("draft", "approved", "prepared", "submitted")


def test_normalize_keeps_known_statuses() -> None:
    for status in PACKAGE_LIFECYCLE_FLOW + tuple(TERMINAL_STATUSES):
        assert normalize_package_status(status) == status


def test_normalize_unknown_statuses_to_draft() -> None:
    assert normalize_package_status("teleported") == "draft"
    assert normalize_package_status("") == "draft"
    assert normalize_package_status(None) == "draft"
    assert normalize_package_status("  Draft  ") == "draft"


def test_history_only_statuses_are_not_package_statuses() -> None:
    for status in HISTORY_ONLY_STATUSES:
        assert normalize_package_status(status) == "draft"


def test_forward_transitions_follow_the_flow() -> None:
    assert validate_transition("draft", "approved")
    assert validate_transition("approved", "prepared")
    assert validate_transition("approved", "submitted")
    assert validate_transition("prepared", "submitted")


def test_draft_cannot_jump_ahead_without_approval() -> None:
    assert not validate_transition("draft", "prepared")
    assert not validate_transition("draft", "submitted")


def test_return_to_draft_is_always_allowed() -> None:
    for status in PACKAGE_LIFECYCLE_FLOW:
        assert validate_transition(status, "draft")


def test_terminal_statuses_are_dead_ends() -> None:
    for terminal in TERMINAL_STATUSES:
        assert validate_transition("draft", terminal)
        assert validate_transition("approved", terminal)
        assert not validate_transition(terminal, "approved")
        assert not validate_transition(terminal, "draft")


def test_same_status_is_valid() -> None:
    for status in PACKAGE_LIFECYCLE_FLOW + tuple(TERMINAL_STATUSES):
        assert validate_transition(status, status)


def test_submitted_is_a_forward_dead_end() -> None:
    assert not validate_transition("submitted", "prepared")
    assert not validate_transition("submitted", "approved")


def test_requires_approval_only_for_reviewable_statuses() -> None:
    assert requires_approval("draft")
    assert requires_approval("rejected")
    assert requires_approval("teleported")
    assert not requires_approval("approved")
    assert not requires_approval("prepared")
    assert not requires_approval("submitted")
