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

from job_automation import ApplicationHistory, job_id
from job_automation.history import VALID_STATUSES
from job_automation.packages import load_packages, save_packages


HISTORY_PATH = Path("output/application_history.json")
PACKAGES_PATH = Path("output/application_packages.json")
BLACKLIST_PATH = Path("output/blacklist.json")
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


def _last_lines(text: str, n: int = 30) -> str:
    lines = text.strip().splitlines()
    if len(lines) <= n:
        return text.strip()
    return "\n".join(["… (truncated)", *lines[-n:]])


def render_package_editor(package: dict[str, Any], form_key: str) -> None:
    with st.form(form_key):
        status = st.selectbox(
            "Review status",
            ["draft", "approved", "rejected"],
            index=["draft", "approved", "rejected"].index(package.get("status", "draft"))
            if package.get("status") in {"draft", "approved", "rejected"} else 0,
        )
        email = st.text_input("Contact email (for follow-up)", package.get("job", {}).get("email", ""))
        cover_letter = st.text_area("Tailored cover letter", package.get("cover_letter", ""), height=240)
        answers_json = st.text_area("Suggested answers (JSON)", json.dumps(package.get("answers", {}), indent=2), height=140)
        notes = st.text_area("Reviewer notes", package.get("notes", ""))
        saved = st.form_submit_button("Save", type="primary", icon=":material/save:")
    if saved:
        try:
            answers = json.loads(answers_json)
            if not isinstance(answers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items()):
                raise ValueError("Answers must be a JSON object of text values.")
        except (json.JSONDecodeError, ValueError) as exc:
            st.error(f"Suggested answers were not saved: {exc}")
            st.stop()
        package.setdefault("job", {})["email"] = email
        package.update({"status": status, "cover_letter": cover_letter, "answers": answers, "notes": notes})
        save_packages(packages, PACKAGES_PATH)
        load_package_rows.clear()
        st.rerun()


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
        status_text = st.empty()
        status_text.info("Loading resume...")
        success, output = run_project_command(["crew.py", "--search", "--query", search_query, "--location", search_location])
        if success:
            status_text.success("New review queue created.")
        else:
            status_text.error("Job search failed.")
        with st.expander("Command output"):
            st.code(_last_lines(output or "No command output"))
        if success:
            load_package_rows.clear()
            st.rerun()
    with st.form("add-package-form", border=False):
        add_url = st.text_input("Job URL", placeholder="https://...")
        add_title = st.text_input("Job title", value="Untitled")
        add_company = st.text_input("Company", value="Unknown")
        add_email = st.text_input("Contact email", placeholder="hr@company.com")
        add_package = st.form_submit_button("Add package", icon=":material/add:")
    if add_package and add_url:
        success, output = run_project_command([
            "crew.py", "--add-package", add_url, "--title", add_title,
            "--company", add_company, "--email", add_email,
        ])
        (st.success if success else st.error)("Package added." if success else "Failed to add package.")
        if success:
            load_package_rows.clear()
            st.rerun()
    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Extract emails", icon=":material/mail:", use_container_width=True):
            with st.spinner("Extracting emails..."):
                success, output = run_project_command(["monitor.py", "--extract-emails"])
            (st.success if success else st.error)(output or "Email extraction finished.")
    with col2:
        if st.button("Send follow-ups", icon=":material/send:", use_container_width=True):
            with st.spinner("Sending follow-ups..."):
                success, output = run_project_command(["monitor.py", "--after-days", str(follow_up_days), "--send-email", "--notify"])
            (st.success if success else st.error)(output or "Follow-ups sent.")
    if st.button("Generate weekly report", icon=":material/description:", use_container_width=True):
        with st.spinner("Generating weekly report…"):
            success, output = run_project_command(["report_weekly.py"])
        (st.success if success else st.error)(output or "Weekly report command finished.")

