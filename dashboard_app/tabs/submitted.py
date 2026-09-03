"""Submitted tab: applications marked submitted, with follow-up nudges.

Applications older than ``FOLLOWUP_AFTER_DAYS`` with no logged response get a
\"needs follow-up\" section; each row can log a follow-up event into history
(which also stops the nudge). The table below lists every submission with
days-since-submission.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard_app import common
from dashboard_app.rows import FOLLOWUP_AFTER_DAYS, followup_rows, submitted_rows
from events import log_event


def _log_followup(job: dict[str, Any], job_id_value: str, days: int) -> None:
    log_event(
        "follow-up",
        job,
        {"note": f"Follow-up sent {days} days after submission", "job_id": job_id_value},
    )
    common.load_history_rows.clear()


def render(history: list[dict[str, Any]], packages: list[dict[str, Any]]) -> None:
    st.caption(
        "Applications marked submitted. Days since submission is computed from the submission "
        f"timestamp; applications older than {FOLLOWUP_AFTER_DAYS} days with no logged response "
        "are flagged above."
    )
    due = followup_rows(history)
    if due:
        st.subheader(f"Needs follow-up ({len(due)})")
        st.caption(
            "No response event (interview, offer, rejection, withdrawal, or follow-up) has been "
            "logged since submission. Oldest first."
        )
        for row in due:
            age = row["days_since_submission"]
            col1, col2 = st.columns([6, 2])
            with col1:
                st.markdown(
                    f"**{row['company']} — {row['title']}** "
                    f"(fit {row.get('score', 'n/a')}) — *{age} days ago*"
                    + (f" — [job]({row['url']})" if row.get("url") else "")
                )
            with col2:
                if st.button(
                    "📨 Log follow-up",
                    key=f"followup-{row['job_id'][:12]}",
                    help="Record that you followed up; stops the nudge for this application",
                ):
                    _log_followup(row.get("job", {}), row["job_id"], age)
                    st.success(f"Follow-up logged for {row['company']}.")
                    st.rerun()
        st.divider()

    rows = submitted_rows(history)
    if not rows:
        st.info("No submitted applications have been recorded yet.")
        return
    df_submitted = pd.DataFrame(rows)
    st.dataframe(
        df_submitted,
        hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("Job listing"),
            "days_since_submission": st.column_config.NumberColumn(
                "Days since submission", format="%d"
            ),
            "score": st.column_config.NumberColumn("Fit score", format="%.0f"),
            "job_id": st.column_config.TextColumn("Job ID"),
        },
    )
