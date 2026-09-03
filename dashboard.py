"""JobCrew dashboard: review queue, ready-to-apply, funnel tracking, and history.

Thin entry point: page setup, sidebar actions (search, add package, weekly
report, blacklist manager), and tab dispatch. All per-tab rendering lives in
``dashboard_app/`` and the table-row logic in ``dashboard_app.rows``.
"""

from __future__ import annotations

import streamlit as st

from dashboard_app import common, rows
from dashboard_app.common import (
    CONFIG,
    HISTORY_PATH,
    PACKAGES_PATH,
    blacklist_entries,
    inject_keyboard_shortcuts,
    load_history_rows,
    load_package_rows,
    modified_at,
    run_project_command,
    run_project_command_streaming,
)
from dashboard_app.tabs import attention as attention_tab
from dashboard_app.tabs import history_tab
from dashboard_app.tabs import ready as ready_tab
from dashboard_app.tabs import review as review_tab
from dashboard_app.tabs import submitted as submitted_tab
from job_automation.listings import add_to_blacklist, remove_from_blacklist

TAB_LABELS = ["Needs attention", "Review queue", "Ready to apply", "Submitted", "History"]

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

with st.sidebar:
    st.subheader("Settings")
    st.session_state.auto_refresh = st.checkbox(
        "Auto-refresh",
        value=st.session_state.auto_refresh,
        help="Automatically refresh the workspace data at a fixed interval",
    )
    if st.session_state.auto_refresh:
        st.session_state.refresh_interval = st.slider(
            "Refresh interval (seconds)", 10, 300, st.session_state.refresh_interval, 10
        )
    st.divider()
    st.subheader("Run JobCrew")
    with st.form("new-search", border=False):
        search_query = st.text_input(
            "Job search query",
            value=CONFIG.get("search", {}).get("query", "python developer remote"),
            help="Search query for job search (e.g., 'python')",
        )
        search_location = st.text_input(
            "Location",
            value=CONFIG.get("search", {}).get("location", "Remote"),
            help="Target location for job search",
        )
        with st.expander("Advanced options"):
            max_listing_pages = st.number_input(
                "Max listing pages",
                min_value=1,
                max_value=50,
                value=int(CONFIG.get("search", {}).get("max_listing_pages", 5)),
                help="Maximum number of listing pages to crawl per search",
            )
            max_pages_per_domain = st.number_input(
                "Max pages per domain",
                min_value=1,
                max_value=20,
                value=int(CONFIG.get("search", {}).get("max_pages_per_domain", 2)),
                help="Maximum listing pages crawled per domain",
            )
            dry_run = st.toggle("Dry run", value=False, help="Preview actions without creating packages")
            verbose = st.toggle("Verbose output", value=False, help="Show detailed logs during the search")
        run_search = st.form_submit_button(
            "Create a new review queue", icon=":material/search:", help="Search for jobs and create review packages"
        )
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
            st.code(common._last_lines(output or "No command output"))
        if success:
            load_package_rows.clear()
            st.rerun()
    with st.form("add-package-form", border=False):
        add_url = st.text_input("Job URL", placeholder="https://...", help="Direct URL to the job posting")
        add_title = st.text_input("Job title", value="Untitled", help="Job title")
        add_company = st.text_input("Company", value="Unknown", help="Company name")
        add_package = st.form_submit_button(
            "Add package", icon=":material/add:",
            help="Add a single job package from a URL",
        )
    if add_package and add_url:
        success, output = run_project_command([
            "crew.py", "--add-package", add_url, "--title", add_title,
            "--company", add_company,
        ])
        (st.success if success else st.error)("Package added." if success else "Failed to add package.")
        if success:
            load_package_rows.clear()
            st.rerun()
    st.divider()
    if st.button(
        "Generate weekly report", icon=":material/description:", use_container_width=True,
        help="Generate a weekly metrics report"
    ):
        with st.spinner("Generating weekly report…"):
            success, output = run_project_command(["report_weekly.py"])
        (st.success if success else st.error)(output or "Weekly report command finished.")

    st.divider()
    st.subheader("Blacklist")
    st.caption("Blocked companies block matching packages from new searches.")
    entries = blacklist_entries()
    if entries:
        blocked = st.multiselect(
            "Blocked entries", options=entries, default=[],
            help="Select entries to unblock below",
            key="blacklist-remove-select",
        )
        if blocked and st.button("Unblock selected", key="blacklist-remove", icon=":material/delete:"):
            remaining = remove_from_blacklist(common.BLACKLIST_PATH, blocked)
            if len(remaining) < len(entries):
                removed_count = len(entries) - len(remaining)
                noun = "entry" if removed_count == 1 else "entries"
                st.success(f"Unblocked {removed_count} {noun}.")
                st.rerun()
            else:
                st.warning("Nothing was removed.")
    else:
        st.caption("Nothing blocked yet.")
    with st.form("blacklist-add-form", border=False):
        block_input = st.text_input(
            "Block domain or URL", placeholder="example.com or https://example.com/careers",
            help="Domain entries block the whole host and its subdomains; URL entries block matching URLs",
        )
        add_block = st.form_submit_button("Block", icon=":material/block:")
    if add_block and block_input.strip():
        domain = block_input.strip()
        add_to_blacklist(common.BLACKLIST_PATH, [domain])
        st.success(f"Blocked {domain}.")
        st.rerun()


