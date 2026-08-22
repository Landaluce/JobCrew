from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from job_automation import ApplicationHistory, job_id, load_config
from job_automation.history import VALID_STATUSES
from job_automation.packages import load_packages, save_packages

CONFIG = load_config(Path(__file__).parent)

# Status color mapping
STATUS_COLORS = {
    "draft": "#6c757d",
    "approved": "#198754",
    "prepared": "#0d6efd",
    "submitted": "#0d6efd",
    "rejected": "#dc3545",
    "failed": "#dc3545",
    "error": "#dc3545",
    "interview": "#6f42c1",
    "offer": "#198754",
    "withdrawn": "#6c757d",
    "skipped_invalid_url": "#6c757d",
}


def status_badge(status: str) -> str:
    """Return HTML for a colored status badge."""
    color = STATUS_COLORS.get(status, "#6c757d")
    return f'<span style="background-color: {color}; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 500;">{status.upper()}</span>'


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


def run_project_command_streaming(arguments: list[str], log_container) -> tuple[bool, str]:
    """Run a command and stream output to a container in real-time."""
    process = subprocess.Popen(
        [sys.executable, *arguments],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    
    output_lines = []
    with log_container:
        log_placeholder = st.empty()
        for line in iter(process.stdout.readline, ''):
            output_lines.append(line)
            log_placeholder.code("".join(output_lines[-50:]), language="bash")
        process.stdout.close()
        process.wait()
    
    return process.returncode == 0, "".join(output_lines)


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


def confirm_dialog(message: str, key: str) -> bool:
    """Render a confirmation dialog."""
    if f"confirm_{key}" not in st.session_state:
        st.session_state[f"confirm_{key}"] = False
    
    if not st.session_state[f"confirm_{key}"]:
        if st.button(f"⚠️ {message}", key=f"confirm_btn_{key}", type="secondary"):
            st.session_state[f"confirm_{key}"] = True
            st.rerun()
        return False
    else:
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Confirm", key=f"confirm_yes_{key}", type="primary"):
                st.session_state[f"confirm_{key}"] = False
                return True
        with col2:
            if st.button("❌ Cancel", key=f"confirm_no_{key}", type="secondary"):
                st.session_state[f"confirm_{key}"] = False
                st.rerun()
        return False


def inject_keyboard_shortcuts():
    """Inject JavaScript for keyboard shortcuts."""
    st.html("""
    <script>
    document.addEventListener('keydown', function(e) {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) {
            return;
        }
        if (e.key === 'r' && !e.ctrlKey && !e.metaKey) {
            window.location.reload();
        }
        if (e.key === 'a' && !e.ctrlKey && !e.metaKey) {
            const approveBtn = document.querySelector('button[key^="approve-"]:not(:disabled)');
            if (approveBtn) approveBtn.click();
        }
        if (e.key === 'j' && !e.ctrlKey && !e.metaKey) {
            const nextRow = document.querySelector('tr[data-selected] + tr');
            if (nextRow) nextRow.click();
        }
        if (e.key === 'k' && !e.ctrlKey && !e.metaKey) {
            const prevRow = document.querySelector('tr[data-selected]');
            if (prevRow && prevRow.previousElementSibling) prevRow.previousElementSibling.click();
        }
    });
    </script>
    """, unsafe_allow_javascript=True)


def sync_package_status(target_job_id: str, job: dict[str, Any], new_status: str) -> None:
    """Mirror a status change onto the matching application package so it appears in the right tab."""
    for package in packages:
        if package.get("job_id") == target_job_id:
            package["status"] = new_status
            break
    else:
        packages.append({
            "job_id": target_job_id,
            "job": job,
            "cover_letter": "",
            "resume_path": "",
            "resume_hash": "",
            "answers": {},
            "status": new_status,
        })
    save_packages(packages, PACKAGES_PATH)
    load_package_rows.clear()


def render_package_editor(package: dict[str, Any], form_key: str) -> None:
    with st.form(form_key):
        status = st.selectbox(
            "Review status",
            ["draft", "approved", "rejected"],
            index=["draft", "approved", "rejected"].index(package.get("status", "draft"))
            if package.get("status") in {"draft", "approved", "rejected"} else 0,
        )
        email = st.text_input("Contact email", package.get("job", {}).get("email", ""))
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

# Inject keyboard shortcuts
inject_keyboard_shortcuts()

# Initialize session state for auto-refresh
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = CONFIG.get("dashboard", {}).get("auto_refresh", False)
if "refresh_interval" not in st.session_state:
    st.session_state.refresh_interval = CONFIG.get("dashboard", {}).get("refresh_interval", 30)

st.title("Job application workspace")
st.caption("Review packages before applying, then track the application funnel in one place.")

# Auto-refresh: use a fragment with a timer (manual refresh for now, can be enhanced)
if st.session_state.auto_refresh:
    st.warning("Auto-refresh enabled — use the refresh button or press 'r' to reload.")

history = load_history_rows(str(HISTORY_PATH), modified_at(HISTORY_PATH))
packages = load_package_rows(str(PACKAGES_PATH), modified_at(PACKAGES_PATH))

submitted = sum(row.get("status") == "submitted" for row in history)
pending = sum(package.get("status") == "draft" for package in packages)
ready_to_apply = sum(package.get("status") == "approved" for package in packages)
with st.sidebar:
    st.subheader("Settings")
    st.session_state.auto_refresh = st.checkbox("Auto-refresh", value=st.session_state.auto_refresh, help="Automatically refresh the dashboard")
    if st.session_state.auto_refresh:
        st.session_state.refresh_interval = st.slider("Refresh interval (seconds)", 10, 300, st.session_state.refresh_interval, 10)
    st.divider()
    st.subheader("Run JobCrew")
    with st.form("new-search", border=False):
        search_query = st.text_input("Job search query", value=CONFIG.get("search", {}).get("query", "python developer remote"), help="Search query for job search (e.g., 'python')")
        search_location = st.text_input("Location", value=CONFIG.get("search", {}).get("location", "Remote"), help="Target location for job search")
        with st.expander("Advanced options"):
            max_listing_pages = st.number_input(
                "Max listing pages", min_value=1, max_value=50,
                value=int(CONFIG.get("search", {}).get("max_listing_pages", 5)),
                help="Maximum number of listing pages to crawl per search",
            )
            max_pages_per_domain = st.number_input(
                "Max pages per domain", min_value=1, max_value=20,
                value=int(CONFIG.get("search", {}).get("max_pages_per_domain", 2)),
                help="Maximum listing pages crawled per domain",
            )
            dry_run = st.toggle("Dry run", value=False, help="Preview actions without creating packages")
            verbose = st.toggle("Verbose output", value=False, help="Show detailed logs during the search")
        run_search = st.form_submit_button("Create a new review queue", icon=":material/search:", help="Search for jobs and create review packages")
    if run_search:
        log_container = st.container()
        with log_container:
            st.info("🔄 Starting job search...", icon=":material/hourglass_empty:")
        status_text = st.empty()
        status_text.info("Loading resume...")
        arguments = [
            "crew.py", "--search",
            "--query", str(search_query),
            "--location", str(search_location),
            "--max-listing-pages", str(max_listing_pages),
            "--max-pages-per-domain", str(max_pages_per_domain),
        ]
        if verbose:
            arguments.append("--verbose")
        if dry_run:
            arguments.append("--dry-run")
        success, output = run_project_command_streaming(arguments, log_container)
        if success:
            status_text.success("New review queue created.")
        else:
            status_text.error("Job search failed.")
        with st.expander("Command output", expanded=not success):
            st.code(_last_lines(output or "No command output"))
        if success:
            load_package_rows.clear()
            st.rerun()
    with st.form("add-package-form", border=False):
        add_url = st.text_input("Job URL", placeholder="https://...", help="Direct URL to the job posting")
        add_title = st.text_input("Job title", value="Untitled", help="Job title")
        add_company = st.text_input("Company", value="Unknown", help="Company name")
        add_email = st.text_input("Contact email", placeholder="hr@company.com", help="Contact email")
        add_package = st.form_submit_button("Add package", icon=":material/add:", help="Add a single job package from a URL")
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
    if st.button("Generate weekly report", icon=":material/description:", use_container_width=True, help="Generate a weekly metrics report"):
        with st.spinner("Generating weekly report…"):
            success, output = run_project_command(["report_weekly.py"])
        (st.success if success else st.error)(output or "Weekly report command finished.")

attention_rows: list[dict[str, Any]] = []
seen_job_ids: set[str] = set()
for event_index, event in enumerate(history):
    job = event.get("job", event)
    status = event.get("status", "")
    jid = job_id(job)
    if jid in seen_job_ids:
        continue
    reason = None
    if status in {"failed", "error"}:
        reason = "Automation failed — review and retry or complete manually"
    if reason:
        seen_job_ids.add(jid)
        attention_rows.append({
            "reason": reason, "status": status,
            "company": job.get("company", "Unknown"), "title": job.get("title", "Untitled"),
            "email": job.get("email", ""),
            "score": job.get("score") if isinstance(job.get("score"), (int, float)) else None,
            "url": job.get("url", ""), "timestamp": event.get("timestamp", event.get("created_at", "")),
            "job_id": jid,
            "source": "history", "source_index": event_index,
        })

submitted_rows: list[dict[str, Any]] = []
for event in history:
    if event.get("status") != "submitted":
        continue
    job = event.get("job", event)
    when = event_time(event)
    age_days = (datetime.now(timezone.utc) - when).days if when else None
    submitted_rows.append({
        "company": job.get("company", "Unknown"),
        "title": job.get("title", "Untitled"),
        "email": job.get("email", ""),
        "score": job.get("score") if isinstance(job.get("score"), (int, float)) else None,
        "job_id": job_id(job),
        "submitted_at": event.get("timestamp", event.get("created_at", "")),
        "days_since_submission": age_days,
        "url": job.get("url", ""),
    })

TAB_LABELS = ["Needs attention", "Review queue", "Ready to apply", "Submitted", "History"]
STATUS_OPTIONS = sorted(VALID_STATUSES)


def _select_tab(label: str) -> None:
    st.session_state.active_tab = label


if "active_tab" not in st.session_state:
    st.session_state.active_tab = TAB_LABELS[0]

# Clickable metric cards that switch tabs; "History events" opens the History tab
metric_cards: list[tuple[str, int, str]] = [
    ("Needs attention", len(attention_rows), "Needs attention"),
    ("Review queue", pending, "Review queue"),
    ("Ready to apply", ready_to_apply, "Ready to apply"),
    ("Submitted", submitted, "Submitted"),
    ("History events", len(history), "History"),
]
with st.container(horizontal=True):
    for card_label, value, target_tab in metric_cards:
        st.button(
            f"**{value}** {card_label}",
            key=f"metric-{card_label.lower().replace(' ', '-')}",
            icon=":material/warning:" if card_label == "Needs attention"
            else ":material/mark_email_read:" if card_label == "Submitted"
            else ":material/inbox:" if card_label == "Review queue"
            else ":material/play_circle:" if card_label == "Ready to apply"
            else ":material/timeline:",
            on_click=_select_tab,
            args=(target_tab,),
            type="primary" if card_label == st.session_state.active_tab else "secondary",
        )

# Status legend
st.caption("Status legend: " + " | ".join([
    f'<span style="background-color: {color}; color: white; padding: 1px 6px; border-radius: 8px; font-size: 0.7rem;">{status}</span>'
    for status, color in STATUS_COLORS.items() if status in {"draft", "approved", "submitted", "rejected", "failed"}
]), unsafe_allow_html=True)

# Tab bar hidden: the metric cards are the navigation; render only the active section
active_tab = st.session_state.active_tab
if active_tab not in TAB_LABELS:  # stale value from an older session
    active_tab = TAB_LABELS[0]
    st.session_state.active_tab = active_tab

if active_tab == "Needs attention":
    st.caption("Includes failed automation and draft packages awaiting review.")
    if attention_rows:
        # Add status badges to dataframe
        df_attention = pd.DataFrame(attention_rows)[["reason", "status", "company", "title", "email", "score", "job_id", "url", "timestamp"]]
        st.dataframe(
            df_attention,
            hide_index=True,
            column_config={
                "url": st.column_config.LinkColumn("Job listing"),
                "job_id": st.column_config.TextColumn("Job ID"),
                "status": st.column_config.TextColumn("Status", help="Current application status"),
            },
        )
        # Show status badges below
        for row in attention_rows:
            st.markdown(f"{row['company']} — {row['title']} (score: {row.get('score', 'n/a')}): {status_badge(row['status'])}", unsafe_allow_html=True)
        
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
                sync_package_status(item["job_id"], event.get("job", event), status)
                ApplicationHistory(HISTORY_PATH).replace(history)
                load_history_rows.clear()
                st.rerun()
            job_url = item.get("url", "")
            if job_url and confirm_dialog(f"Block domain {job_url}? This will reject the application.", "block_attention"):
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
                sync_package_status(item["job_id"], event.get("job", event), "rejected")
                ApplicationHistory(HISTORY_PATH).replace(history)
                load_history_rows.clear()
                st.rerun()
    else:
        st.success("Nothing needs attention right now.")

elif active_tab == "Submitted":
    st.caption("Applications marked submitted. Days since submission is computed from the submission timestamp.")
    if submitted_rows:
        df_submitted = pd.DataFrame(submitted_rows)
        st.dataframe(
            df_submitted,
            hide_index=True,
            column_config={
                "url": st.column_config.LinkColumn("Job listing"),
                "days_since_submission": st.column_config.NumberColumn("Days since submission", format="%d"),
                "score": st.column_config.NumberColumn("Fit score", format="%.0f"),
                "job_id": st.column_config.TextColumn("Job ID"),
            },
        )
        # Show status badges
        for row in submitted_rows:
            st.markdown(f"{row['company']} — {row['title']} (score: {row.get('score', 'n/a')}): {status_badge('submitted')} — {row['days_since_submission']} days since submission", unsafe_allow_html=True)
    else:
        st.info("No submitted applications have been recorded yet.")

elif active_tab == "Review queue":
    review_packages = [package for package in packages if package.get("status", "draft") == "draft"]
    if not packages:
        st.info("No application packages yet. Run `crew.py` to create a shortlist and tailored review packages.")
    elif not review_packages:
        st.success("All application packages have been reviewed.")
    else:
        # Show status summary
        st.caption(f"Showing {len(review_packages)} draft packages. Press 'a' to approve, 'r' to reject.")
        for idx, package in enumerate(review_packages):
            job = package["job"]
            with st.container(border=True):
                st.markdown(f"### {job.get('title', 'Untitled')} at {job.get('company', 'Unknown')} {status_badge(package.get('status', 'draft'))}", unsafe_allow_html=True)
                st.caption(f"{job.get('location', 'Location not specified')} · fit score: {job.get('score', 'n/a')} · ID: `{package['job_id']}`")
                if job.get("url"):
                    st.link_button("Open job listing", job["url"], icon=":material/open_in_new:")
                st.write(job.get("rationale", "No rationale was saved."))
                current_status = package.get("status", "draft")
                new_review_status = st.selectbox(
                    "Status",
                    STATUS_OPTIONS,
                    index=STATUS_OPTIONS.index(current_status) if current_status in VALID_STATUSES else 0,
                    key=f"review-status-{package['job_id'][:12]}",
                    help="Change the package status (e.g., approve, reject, or withdraw)",
                )
                if new_review_status != current_status:
                    package["status"] = new_review_status
                    save_packages(packages, PACKAGES_PATH)
                    ApplicationHistory(HISTORY_PATH).append({
                        "job": job,
                        "status": new_review_status,
                        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "details": {"note": f"Status changed from {current_status} via dashboard"},
                    })
                    load_package_rows.clear()
                    load_history_rows.clear()
                    st.rerun()

                # Cover letter editor
                cover_letter = st.text_area(
                    "Cover letter (editable)",
                    value=package.get("cover_letter", ""),
                    height=200,
                    key=f"cover_letter_review_{package['job_id'][:12]}",
                    help="Edit the cover letter before approving",
                )
                if cover_letter != package.get("cover_letter", ""):
                    package["cover_letter"] = cover_letter
                    save_packages(packages, PACKAGES_PATH)
                    load_package_rows.clear()
                    st.rerun()
                
                col1, col2, col3 = st.columns([1, 1, 4])
                with col1:
                    if st.button("✅ Approve", key=f"approve-{idx}", type="primary", help="Approve this package for application"):
                        package["status"] = "approved"
                        save_packages(packages, PACKAGES_PATH)
                        load_package_rows.clear()
                        st.rerun()
                with col2:
                    if confirm_dialog(f"Reject '{job.get('title', 'Untitled')}'?", f"reject_review_{idx}"):
                        package["status"] = "rejected"
                        save_packages(packages, PACKAGES_PATH)
                        load_package_rows.clear()
                        st.rerun()
                with col3:
                    if not package.get("cover_letter"):
                        if st.button("Generate cover letter", key=f"gen-cover-{idx}", icon=":material/auto_awesome:", help="Generate a tailored cover letter"):
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
                            help="Automatically submit the application after review",
                        )
                        if st.button("Open in browser", key=f"open-review-{idx}", icon=":material/open_in_new:", help="Open application in browser for manual review"):
                            arguments = [
                                "crew.py", "--apply-existing", "--job-id", package["job_id"],
                                "--playwright", "--review",
                            ]
                            if auto_submit:
                                arguments.append("--auto-submit")
                            success, message = launch_in_terminal(arguments)
                            (st.success if success else st.error)(message)