attention_rows: list[dict[str, Any]] = []
cutoff = datetime.now(timezone.utc) - timedelta(days=follow_up_days)
seen_job_ids: set[str] = set()
for event_index, event in enumerate(history):
    job = event.get("job", event)
    status = event.get("status", "")
    when = event_time(event)
    jid = job_id(job)
    if jid in seen_job_ids:
        continue
    reason = None
    if status in {"failed", "error"}:
        reason = "Automation failed — review and retry or complete manually"
    elif status in {"submitted", "applied", "success"} and when is not None and when <= cutoff:
        reason = f"Follow up — submitted {follow_up_days}+ days ago"
    if reason:
        seen_job_ids.add(jid)
        attention_rows.append({
            "reason": reason, "status": status,
            "company": job.get("company", "Unknown"), "title": job.get("title", "Untitled"),
            "email": job.get("email", ""),
            "url": job.get("url", ""), "timestamp": event.get("timestamp", event.get("created_at", "")),
            "job_id": jid,
            "source": "history", "source_index": event_index,
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
        "email": job.get("email", ""),
        "job_id": job_id(job),
        "submitted_at": event.get("timestamp", event.get("created_at", "")),
        "days_since_submission": age_days,
        "follow_up": "Due" if when and when <= cutoff else "Not due",
        "url": job.get("url", ""),
    })

with st.container(horizontal=True):
    st.metric("Needs attention", len(attention_rows), border=True)
    st.metric("Submitted", submitted, border=True)
    st.metric("Review queue", pending, border=True)
    st.metric("Ready to apply", ready_to_apply, border=True)
    st.metric("History events", len(history), border=True)

attention_tab, submitted_tab, review_tab, ready_tab, tracking_tab = st.tabs(
    ["Needs attention", "Submitted", "Review queue", "Ready to apply", "Application tracking"]
)

