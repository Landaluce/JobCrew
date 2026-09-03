"""Ready to apply tab: approved packages that can go through the browser flow."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import streamlit as st

from dashboard_app import common
from job_automation.history import VALID_STATUSES


def _status_options() -> list[str]:
    return sorted(VALID_STATUSES)


def _mark_submitted(package: dict[str, Any]) -> None:
    common.ApplicationHistory(common.HISTORY_PATH).append({
        "job": package["job"],
        "status": "submitted",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "details": {"note": "Manually marked as submitted from dashboard"},
    })
    common.load_history_rows.clear()


def _log_status_change(job: dict[str, Any], previous: str, new: str) -> None:
    common.ApplicationHistory(common.HISTORY_PATH).append({
        "job": job,
        "status": new,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "details": {"note": f"Status changed from {previous} via dashboard"},
    })
    common.load_history_rows.clear()


def render(history: list[dict[str, Any]], packages: list[dict[str, Any]]) -> None:
    approved_packages = [package for package in packages if package.get("status") == "approved"]
    st.caption("Approved packages ready for the browser application flow. You can still edit or return one to draft.")
    if not approved_packages:
        st.info("No approved packages are ready to apply.")
        return

    batch_auto_submit = st.checkbox(
        "Auto-submit for batch apply",
        key="batch-auto-submit",
        help="Automatically submit all applications after review",
    )
    configured_cap = common.CONFIG.get("application", {}).get("max_applications")
    batch_limit = min(int(configured_cap), len(approved_packages)) if configured_cap else len(approved_packages)
    batch_help = (
        f"Apply to up to {batch_limit} approved packages (capped by config application.max_applications)"
        if configured_cap else "Apply to all approved packages"
    )
    if st.button(
        "Apply all approved", type="primary", icon=":material/play_arrow:", key="batch-apply", help=batch_help
    ):
        arguments = [
            "crew.py", "--apply-existing", "--playwright", "--review",
            "--max-applications", str(batch_limit),
        ]
        if batch_auto_submit:
            arguments.append("--auto-submit")
        success, message = common.launch_in_terminal(arguments)
        (st.success if success else st.error)(message)

    st.divider()
    status_options = _status_options()
    for idx, package in enumerate(approved_packages):
        job = package["job"]
        with st.container(border=True):
            st.markdown(
                f"### {job.get('title', 'Untitled')} at {job.get('company', 'Unknown')} "
                f"{common.status_badge(package.get('status', 'approved'))}",
                unsafe_allow_html=True,
            )
            st.caption(
                f"{job.get('location', 'Location not specified')} · fit score: "
                f"{job.get('score', 'n/a')} · ID: `{package['job_id']}`"
            )
            if job.get("url"):
                st.link_button("Open job listing", job["url"], icon=":material/open_in_new:")
            st.write(job.get("rationale", "No rationale was saved."))

            current_status = str(package.get("status") or "approved")
            selected_status = st.selectbox(
                "Status",
                status_options,
                index=status_options.index(current_status) if current_status in VALID_STATUSES else 0,
                key=f"ready-status-{package['job_id'][:12]}",
                help="Change the package status (e.g., return to draft or mark withdrawn)",
            )
            new_status = selected_status or current_status
            if new_status != current_status:
                package["status"] = new_status
                common.write_packages(packages)
                _log_status_change(job, current_status, new_status)
                st.rerun()

            with st.expander("Cover letter", expanded=bool(package.get("cover_letter"))):
                if package.get("cover_letter"):
                    st.write(package["cover_letter"])
                else:
                    st.caption("No cover letter yet.")
                    if st.button(
                        "Generate cover letter",
                        key=f"ready-gen-cover-{idx}",
                        icon=":material/auto_awesome:",
                        help="Generate a tailored cover letter",
                    ):
                        with st.spinner("Generating cover letter…"):
                            success, output = common.run_project_command([
                                "crew.py", "--generate-cover", package["job_id"],
                            ])
                        (st.success if success else st.error)(output or "Cover letter command finished.")
                        if success:
                            st.rerun()

            with st.expander("Tailored resume", expanded=bool(package.get("tailored_resume"))):
                tailored_resume = package.get("tailored_resume", "")
                if tailored_resume:
                    st.caption(
                        f"Also saved to `output/tailored_resumes/{package['job_id']}.txt`. "
                        "The original resume file is still what gets uploaded by default — "
                        "copy this text into a new file if you want the browser flow to use it."
                    )
                else:
                    st.caption(
                        "No tailored resume yet. Generate bullet points and a summary "
                        "aligned with this posting (human review required before use)."
                    )
                    if st.button(
                        "Generate tailored resume",
                        key=f"ready-gen-resume-{idx}",
                        icon=":material/auto_awesome:",
                        help="Rewrite resume bullets around this job posting for review",
                    ):
                        with st.spinner("Generating tailored resume…"):
                            success, output = common.run_project_command([
                                "crew.py", "--generate-resume", package["job_id"],
                            ])
                        (st.success if success else st.error)(
                            "Tailored resume generated — review it before use."
                            if success else "Tailored-resume generation failed."
                        )
                        if not success:
                            with st.expander("Command output"):
                                st.code(common._last_lines(output or "No command output"))
                        else:
                            st.rerun()
                edited_resume = st.text_area(
                    "Tailored resume (editable)",
                    value=tailored_resume,
                    height=240,
                    key=f"ready-tailored-{package['job_id'][:12]}",
                    help="Edits are saved to the package and to output/tailored_resumes/<job_id>.txt",
                )
                if edited_resume != tailored_resume:
                    package["tailored_resume"] = edited_resume
                    common.write_packages(packages)
                    _write_tailored_file(package)
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
                common.write_packages(packages)

            col1, col2, col3, col4, col5 = st.columns([1, 2, 1, 1, 1])
            with col1:
                auto_submit = st.checkbox(
                    "Auto-submit",
                    key=f"ready-auto-submit-{idx}",
                    help="Automatically submit after review",
                )
            with col2:
                if st.button(
                    "Open in browser", type="primary", key=f"ready-apply-{idx}",
                    icon=":material/open_in_new:", help="Open application in browser for manual review"
                ):
                    arguments = [
                        "crew.py", "--apply-existing", "--job-id", package["job_id"],
                        "--playwright", "--review",
                    ]
                    if auto_submit:
                        arguments.append("--auto-submit")
                    success, message = common.launch_in_terminal(arguments)
                    (st.success if success else st.error)(message)
            with col3:
                if st.button(
                    "✅ Submitted", key=f"ready-submitted-{idx}",
                    icon=":material/check_circle:", help="Mark as manually submitted"
                ):
                    _mark_submitted(package)
                    package["status"] = "submitted"
                    common.write_packages(packages)
                    st.rerun()
            with col4:
                if common.confirm_dialog(f"Reject '{job.get('title', 'Untitled')}'?", f"reject_ready_{idx}"):
                    package["status"] = "rejected"
                    common.write_packages(packages)
                    st.rerun()
            with col5:
                if job.get("url") and common.confirm_dialog(
                    f"Block domain for '{job.get('title', 'Untitled')}'?", f"block_ready_{idx}"
                ):
                    common.block_domain(job["url"])
                    package["status"] = "rejected"
                    common.write_packages(packages)
                    st.rerun()


def _write_tailored_file(package: dict[str, Any]) -> None:
    """Keep output/tailored_resumes/<job_id>.txt in sync with manual edits."""
    from pathlib import Path

    job = package.get("job", {})
    destination = Path("output/tailored_resumes") / f"{package['job_id']}.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Tailored resume — {job.get('title', 'Untitled')} at {job.get('company', 'Unknown')}\n"
        f"# Job: {job.get('url', '')}\n"
        "# Review before using. The original resume file is still what gets uploaded by default.\n\n"
    )
    destination.write_text(header + (package.get("tailored_resume") or ""), encoding="utf-8")