elif active_tab == "Ready to apply":
    approved_packages = [package for package in packages if package.get("status") == "approved"]
    st.caption("Approved packages ready for the browser application flow. You can still edit or return one to draft.")
    if not approved_packages:
        st.info("No approved packages are ready to apply.")
    else:
        batch_auto_submit = st.checkbox(
            "Auto-submit for batch apply",
            key="batch-auto-submit",
            help="Automatically submit all applications after review",
        )
        configured_cap = CONFIG.get("application", {}).get("max_applications")
        batch_limit = min(int(configured_cap), len(approved_packages)) if configured_cap else len(approved_packages)
        batch_help = (
            f"Apply to up to {batch_limit} approved packages (capped by config application.max_applications)"
            if configured_cap else "Apply to all approved packages"
        )
        if st.button("Apply all approved", type="primary", icon=":material/play_arrow:", key="batch-apply", help=batch_help):
            arguments = [
                "crew.py", "--apply-existing", "--playwright", "--review",
                "--max-applications", str(batch_limit),
            ]
            if batch_auto_submit:
                arguments.append("--auto-submit")
            success, message = launch_in_terminal(arguments)
            (st.success if success else st.error)(message)
        st.divider()
        for idx, package in enumerate(approved_packages):
            job = package["job"]
            with st.container(border=True):
                st.markdown(f"### {job.get('title', 'Untitled')} at {job.get('company', 'Unknown')} {status_badge(package.get('status', 'approved'))}", unsafe_allow_html=True)
                st.caption(f"{job.get('location', 'Location not specified')} · fit score: {job.get('score', 'n/a')} · ID: `{package['job_id']}`")
                if job.get("url"):
                    st.link_button("Open job listing", job["url"], icon=":material/open_in_new:")
                st.write(job.get("rationale", "No rationale was saved."))
                current_status = package.get("status", "approved")
                new_status = st.selectbox(
                    "Status",
                    STATUS_OPTIONS,
                    index=STATUS_OPTIONS.index(current_status) if current_status in VALID_STATUSES else 0,
                    key=f"ready-status-{package['job_id'][:12]}",
                    help="Change the package status (e.g., return to draft or mark withdrawn)",
                )
                if new_status != current_status:
                    package["status"] = new_status
                    save_packages(packages, PACKAGES_PATH)
                    ApplicationHistory(HISTORY_PATH).append({
                        "job": job,
                        "status": new_status,
                        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "details": {"note": f"Status changed from {current_status} via dashboard"},
                    })
                    load_package_rows.clear()
                    load_history_rows.clear()
                    st.rerun()
                with st.expander("Cover letter", expanded=bool(package.get("cover_letter"))):
                    if package.get("cover_letter"):
                        st.write(package["cover_letter"])
                    else:
                        st.caption("No cover letter yet.")
                        if st.button("Generate cover letter", key=f"ready-gen-cover-{idx}", icon=":material/auto_awesome:", help="Generate a tailored cover letter"):
                            with st.spinner("Generating cover letter…"):
                                success, output = run_project_command(["crew.py", "--generate-cover", package["job_id"]])
                            (st.success if success else st.error)(output or "Cover letter command finished.")
                            if success:
                                load_package_rows.clear()
                                st.rerun()
                notes = st.text_area(
                    "Notes",
                    value=package.get("notes", ""),
                    height=100,
                    key=f"ready-notes-{package['job_id'][:12]}",
                    help="Reviewer or application notes (saved automatically)",
                )
                if notes != package.get("notes", ""):
                    package["notes"] = notes
                    save_packages(packages, PACKAGES_PATH)
                    load_package_rows.clear()
                col1, col2, col3, col4, col5 = st.columns([1, 2, 1, 1, 1])
                with col1:
                    auto_submit = st.checkbox(
                        "Auto-submit",
                        key=f"ready-auto-submit-{idx}",
                        help="Automatically submit after review",
                    )
                with col2:
                    if st.button("Open in browser", type="primary", key=f"ready-apply-{idx}", icon=":material/open_in_new:", help="Open application in browser for manual review"):
                        arguments = [
                            "crew.py", "--apply-existing", "--job-id", package["job_id"],
                            "--playwright", "--review",
                        ]
                        if auto_submit:
                            arguments.append("--auto-submit")
                        success, message = launch_in_terminal(arguments)
                        (st.success if success else st.error)(message)
                with col3:
                    if st.button("✅ Submitted", key=f"ready-submitted-{idx}", icon=":material/check_circle:", help="Mark as manually submitted"):
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
                    if confirm_dialog(f"Reject '{job.get('title', 'Untitled')}'?", f"reject_ready_{idx}"):
                        package["status"] = "rejected"
                        save_packages(packages, PACKAGES_PATH)
                        load_package_rows.clear()
                        st.rerun()
                with col5:
                    if job.get("url") and confirm_dialog(f"Block domain for '{job.get('title', 'Untitled')}'?", f"block_ready_{idx}"):
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

