"""Review queue tab: draft packages awaiting a decision."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import streamlit as st

from dashboard_app import common
from job_automation.history import VALID_STATUSES
from job_automation.packages import dedupe_packages, find_duplicate_groups


def _status_options() -> list[str]:
    return sorted(VALID_STATUSES)


def _log_status_change(job: dict[str, Any], previous: str, new: str) -> None:
    common.ApplicationHistory(common.HISTORY_PATH).append({
        "job": job,
        "status": new,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "details": {"note": f"Status changed from {previous} via dashboard"},
    })
    common.load_history_rows.clear()


def render(history: list[dict[str, Any]], packages: list[dict[str, Any]]) -> None:
    review_packages = [package for package in packages if package.get("status", "draft") == "draft"]

    duplicate_groups = find_duplicate_groups(packages)
    if duplicate_groups:
        duplicated = sum(len(group) - 1 for group in duplicate_groups)
        st.warning(
            f"{len(duplicate_groups)} job{'' if len(duplicate_groups) == 1 else 's'} appear more than once "
            f"({duplicated} duplicate package{'' if duplicated == 1 else 's'}) — repeated searches keep old "
            "packages for jobs that are still open."
        )
        if st.button(
            "Merge duplicate packages",
            key="dedupe-merge",
            type="primary",
            icon=":material/merge:",
            help="Keep the most advanced copy of each job and remove the rest",
        ):
            merged, removed = dedupe_packages(packages)
            common.write_packages(
                merged,
                removed_ids=[str(package.get("job_id") or "") for package in removed],
            )
            st.success(f"Merged {len(removed)} duplicate package(s). The richest copy of each job was kept.")
            st.rerun()

    if not packages:
        st.info("No application packages yet. Run `crew.py` to create a shortlist and tailored review packages.")
        return
    if not review_packages:
        st.success("All application packages have been reviewed.")
        return

    st.caption(f"Showing {len(review_packages)} draft packages. Press 'a' to approve, 'r' to reject.")
    status_options = _status_options()
    for idx, package in enumerate(review_packages):
        job = package["job"]
        with st.container(border=True):
            st.markdown(
                f"### {job.get('title', 'Untitled')} at {job.get('company', 'Unknown')} "
                f"{common.status_badge(package.get('status', 'draft'))}",
                unsafe_allow_html=True,
            )
            st.caption(
                f"{job.get('location', 'Location not specified')} · fit score: "
                f"{job.get('score', 'n/a')} · ID: `{package['job_id']}`"
            )
            if job.get("url"):
                st.link_button("Open job listing", job["url"], icon=":material/open_in_new:")
            st.write(job.get("rationale", "No rationale was saved."))

            current_status = str(package.get("status") or "draft")
            selected_status = st.selectbox(
                "Status",
                status_options,
                index=status_options.index(current_status) if current_status in VALID_STATUSES else 0,
                key=f"review-status-{package['job_id'][:12]}",
                help="Change the package status (e.g., approve, reject, or withdraw)",
            )
            new_review_status = selected_status or current_status
            if new_review_status != current_status:
                package["status"] = new_review_status
                common.write_packages(packages)
                _log_status_change(job, current_status, new_review_status)
                st.rerun()

            cover_letter = st.text_area(
                "Cover letter (editable)",
                value=package.get("cover_letter", ""),
                height=200,
                key=f"cover_letter_review_{package['job_id'][:12]}",
                help="Edit the cover letter before approving",
            )
            if cover_letter != package.get("cover_letter", ""):
                package["cover_letter"] = cover_letter
                common.write_packages(packages)
                st.rerun()

            col1, col2, col3 = st.columns([1, 1, 4])
            with col1:
                if st.button(
                    "✅ Approve", key=f"approve-{idx}", type="primary",
                    help="Approve this package for application",
                ):
                    package["status"] = "approved"
                    common.write_packages(packages)
                    st.rerun()
            with col2:
                if common.confirm_dialog(f"Reject '{job.get('title', 'Untitled')}'?", f"reject_review_{idx}"):
                    package["status"] = "rejected"
                    common.write_packages(packages)
                    st.rerun()
            with col3:
                if not package.get("cover_letter"):
                    if st.button(
                        "Generate cover letter",
                        key=f"gen-cover-{idx}",
                        icon=":material/auto_awesome:",
                        help="Generate a tailored cover letter",
                    ):
                        package["status"] = "approved"
                        common.write_packages(packages)
                        with st.spinner("Generating a letter for this job…"):
                            success, output = common.run_project_command([
                                "crew.py", "--generate-cover", package["job_id"],
                            ])
                        (st.success if success else st.error)(
                            "Cover letter generated." if success else "Cover-letter generation failed."
                        )
                        if not success:
                            with st.expander("Command output"):
                                st.code(common._last_lines(output or "No command output"))
                        else:
                            st.rerun()
                else:
                    auto_submit = st.checkbox(
                        "Auto-submit",
                        key=f"auto-submit-review-{idx}",
                        help="Automatically submit the application after review",
                    )
                    if st.button(
                        "Open in browser",
                        key=f"open-review-{idx}",
                        icon=":material/open_in_new:",
                        help="Open application in browser for manual review",
                    ):
                        arguments = [
                            "crew.py", "--apply-existing", "--job-id", package["job_id"],
                            "--playwright", "--review",
                        ]
                        if auto_submit:
                            arguments.append("--auto-submit")
                        success, message = common.launch_in_terminal(arguments)
                        (st.success if success else st.error)(message)
