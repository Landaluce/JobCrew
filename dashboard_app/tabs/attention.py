"""Needs attention tab: failed/error events that require a human decision."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard_app import common
from dashboard_app.rows import attention_rows
from job_automation.history import VALID_STATUSES


def render(history: list[dict[str, Any]], packages: list[dict[str, Any]]) -> None:
    rows = attention_rows(history)
    st.caption("Includes failed automation and draft packages awaiting review.")
    if not rows:
        st.success("Nothing needs attention right now.")
        return

    df_attention = pd.DataFrame(rows)[["reason", "status", "company", "title", "score", "job_id", "url", "timestamp"]]
    st.dataframe(
        df_attention,
        hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("Job listing"),
            "job_id": st.column_config.TextColumn("Job ID"),
            "status": st.column_config.TextColumn("Status", help="Current application status"),
        },
    )
    for row in rows:
        st.markdown(
            f"{row['company']} — {row['title']} (score: {row.get('score', 'n/a')}): "
            f"{common.status_badge(row['status'])}",
            unsafe_allow_html=True,
        )

    selected = st.selectbox(
        "Edit an attention item",
        range(len(rows)),
        index=(
            st.session_state.get("attention_selected", 0)
            if st.session_state.get("attention_selected", 0) < len(rows) else 0
        ),
        format_func=lambda index: f"{rows[index]['company']} — {rows[index]['title']}: {rows[index]['reason']}",
        key="attention_select",
    )
    st.session_state["attention_selected"] = selected
    item = rows[selected]
    if item["source"] != "history":
        return
    event = history[item["source_index"]]
    with st.form(f"attention-history-{item['source_index']}"):
        status = st.selectbox(
            "Application status",
            sorted(VALID_STATUSES),
            index=sorted(VALID_STATUSES).index(event.get("status") or "failed"),
            key=f"attention-status-{item['source_index']}",
        ) or event.get("status") or "failed"
        notes = st.text_area(
            "Notes",
            event.get("details", {}).get("note", ""),
            key=f"attention-notes-{item['source_index']}",
        )
        saved = st.form_submit_button("Save attention item", type="primary", icon=":material/save:")
    if saved:
        event["status"] = status or event.get("status") or "failed"
        event.setdefault("details", {})["note"] = notes
        common.sync_package_status(packages, item["job_id"], event.get("job", event), status)
        common.write_history(history)
        st.rerun()

    job_url = item.get("url", "")
    if job_url and common.confirm_dialog(
        f"Block domain {job_url}? This will reject the application.", "block_attention"
    ):
        common.block_domain(job_url)
        event["status"] = "rejected"
        common.sync_package_status(packages, item["job_id"], event.get("job", event), "rejected")
        common.write_history(history)
        st.rerun()

    if item["status"] in {"failed", "error"} and st.button(
        "Retry application",
        key=f"retry-attention-{item['source_index']}",
        icon=":material/refresh:",
        type="primary",
        help="Reopen this application in the browser for another attempt",
    ):
        package = common.ensure_package(packages, item["job_id"], event.get("job", event))
        package["status"] = "approved"
        common.write_packages(packages)
        success, message = common.launch_in_terminal([
            "crew.py", "--apply-existing", "--job-id", item["job_id"],
            "--playwright", "--review",
        ])
        (st.success if success else st.error)(message)