def _select_tab(label: str) -> None:
    st.session_state.active_tab = label


def render_workspace() -> None:
    history = load_history_rows(str(HISTORY_PATH), modified_at(HISTORY_PATH))
    packages = load_package_rows(str(PACKAGES_PATH), modified_at(PACKAGES_PATH))
    counts = rows.funnel_counts(history, packages)

    if "active_tab" not in st.session_state:
        st.session_state.active_tab = TAB_LABELS[0]
    active_tab = st.session_state.active_tab
    if active_tab not in TAB_LABELS:  # stale value from an older session
        active_tab = TAB_LABELS[0]
        st.session_state.active_tab = active_tab

    # Clickable metric cards that switch tabs; "History events" opens the History tab
    metric_cards: list[tuple[str, int, str]] = [
        ("Needs attention", counts["attention"], "Needs attention"),
        ("Review queue", counts["pending"], "Review queue"),
        ("Ready to apply", counts["ready_to_apply"], "Ready to apply"),
        ("Submitted", counts["submitted"], "Submitted"),
        ("History events", counts["history_events"], "History"),
    ]
    with st.container(horizontal=True):
        for card_label, value, target_tab in metric_cards:
            st.button(
                f"**{value}** {card_label}",
                key=f"metric-{card_label.lower().replace(' ', '-')}",
                icon=":material/warning:" if card_label == "Needs attention"
                else ":material/assignment_turned_in:" if card_label == "Submitted"
                else ":material/inbox:" if card_label == "Review queue"
                else ":material/play_circle:" if card_label == "Ready to apply"
                else ":material/timeline:",
                on_click=_select_tab,
                args=(target_tab,),
                type="primary" if card_label == active_tab else "secondary",
            )

    # Status legend
    st.caption("Status legend: " + " | ".join([
        (
            f'<span style="background-color: {color}; color: white; padding: 1px 6px; '
            f'border-radius: 8px; font-size: 0.7rem;">{status}</span>'
        )
        for status, color in common.STATUS_COLORS.items()
        if status in {"draft", "approved", "submitted", "rejected", "failed"}
    ]), unsafe_allow_html=True)

    # Tab bar hidden: the metric cards are the navigation; render only the active section
    if active_tab == "Needs attention":
        attention_tab.render(history, packages)
    elif active_tab == "Review queue":
        review_tab.render(history, packages)
    elif active_tab == "Ready to apply":
        ready_tab.render(history, packages)
    elif active_tab == "Submitted":
        submitted_tab.render(history, packages)
    elif active_tab == "History":
        history_tab.render(history, packages)


# Only the workspace auto-refreshes; sidebar controls stay outside the fragment.
_workspace = render_workspace
if st.session_state.auto_refresh:
    _workspace = st.fragment(run_every=st.session_state.refresh_interval)(_workspace)
else:
    _workspace = st.fragment()(_workspace)
_workspace()
