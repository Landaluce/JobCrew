import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HISTORY_JSON = "output/application_history.json"
REPORT_MD = "output/weekly_report.md"
REPORT_PDF = "output/weekly_report.pdf"
METRICS_JSON = "output/application_metrics.json"


def load_history():
    if not os.path.exists(HISTORY_JSON):
        return []
    with open(HISTORY_JSON, encoding="utf-8") as f:
        return json.load(f)


def event_datetime(event):
    ts = event.get("timestamp", event.get("created_at", ""))
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def in_window(event, days):
    """Keep events inside the last `days` days; events without a timestamp are kept."""
    when = event_datetime(event)
    if when is None:
        return True
    return when >= datetime.now(timezone.utc) - timedelta(days=days)


def compute_metrics(history, days):
    daily = defaultdict(int)
    status = Counter()
    sites = Counter()
    roles = Counter()
    turnaround = []

    for e in history:
        job = e.get("job", {})
        details = e.get("details", {})
        status[e.get("status", "")] += 1
        sites[details.get("site") or job.get("source") or "unknown"] += 1
        roles[job.get("title") or "unknown"] += 1
        when = event_datetime(e)
        if when:
            daily[when.date().isoformat()] += 1
        if isinstance(details.get("approval_turnaround_hours"), (int, float)):
            turnaround.append(details["approval_turnaround_hours"])

    submitted = sum(1 for e in history if e.get("status") in {"submitted"})
    failed = sum(1 for e in history if e.get("status") in {"failed", "error"})
    rate = submitted / max(1, submitted + failed)

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=days)
    timestamps = [event_datetime(e) for e in history if event_datetime(e) is not None]
    earliest = min(timestamps) if timestamps else window_start

    return {
        "window_days": days,
        "window_start": max(window_start, earliest).isoformat(),
        "window_end": now.isoformat(),
        "total_attempts": len(history),
        "avg_applications_per_day": len(history) / max(1, days),
        "submission_success_rate": rate,
        "avg_approval_turnaround_hours": mean(turnaround) if turnaround else None,
        "status_counts": status.most_common(),
        "top_sites": sites.most_common(10),
        "top_roles": roles.most_common(10),
        "daily_counts": dict(sorted(daily.items())),
    }


def save_markdown(metrics):
    lines = [
        "# Weekly Job Application Report",
        "",
        f"- Reporting window: last {metrics['window_days']} days "
        f"({metrics['window_start']} → {metrics['window_end']})",
        f"- Total attempts: {metrics['total_attempts']}",
        f"- Avg applications/day: {metrics['avg_applications_per_day']:.2f}",
        f"- Submission success rate: {metrics['submission_success_rate']:.2%}",
        (
            f"- Avg approval turnaround hours: {metrics['avg_approval_turnaround_hours']:.2f}"
            if metrics["avg_approval_turnaround_hours"] is not None
            else "- Avg approval turnaround hours: n/a"
        ),
        "",
        "## Top Sites\n",
    ]
    for k, v in metrics["top_sites"]:
        lines.append(f"- {k}: {v}")
    lines += ["", "## Top Roles\n"]
    for k, v in metrics["top_roles"]:
        lines.append(f"- {k}: {v}")
    lines += ["", "## Status Breakdown\n"]
    for k, v in metrics["status_counts"]:
        lines.append(f"- {k}: {v}")
    lines += ["", "## Daily Volume\n"]
    for k, v in metrics["daily_counts"].items():
        lines.append(f"- {k}: {v}")

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def save_pdf(metrics):
    c = canvas.Canvas(REPORT_PDF, pagesize=letter)
    width, height = letter
    y = height - 40

    def write(line, size=10, gap=14):
        nonlocal y
        if y < 60:
            c.showPage()
            y = height - 40
        c.setFont("Helvetica", size)
        c.drawString(40, y, line[:110])
        y -= gap

    write("Weekly Job Application Report", 14, 20)
    write(f"Reporting window: last {metrics['window_days']} days")
    write(f"Total attempts: {metrics['total_attempts']}")
    write(f"Avg applications/day: {metrics['avg_applications_per_day']:.2f}")
    write(f"Submission success rate: {metrics['submission_success_rate']:.2%}")
    if metrics["avg_approval_turnaround_hours"] is not None:
        write(f"Avg approval turnaround hours: {metrics['avg_approval_turnaround_hours']:.2f}")
    else:
        write("Avg approval turnaround hours: n/a")
    write("")
    write("Top Sites:", 12)
    for k, v in metrics["top_sites"]:
        write(f"- {k}: {v}")
    write("")
    write("Top Roles:", 12)
    for k, v in metrics["top_roles"]:
        write(f"- {k}: {v}")
    c.save()


def main():
    parser = argparse.ArgumentParser(description="Generate a metrics report for the last N days")
    parser.add_argument("--days", type=int, default=7, help="Number of days to include in the report (default: 7)")
    args = parser.parse_args()

    os.makedirs("output", exist_ok=True)
    history = [e for e in load_history() if in_window(e, args.days)]
    metrics = compute_metrics(history, args.days)
    with open(METRICS_JSON, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    save_markdown(metrics)
    save_pdf(metrics)
    print(f"Weekly report generated (last {args.days} days, {metrics['total_attempts']} events).")


if __name__ == "__main__":
    main()
