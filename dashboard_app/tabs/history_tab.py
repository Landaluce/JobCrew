"""History tab: full, editable application event stream."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard_app import common
from dashboard_app.rows import history_table_rows
from job_automation.history import VALID_STATUSES


def render(history: list[dict[str, Any]], packages: list[dict[str, Any]]) -> None:
    if not history:
        st.info("No application events have been recorded yet.")
        return
    st.subheader("Full history (editable)")
    table = pd.DataFrame(history_table_rows(history, packages))
    edited = st.data_editor(
        table,
        key="history_editor",
        hide_index=True,
        disabled=["timestamp", "company", "title", "score", "job_id", "url"],
        column_config={
            "status": st.column_config.SelectboxColumn(
                "Status", options=sorted(VALID_STATUSES), help="Application status"
            ),
            "url": st.column_config.LinkColumn("Job listing"),
            "score": st.column_config.NumberColumn("Fit score", format="%.0f"),
            "job_id": st.column_config.TextColumn("Job ID"),
            "notes": st.column_config.TextColumn(
                "Notes", width="large", help="Event note, or the package's latest notes"
            ),
        },
    )
    if st.button("Save tracking changes", icon=":material/save:", help="Save all status changes"):
        for index, row in edited.iterrows():
            history[index]["status"] = row["status"]
            history[index].setdefault("details", {})["note"] = row["notes"]
        common.write_history(history)
        st.success("Tracking changes saved.")
