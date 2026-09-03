"""Tests for the dashboard's pure row-building logic (no Streamlit needed)."""

from datetime import datetime, timezone

from dashboard_app.rows import (
    FOLLOWUP_AFTER_DAYS,
    attention_rows,
    followup_rows,
    funnel_counts,
    history_table_rows,
    submitted_rows,
)

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


def _event(status: str, job: dict, timestamp: str, note: str | None = None) -> dict:
    return {"status": status, "job": job, "timestamp": timestamp, "details": {"note": note} if note else {}}


JOB_A = {"company": "Acme", "title": "Engineer", "url": "https://boards.greenhouse.io/acme/jobs/1", "score": 88}
JOB_B = {"company": "Globex", "title": "Analyst", "url": "https://jobs.lever.co/globex/role/2", "score": 71}


def test_attention_rows_only_failed_or_error() -> None:
    history = [
        _event("submitted", JOB_A, "2026-08-20T10:00:00Z"),
        _event("failed", JOB_B, "2026-08-21T10:00:00Z", note="timeout"),
    ]
    rows = attention_rows(history)
    assert len(rows) == 1
    assert rows[0]["company"] == "Globex"
    assert "Automation failed" in rows[0]["reason"]
    assert rows[0]["source_index"] == 1


def test_attention_rows_dedupe_repeated_events_per_job() -> None:
    history = [
        _event("error", JOB_A, "2026-08-20T10:00:00Z"),
        _event("failed", JOB_A, "2026-08-21T10:00:00Z"),
    ]
    assert len(attention_rows(history)) == 1


def test_submitted_rows_days_since_submission() -> None:
    history = [_event("submitted", JOB_A, "2026-08-27T12:00:00Z")]
    rows = submitted_rows(history, now=NOW)
    assert len(rows) == 1
    assert rows[0]["days_since_submission"] == 5


def test_submitted_rows_ignore_other_statuses_and_bad_timestamps() -> None:
    history = [
        _event("approved", JOB_A, "2026-08-20T10:00:00Z"),
        _event("submitted", JOB_B, "not-a-date"),
    ]
    rows = submitted_rows(history, now=NOW)
    assert len(rows) == 1
    assert rows[0]["days_since_submission"] is None


def test_history_table_rows_fall_back_to_package_notes() -> None:
    from job_automation import job_id

    matching_id = job_id(JOB_A)
    package = {"job_id": matching_id, "notes": "package-level note", "status": "approved"}
    history = [_event("submitted", JOB_A, "2026-08-20T10:00:00Z")]
    rows = history_table_rows(history, packages=[package])
    assert rows[0]["notes"] == "package-level note"

    # An event-level note wins over the package fallback.
    event_with_note = [_event("submitted", JOB_A, "2026-08-20T10:00:00Z", note="event note")]
    assert history_table_rows(event_with_note, packages=[package])[0]["notes"] == "event note"


def test_funnel_counts() -> None:
    history = [
        _event("submitted", JOB_A, "2026-08-20T10:00:00Z"),
        _event("failed", JOB_B, "2026-08-21T10:00:00Z"),
    ]
    packages = [
        {"status": "draft", "job_id": "d1"},
        {"status": "draft", "job_id": "d2"},
        {"status": "approved", "job_id": "a1"},
        {"status": "submitted", "job_id": "s1"},
    ]
    counts = funnel_counts(history, packages)
    assert counts == {
        "attention": 1,
        "pending": 2,
        "ready_to_apply": 1,
        "submitted": 1,
        "history_events": 2,
    }


def _days_ago(n: int) -> str:
    from datetime import timedelta

    return (NOW - timedelta(days=n)).isoformat().replace("+00:00", "Z")


def test_followup_rows_old_enough_and_no_response() -> None:
    history = [_event("submitted", JOB_A, _days_ago(FOLLOWUP_AFTER_DAYS + 2))]
    rows = followup_rows(history, now=NOW)
    assert len(rows) == 1
    assert rows[0]["company"] == "Acme"
    assert rows[0]["days_since_submission"] == FOLLOWUP_AFTER_DAYS + 2


def test_followup_rows_skips_recent_submissions() -> None:
    history = [_event("submitted", JOB_A, _days_ago(FOLLOWUP_AFTER_DAYS - 3))]
    assert followup_rows(history, now=NOW) == []


def test_followup_rows_stops_after_response_event() -> None:
    history = [
        _event("submitted", JOB_A, _days_ago(FOLLOWUP_AFTER_DAYS + 5)),
        _event("interview", JOB_A, _days_ago(1)),
    ]
    assert followup_rows(history, now=NOW) == []


def test_followup_rows_stops_after_logged_followup() -> None:
    history = [
        _event("submitted", JOB_A, _days_ago(FOLLOWUP_AFTER_DAYS + 5)),
        _event("follow-up", JOB_A, _days_ago(2)),
    ]
    assert followup_rows(history, now=NOW) == []


def test_followup_rows_ignores_response_before_submission() -> None:
    history = [
        _event("rejected", JOB_A, _days_ago(FOLLOWUP_AFTER_DAYS + 20)),
        _event("submitted", JOB_A, _days_ago(FOLLOWUP_AFTER_DAYS + 5)),
    ]
    rows = followup_rows(history, now=NOW)
    assert len(rows) == 1  # the rejection predates the submission


def test_followup_rows_oldest_first_and_one_row_per_job() -> None:
    history = [
        _event("submitted", JOB_A, _days_ago(FOLLOWUP_AFTER_DAYS + 12)),
        _event("submitted", JOB_B, _days_ago(FOLLOWUP_AFTER_DAYS + 1)),
        _event("submitted", JOB_A, _days_ago(FOLLOWUP_AFTER_DAYS + 2)),  # re-run resets age
    ]
    rows = followup_rows(history, now=NOW)
    assert len(rows) == 2
    assert rows[0]["company"] == "Acme"  # oldest first
    acme_age = next(r["days_since_submission"] for r in rows if r["company"] == "Acme")
    assert acme_age == FOLLOWUP_AFTER_DAYS + 2  # anchored on the most recent submission