with attention_tab:
    st.caption("Includes failed automation, submitted applications due for follow-up, and draft packages awaiting review.")
    if attention_rows:
        st.dataframe(
            pd.DataFrame(attention_rows)[["reason", "status", "company", "title", "email", "job_id", "url", "timestamp"]],
            hide_index=True,
            column_config={
                "url": st.column_config.LinkColumn("Job listing"),
                "job_id": st.column_config.TextColumn("Job ID"),
            },
        )
        selected_attention = st.selectbox(
            "Edit an attention item",
            range(len(attention_rows)),
            index=st.session_state.get("attention_selected", 0) if st.session_state.get("attention_selected", 0) < len(attention_rows) else 0,
            format_func=lambda index: (
                f"{attention_rows[index]['company']} — {attention_rows[index]['title']}: "
                f"{attention_rows[index]['reason']}"
            ),
            key="attention_select",
        )
        st.session_state["attention_selected"] = selected_attention
        item = attention_rows[selected_attention]
        if item["source"] == "history":
            event = history[item["source_index"]]
            with st.form(f"attention-history-{item['source_index']}"):
                status = st.selectbox(
                    "Application status", sorted(VALID_STATUSES),
                    index=sorted(VALID_STATUSES).index(event.get("status", "failed")),
                    key=f"attention-status-{item['source_index']}",
                )
                notes = st.text_area("Notes", event.get("details", {}).get("note", ""), key=f"attention-notes-{item['source_index']}")
                saved = st.form_submit_button("Save attention item", type="primary", icon=":material/save:")
            if saved:
                event["status"] = status
                event.setdefault("details", {})["note"] = notes
                ApplicationHistory(HISTORY_PATH).replace(history)
                load_history_rows.clear()
                st.rerun()
            job_url = item.get("url", "")
            if job_url and st.button("Block URL", key="attention-block-history", icon=":material/block:"):
                from urllib.parse import urlparse as _urlparse
                domain = _urlparse(job_url).netloc.lower()
                try:
                    blacklist = json.loads(BLACKLIST_PATH.read_text(encoding="utf-8")) if BLACKLIST_PATH.exists() else []
                except (json.JSONDecodeError, OSError):
                    blacklist = []
                if domain not in blacklist:
                    blacklist.append(domain)
                    BLACKLIST_PATH.write_text(json.dumps(blacklist, indent=2), encoding="utf-8")
                event["status"] = "rejected"
                ApplicationHistory(HISTORY_PATH).replace(history)
                load_history_rows.clear()
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
                "job_id": st.column_config.TextColumn("Job ID"),
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
        for idx, package in enumerate(review_packages):
            job = package["job"]
            with st.container(border=True):
                st.subheader(f"{job.get('title', 'Untitled')} at {job.get('company', 'Unknown')}")
                st.caption(f"{job.get('location', 'Location not specified')} · fit score: {job.get('score', 'n/a')} · ID: `{package['job_id']}`")
                if job.get("url"):
                    st.link_button("Open job listing", job["url"], icon=":material/open_in_new:")
                st.write(job.get("rationale", "No rationale was saved."))
                col1, col2, col3 = st.columns([1, 1, 4])
                with col1:
                    if st.button("Approve", key=f"approve-{idx}", type="primary", icon=":material/check:"):
                        package["status"] = "approved"
                        save_packages(packages, PACKAGES_PATH)
                        load_package_rows.clear()
                        st.rerun()
                with col2:
                    if st.button("Reject", key=f"reject-{idx}", icon=":material/close:"):
                        package["status"] = "rejected"
                        save_packages(packages, PACKAGES_PATH)
                        load_package_rows.clear()
                        st.rerun()
                with col3:
                    if not package.get("cover_letter"):
                        if st.button("Generate cover letter", key=f"gen-cover-{idx}", icon=":material/auto_awesome:"):
                            package["status"] = "approved"
                            save_packages(packages, PACKAGES_PATH)
                            with st.spinner("Generating a letter for this job…"):
                                success, output = run_project_command(["crew.py", "--generate-cover", package["job_id"]])
                            (st.success if success else st.error)("Cover letter generated." if success else "Cover-letter generation failed.")
                            if not success:
                                with st.expander("Command output"):
                                    st.code(_last_lines(output or "No command output"))
                            else:
                                load_package_rows.clear()
                                st.rerun()
                    else:
                        auto_submit = st.checkbox(
                            "Auto-submit",
                            key=f"auto-submit-review-{idx}",
                        )
                        if st.button("Open in browser", key=f"open-review-{idx}", icon=":material/open_in_new:"):
                            arguments = [
                                "crew.py", "--apply-existing", "--job-id", package["job_id"],
                                "--playwright", "--review",
                            ]
                            if auto_submit:
                                arguments.append("--auto-submit")
                            success, message = launch_in_terminal(arguments)
                            (st.success if success else st.error)(message)

