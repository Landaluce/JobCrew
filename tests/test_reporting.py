"""Tests for the reporting scripts: monitor.py (days since submission) and
report_weekly.py (metrics aggregation + markdown rendering). Pure logic only —
no reportlab PDFs are generated against the real output directory.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import monitor
import report_weekly


def _ts(days_ago: float) -> str:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return when.isoformat().replace("+00:00", "Z")


def _event(status: str, days_ago: float = 0.0, **details: object) -> dict:
    job = {"title": "Engineer", "company": "Acme", "url": "https://jobs.lever.co/acme/1"}
    return {
        "timestamp": _ts(days_ago),
        "status": status,
        "job": job,
        "details": details,
    }


class TestMonitor:
    def test_submitted_summary_lists_only_submitted(self) -> None:
        records = [_event("submitted", 3.0), _event("failed", 1.0), _event("draft")]
        lines = monitor.submitted_summary(records)
        assert len(lines) == 2  # header + one submitted row
        assert "Acme — Engineer" in lines[1]
        assert "3 days ago" in lines[1]

    def test_submitted_summary_empty(self) -> None:
        assert monitor.submitted_summary([]) == []
        assert monitor.submitted_summary([_event("prepared")]) == []

    def test_submitted_summary_unknown_age_for_bad_timestamp(self) -> None:
        event = {
            "timestamp": "not-a-date",
            "status": "submitted",
            "job": {"title": "X", "company": "Y", "url": "https://x.example/job/1"},
        }
        lines = monitor.submitted_summary([event])
        assert "unknown age" in lines[1]

    def test_submitted_summary_uses_fixed_now(self) -> None:
        records = [_event("submitted", 10.0)]
        fixed_now = datetime.now(timezone.utc) - timedelta(days=5)
        lines = monitor.submitted_summary(records, now=fixed_now)
        assert "5 days ago" in lines[1]  # age is relative to the supplied now


class TestReportWeekly:
    def test_event_datetime_parses_zulu_and_rejects_garbage(self) -> None:
        parsed = report_weekly.event_datetime({"timestamp": _ts(0.0)})
        assert parsed is not None
        assert parsed.tzinfo is not None
        assert report_weekly.event_datetime({"timestamp": ""}) is None
        assert report_weekly.event_datetime({"timestamp": "junk"}) is None

    def test_in_window_keeps_recent_and_timeless_events(self) -> None:
        assert report_weekly.in_window(_event("draft", 2.0), days=7)
        assert not report_weekly.in_window(_event("draft", 30.0), days=7)
        timeless = {"status": "draft", "job": {}, "details": {}}
        assert report_weekly.in_window(timeless, days=7)

    def test_compute_metrics_counts_and_rate(self) -> None:
        history = [
            _event("submitted", 1.0, site="lever", approval_turnaround_hours=2.0),
            _event("submitted", 2.0, site="lever", approval_turnaround_hours=4.0),
            _event("failed", 3.0, site="workday"),
            _event("prepared", 4.0),
        ]
        metrics = report_weekly.compute_metrics(history, days=7)
        assert metrics["total_attempts"] == 4
        assert metrics["submission_success_rate"] == 2 / 3
        assert metrics["avg_approval_turnaround_hours"] == 3.0
        assert dict(metrics["status_counts"]) == {"submitted": 2, "failed": 1, "prepared": 1}
        assert metrics["top_sites"][0][0] == "lever"
        assert len(metrics["daily_counts"]) == 4

    def test_compute_metrics_empty_history(self) -> None:
        metrics = report_weekly.compute_metrics([], days=7)
        assert metrics["total_attempts"] == 0
        assert metrics["avg_approval_turnaround_hours"] is None
        assert metrics["submission_success_rate"] == 0.0

    def test_save_markdown_renders_sections(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(report_weekly, "REPORT_MD", str(tmp_path / "report.md"))
        metrics = report_weekly.compute_metrics(
            [_event("submitted", 1.0), _event("failed", 2.0)], days=7
        )
        report_weekly.save_markdown(metrics)
        content = (tmp_path / "report.md").read_text(encoding="utf-8")
        assert "Weekly Job Application Report" in content
        assert "Top Sites" in content
        assert "Status Breakdown" in content
        assert "Engineer" in content  # rendered via top roles
        assert "unknown" in content  # no site detail → falls back to 'unknown'