elif active_tab == "History":
    if not history:
        st.info("No application events have been recorded yet.")
    else:
        st.subheader("Full history (editable)")
        # Package notes are the latest per-job notes; show them when an event has none
        package_notes = {p.get("job_id"): p.get("notes", "") for p in packages}
        table = pd.DataFrame([
            {
                "timestamp": event.get("timestamp", event.get("created_at", "")),
                "status": event.get("status", ""),
                "company": event.get("job", {}).get("company", event.get("company", "")),
                "title": event.get("job", {}).get("title", event.get("title", "")),
                "email": event.get("job", {}).get("email", event.get("email", "")),
                "score": (
                    event.get("job", {}).get("score")
                    if isinstance(event.get("job", {}).get("score"), (int, float))
                    else None
                ),
                "job_id": job_id(event.get("job", event)),
                "url": event.get("job", {}).get("url", event.get("url", "")),
                "notes": (
                    event.get("details", {}).get("note")
                    or event.get("notes", "")
                    or package_notes.get(job_id(event.get("job", event)), "")
                ),
            }
            for event in history
        ])
        edited = st.data_editor(
            table,
            key="history_editor",
            hide_index=True,
            disabled=["timestamp", "company", "title", "score", "job_id", "url"],
            column_config={
                "status": st.column_config.SelectboxColumn("Status", options=sorted(VALID_STATUSES), help="Application status"),
                "url": st.column_config.LinkColumn("Job listing"),
                "score": st.column_config.NumberColumn("Fit score", format="%.0f"),
                "job_id": st.column_config.TextColumn("Job ID"),
                "notes": st.column_config.TextColumn("Notes", width="large", help="Event note, or the package's latest notes"),
            },
        )
        # Show status badges for quick visual scanning
        st.caption("Status overview:")
        for _, row in table.iterrows():
            st.markdown(f"{row['company']} — {row['title']}: {status_badge(row['status'])}", unsafe_allow_html=True)
        if st.button("Save tracking changes", icon=":material/save:", help="Save all status changes"):
            for index, row in edited.iterrows():
                history[index]["status"] = row["status"]
                history[index].setdefault("job", {})["email"] = row["email"]
                history[index].setdefault("details", {})["note"] = row["notes"]
            ApplicationHistory(HISTORY_PATH).replace(history)
            load_history_rows.clear()
            st.success("Tracking changes saved.")
