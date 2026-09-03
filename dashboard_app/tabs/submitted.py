"""Submitted tab: applications marked submitted, with days-since-submission."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard_app import common
from dashboard_app.rows import submitted_rows


def render(history: list[dict[str, Any]], packages: list[dict[str, Any]]) -> None:
    rows = submitted_rows(history)
    st.caption("Applications marked submitted. Days since submission is computed from the submission timestamp.")
    if not rows:
        st.info("No submitted applications have been recorded yet.")
        return
    df_submitted = pd.DataFrame(rows)
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
    for row in rows:
        st.markdown(
            f"{row['company']} — {row['title']} (score: {row.get('score', 'n/a')}): "
            f"{common.status_badge('submitted')} — {row['days_since_submission']} days since submission",
            unsafe_allow_html=True,
        )