with ready_tab:
    approved_packages = [package for package in packages if package.get("status") == "approved"]
    st.caption("Approved packages ready for the browser application flow. You can still edit or return one to draft.")
    if not approved_packages:
        st.info("No approved packages are ready to apply.")
    else:
        batch_auto_submit = st.checkbox(
            "Auto-submit for batch apply",
            key="batch-auto-submit",
        )
        if st.button("Apply all approved", type="primary", icon=":material/play_arrow:", key="batch-apply"):
            arguments = [
                "crew.py", "--apply-existing", "--playwright", "--review",
                "--max-applications", str(len(approved_packages)),
            ]
            if batch_auto_submit:
                arguments.append("--auto-submit")
            success, message = launch_in_terminal(arguments)
            (st.success if success else st.error)(message)
        st.divider()
        for idx, package in enumerate(approved_packages):
            job = package["job"]
            with st.container(border=True):
                st.subheader(f"{job.get('title', 'Untitled')} at {job.get('company', 'Unknown')}")
                st.caption(f"ID: `{package['job_id']}`")
                if job.get("url"):
                    st.link_button("Open job listing", job["url"], icon=":material/open_in_new:")
                st.write(job.get("rationale", "No rationale was saved."))
                if package.get("cover_letter"):
                    with st.expander("Cover letter"):
                        st.write(package["cover_letter"])
                col1, col2, col3, col4, col5 = st.columns([1, 2, 1, 1, 1])
                with col1:
                    auto_submit = st.checkbox(
                        "Auto-submit",
                        key=f"ready-auto-submit-{idx}",
                    )
                with col2:
                    if st.button("Open in browser", type="primary", key=f"ready-apply-{idx}", icon=":material/open_in_new:"):
                        arguments = [
                            "crew.py", "--apply-existing", "--job-id", package["job_id"],
                            "--playwright", "--review",
                        ]
                        if auto_submit:
                            arguments.append("--auto-submit")
                        success, message = launch_in_terminal(arguments)
                        (st.success if success else st.error)(message)
                with col3:
                    if st.button("Submitted", key=f"ready-submitted-{idx}", icon=":material/check_circle:", help="Mark as manually submitted"):
                        event = {
                            "job": job,
                            "status": "submitted",
                            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "details": {"note": "Manually marked as submitted from dashboard"},
                        }
                        ApplicationHistory(HISTORY_PATH).append(event)
                        package["status"] = "submitted"
                        save_packages(packages, PACKAGES_PATH)
                        load_package_rows.clear()
                        load_history_rows.clear()
                        st.rerun()
                with col4:
                    if st.button("Reject", key=f"ready-reject-{idx}", icon=":material/close:"):
                        package["status"] = "rejected"
                        save_packages(packages, PACKAGES_PATH)
                        load_package_rows.clear()
                        st.rerun()
                with col5:
                    if job.get("url") and st.button("Block URL", key=f"ready-block-{idx}", icon=":material/block:"):
                        from urllib.parse import urlparse as _urlparse
                        domain = _urlparse(job["url"]).netloc.lower()
                        try:
                            blacklist = json.loads(BLACKLIST_PATH.read_text(encoding="utf-8")) if BLACKLIST_PATH.exists() else []
                        except (json.JSONDecodeError, OSError):
                            blacklist = []
                        if domain not in blacklist:
                            blacklist.append(domain)
                            BLACKLIST_PATH.write_text(json.dumps(blacklist, indent=2), encoding="utf-8")
                        package["status"] = "rejected"
                        save_packages(packages, PACKAGES_PATH)
                        load_package_rows.clear()
                        st.rerun()

with tracking_tab:
    if not history:
        st.info("No application events have been recorded yet.")
    else:
        st.subheader("Full history (editable)")
        table = pd.DataFrame([
            {
                "timestamp": event.get("timestamp", event.get("created_at", "")),
                "status": event.get("status", ""),
                "company": event.get("job", {}).get("company", event.get("company", "")),
                "title": event.get("job", {}).get("title", event.get("title", "")),
                "email": event.get("job", {}).get("email", event.get("email", "")),
                "job_id": job_id(event.get("job", event)),
                "url": event.get("job", {}).get("url", event.get("url", "")),
                "notes": event.get("details", {}).get("note", event.get("notes", "")),
            }
            for event in history
        ])
        edited = st.data_editor(
            table,
            key="history_editor",
            hide_index=True,
            disabled=["timestamp", "company", "title", "job_id", "url"],
            column_config={
                "status": st.column_config.SelectboxColumn("Status", options=sorted(VALID_STATUSES)),
                "url": st.column_config.LinkColumn("Job listing"),
                "job_id": st.column_config.TextColumn("Job ID"),
            },
        )
        if st.button("Save tracking changes", icon=":material/save:"):
            for index, row in edited.iterrows():
                history[index]["status"] = row["status"]
                history[index].setdefault("job", {})["email"] = row["email"]
                history[index].setdefault("details", {})["note"] = row["notes"]
            ApplicationHistory(HISTORY_PATH).replace(history)
            load_history_rows.clear()
            st.success("Tracking changes saved.")
