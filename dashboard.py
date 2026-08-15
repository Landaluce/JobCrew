from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from job_automation import ApplicationHistory
from job_automation.history import VALID_STATUSES
from job_automation.packages import load_packages, save_packages


HISTORY_PATH = Path("output/application_history.json")
PACKAGES_PATH = Path("output/application_packages.json")
PROJECT_ROOT = Path(__file__).resolve().parent


@st.cache_data(ttl="15s")
def load_history_rows(path: str, modified_at: int) -> list[dict[str, Any]]:
    return ApplicationHistory(path).records()


@st.cache_data(ttl="15s")
def load_package_rows(path: str, modified_at: int) -> list[dict[str, Any]]:
    return load_packages(path)


def modified_at(path: Path) -> int:
    return path.stat().st_mtime_ns if path.exists() else 0


def event_time(event: dict[str, Any]) -> datetime | None:
    timestamp = event.get("timestamp", event.get("created_at", ""))
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def run_project_command(arguments: list[str]) -> tuple[bool, str]:
    """Run a supported JobCrew command from the dashboard, with captured output."""
    completed = subprocess.run(
        [sys.executable, *arguments], cwd=PROJECT_ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    return completed.returncode == 0, completed.stdout


def launch_in_terminal(arguments: list[str]) -> tuple[bool, str]:
    """Open an interactive terminal for browser review, which needs a real stdin."""
    terminal = shutil.which("x-terminal-emulator")
    if not terminal:
        return False, "No terminal emulator was found. Use the displayed command in your terminal."
    command = " ".join(shlex.quote(part) for part in [sys.executable, *arguments])
    subprocess.Popen([terminal, "-e", "bash", "-lc", f"cd {shlex.quote(str(PROJECT_ROOT))} && {command}; exec bash"])
    return True, "Opened an interactive terminal for browser review."


st.set_page_config(page_title="Job application workspace", page_icon=":material/work:", layout="wide")
st.title("Job application workspace")
st.caption("Review packages before applying, then track the application funnel in one place.")

history = load_history_rows(str(HISTORY_PATH), modified_at(HISTORY_PATH))
packages = load_package_rows(str(PACKAGES_PATH), modified_at(PACKAGES_PATH))

submitted = sum(row.get("status") in {"submitted", "applied", "success"} for row in history)
pending = sum(package.get("status") == "draft" for package in packages)
ready_to_apply = sum(package.get("status") == "approved" for package in packages)
with st.sidebar:
    follow_up_days = st.number_input("Follow up after (days)", min_value=1, max_value=90, value=7)
    st.divider()
    st.subheader("Run JobCrew")
    with st.form("new-search", border=False):
        search_query = st.text_input("Job search query", value="python developer remote")
        search_location = st.text_input("Location", value="Remote")
        run_search = st.form_submit_button("Create a new review queue", icon=":material/search:")
    if run_search:
        with st.spinner("Searching and ranking jobs…"):
            success, output = run_project_command(["crew.py", "--query", search_query, "--location", search_location])
        (st.success if success else st.error)("New review queue created." if success else "Job search failed.")
        st.code(output or "No command output")
        if success:
            load_package_rows.clear()
            st.rerun()
    if st.button("Generate weekly report", icon=":material/description:"):
        success, output = run_project_command(["report_weekly.py"])
        (st.success if success else st.error)(output or "Weekly report command finished.")
    if st.button("Check follow-ups", icon=":material/notifications:"):
        success, output = run_project_command(["monitor.py", "--after-days", str(follow_up_days)])
        (st.success if success else st.error)(output or "Follow-up check finished.")

attention_rows: list[dict[str, Any]] = []
cutoff = datetime.now(timezone.utc) - timedelta(days=follow_up_days)
for event_index, event in enumerate(history):
    job = event.get("job", event)
    status = event.get("status", "")
    when = event_time(event)
    reason = None
    if status in {"failed", "error"}:
        reason = "Automation failed — review and retry or complete manually"
    elif status in {"submitted", "applied", "success"} and when is not None and when <= cutoff:
        reason = f"Follow up — submitted {follow_up_days}+ days ago"
    if reason:
        attention_rows.append({
            "reason": reason, "status": status,
            "company": job.get("company", "Unknown"), "title": job.get("title", "Untitled"),
            "url": job.get("url", ""), "timestamp": event.get("timestamp", event.get("created_at", "")),
            "source": "history", "source_index": event_index,
        })
for package_index, package in enumerate(packages):
    if package.get("status") == "draft":
        job = package.get("job", {})
        attention_rows.append({
            "reason": "Review required before applying", "status": "draft",
            "company": job.get("company", "Unknown"), "title": job.get("title", "Untitled"),
            "url": job.get("url", ""), "timestamp": package.get("created_at", ""),
            "source": "package", "source_index": package_index,
        })

submitted_rows: list[dict[str, Any]] = []
for event in history:
    if event.get("status") not in {"submitted", "applied", "success"}:
        continue
    job = event.get("job", event)
    when = event_time(event)
    age_days = (datetime.now(timezone.utc) - when).days if when else None
    submitted_rows.append({
        "company": job.get("company", "Unknown"),
        "title": job.get("title", "Untitled"),
        "submitted_at": event.get("timestamp", event.get("created_at", "")),
        "days_since_submission": age_days,
        "follow_up": "Due" if when and when <= cutoff else "Not due",
        "url": job.get("url", ""),
    })

with st.container(horizontal=True):
    st.metric("Review queue", pending, border=True)
    st.metric("Ready to apply", ready_to_apply, border=True)
    st.metric("Submitted", submitted, border=True)
    st.metric("Needs attention", len(attention_rows), border=True)
    st.metric("History events", len(history), border=True)

attention_tab, submitted_tab, review_tab, ready_tab, tracking_tab = st.tabs(
    ["Needs attention", "Submitted", "Review queue", "Ready to apply", "Application tracking"]
)

with attention_tab:
    st.caption("Includes failed automation, submitted applications due for follow-up, and draft packages awaiting review.")
    if attention_rows:
        st.dataframe(
            pd.DataFrame(attention_rows)[["reason", "status", "company", "title", "url", "timestamp"]],
            hide_index=True,
            column_config={"url": st.column_config.LinkColumn("Job listing")},
        )
        selected_attention = st.selectbox(
            "Edit an attention item",
            range(len(attention_rows)),
            format_func=lambda index: (
                f"{attention_rows[index]['company']} — {attention_rows[index]['title']}: "
                f"{attention_rows[index]['reason']}"
            ),
        )
        item = attention_rows[selected_attention]
        if item["source"] == "history":
            event = history[item["source_index"]]
            with st.form(f"attention-history-{item['source_index']}"):
                status = st.selectbox(
                    "Application status", sorted(VALID_STATUSES),
                    index=sorted(VALID_STATUSES).index(event.get("status", "failed")),
                )
                notes = st.text_area("Notes", event.get("details", {}).get("note", ""))
                saved = st.form_submit_button("Save attention item", type="primary", icon=":material/save:")
            if saved:
                event["status"] = status
                event.setdefault("details", {})["note"] = notes
                ApplicationHistory(HISTORY_PATH).replace(history)
                load_history_rows.clear()
                st.rerun()
        else:
            package = packages[item["source_index"]]
            with st.form(f"attention-package-{package['job_id']}"):
                status = st.selectbox("Review status", ["draft", "approved", "rejected"])
                cover_letter = st.text_area("Tailored cover letter", package.get("cover_letter", ""), height=240)
                answers_json = st.text_area("Suggested answers (JSON)", json.dumps(package.get("answers", {}), indent=2), height=140)
                notes = st.text_area("Reviewer notes", package.get("notes", ""))
                saved = st.form_submit_button("Save attention item", type="primary", icon=":material/save:")
            if saved:
                try:
                    answers = json.loads(answers_json)
                    if not isinstance(answers, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in answers.items()):
                        raise ValueError("Answers must be a JSON object of text values.")
                except (json.JSONDecodeError, ValueError) as exc:
                    st.error(f"Suggested answers were not saved: {exc}")
                    st.stop()
                package.update({"status": status, "cover_letter": cover_letter, "answers": answers, "notes": notes})
                save_packages(packages, PACKAGES_PATH)
                load_package_rows.clear()
                st.rerun()
    else:
        st.success("Nothing needs attention right now.")

with submitted_tab:
    st.caption("Applications marked submitted, applied, or successful. Follow-up status uses the sidebar setting.")
    if submitted_rows:
        st.dataframe(
            pd.DataFrame(submitted_rows),
            hide_index=True,
            column_config={
                "url": st.column_config.LinkColumn("Job listing"),
                "days_since_submission": st.column_config.NumberColumn("Days since submission", format="%d"),
            },
        )
    else:
        st.info("No submitted applications have been recorded yet.")

with review_tab:
    review_packages = [package for package in packages if package.get("status", "draft") == "draft"]
    if not packages:
        st.info("No application packages yet. Run `crew.py` to create a shortlist and tailored review packages.")
    elif not review_packages:
        st.success("All application packages have been reviewed.")
    else:
        choices = {
            f"{item['job'].get('company', 'Unknown')} — {item['job'].get('title', 'Untitled')} ({item.get('status', 'draft')})": index
            for index, item in enumerate(review_packages)
        }
        selected = st.selectbox("Application package", choices)
        package = review_packages[choices[selected]]
        job = package["job"]
        with st.container(border=True):
            st.subheader(f"{job.get('title', 'Untitled')} at {job.get('company', 'Unknown')}")
            st.caption(f"{job.get('location', 'Location not specified')} · fit score: {job.get('score', 'n/a')}")
            if job.get("url"):
                st.link_button("Open job listing", job["url"], icon=":material/open_in_new:")
            st.write(job.get("rationale", "No rationale was saved."))
        if not package.get("cover_letter"):
            if st.button("Generate tailored cover letter", icon=":material/auto_awesome:"):
                with st.spinner("Generating a letter for this approved job…"):
                    success, output = run_project_command(["crew.py", "--generate-cover", package["job_id"]])
                (st.success if success else st.error)("Cover letter generated." if success else "Cover-letter generation failed.")
                if not success:
                    st.code(output or "No command output")
                else:
                    load_package_rows.clear()
                    st.rerun()
        else:
            auto_submit = st.checkbox(
                "Automatically click an explicitly labelled final Submit button",
                key=f"auto-submit-{package['job_id']}",
            )
            if st.button("Open application in browser", icon=":material/open_in_new:"):
                arguments = [
                    "crew.py", "--apply-existing", "--job-id", package["job_id"],
                    "--playwright", "--review",
                ]
                if auto_submit:
                    arguments.append("--auto-submit")
                success, message = launch_in_terminal(arguments)
                (st.success if success else st.error)(message)

        with st.form(f"package-{package['job_id']}"):
            status = st.selectbox("Review status", ["draft", "approved", "rejected"], index=["draft", "approved", "rejected"].index(package.get("status", "draft")) if package.get("status") in {"draft", "approved", "rejected"} else 0)
            cover_letter = st.text_area("Tailored cover letter", package.get("cover_letter", ""), height=240)
            answers_json = st.text_area("Suggested answers (JSON)", json.dumps(package.get("answers", {}), indent=2), height=140)
            notes = st.text_area("Reviewer notes", package.get("notes", ""))
            saved = st.form_submit_button("Save review", type="primary", icon=":material/save:")
        if saved:
            try:
                answers = json.loads(answers_json)
                if not isinstance(answers, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in answers.items()):
                    raise ValueError("Answers must be a JSON object of text values.")
            except (json.JSONDecodeError, ValueError) as exc:
                st.error(f"Suggested answers were not saved: {exc}")
                st.stop()
            package.update({"status": status, "cover_letter": cover_letter, "answers": answers, "notes": notes})
            save_packages(packages, PACKAGES_PATH)
            load_package_rows.clear()
            st.rerun()

with ready_tab:
    approved_packages = [package for package in packages if package.get("status") == "approved"]
    st.caption("Approved packages ready for the browser application flow. You can still edit or return one to draft.")
    if not approved_packages:
        st.info("No approved packages are ready to apply.")
    else:
        choices = {
            f"{item['job'].get('company', 'Unknown')} — {item['job'].get('title', 'Untitled')}": index
            for index, item in enumerate(approved_packages)
        }
        selected = st.selectbox("Approved application package", choices)
        package = approved_packages[choices[selected]]
        job = package["job"]
        with st.container(border=True):
            st.subheader(f"{job.get('title', 'Untitled')} at {job.get('company', 'Unknown')}")
            if job.get("url"):
                st.link_button("Open job listing", job["url"], icon=":material/open_in_new:")
            st.write(job.get("rationale", "No rationale was saved."))
        with st.form(f"ready-package-{package['job_id']}"):
            status = st.selectbox("Review status", ["approved", "draft", "rejected"])
            cover_letter = st.text_area("Tailored cover letter", package.get("cover_letter", ""), height=240)
            answers_json = st.text_area("Suggested answers (JSON)", json.dumps(package.get("answers", {}), indent=2), height=140)
            notes = st.text_area("Reviewer notes", package.get("notes", ""))
            saved = st.form_submit_button("Save package", type="primary", icon=":material/save:")
        if saved:
            try:
                answers = json.loads(answers_json)
                if not isinstance(answers, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in answers.items()):
                    raise ValueError("Answers must be a JSON object of text values.")
            except (json.JSONDecodeError, ValueError) as exc:
                st.error(f"Suggested answers were not saved: {exc}")
                st.stop()
            package.update({"status": status, "cover_letter": cover_letter, "answers": answers, "notes": notes})
            save_packages(packages, PACKAGES_PATH)
            load_package_rows.clear()
            st.rerun()

with tracking_tab:
    if not history:
        st.info("No application events have been recorded yet.")
    else:
        table = pd.DataFrame([
            {
                "timestamp": event.get("timestamp", event.get("created_at", "")),
                "status": event.get("status", ""),
                "company": event.get("job", {}).get("company", event.get("company", "")),
                "title": event.get("job", {}).get("title", event.get("title", "")),
                "url": event.get("job", {}).get("url", event.get("url", "")),
                "notes": event.get("details", {}).get("note", event.get("notes", "")),
            }
            for event in history
        ])
        edited = st.data_editor(
            table,
            key="history_editor",
            hide_index=True,
            disabled=["timestamp", "company", "title", "url"],
            column_config={
                "status": st.column_config.SelectboxColumn("Status", options=sorted(VALID_STATUSES)),
                "url": st.column_config.LinkColumn("Job listing"),
            },
        )
        if st.button("Save tracking changes", icon=":material/save:"):
            for index, row in edited.iterrows():
                history[index]["status"] = row["status"]
                history[index].setdefault("details", {})["note"] = row["notes"]
            ApplicationHistory(HISTORY_PATH).replace(history)
            load_history_rows.clear()
            st.success("Tracking changes saved.")
