"""Shared helpers for the Streamlit dashboard.

Everything in this module is UI-agnostic plumbing: paths, cached loaders,
status rendering, subprocess runners, and the small write helpers that keep
``application_packages.json`` and ``application_history.json`` in sync.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import streamlit as st

from job_automation import (
    ApplicationHistory,
    add_to_blacklist,
    load_blacklist,
    load_config,
)
from job_automation.packages import load_packages, save_packages

CONFIG = load_config(Path(__file__).resolve().parent.parent)

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

HISTORY_PATH = Path("output/application_history.json")
PACKAGES_PATH = Path("output/application_packages.json")
BLACKLIST_PATH = Path("output/blacklist.json")
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def status_badge(status: str) -> str:
    """Return HTML for a colored status badge."""
    color = STATUS_COLORS.get(status, "#6c757d")
    return (
        f'<span style="background-color: {color}; color: white; padding: 2px 8px; '
        f'border-radius: 12px; font-size: 0.75rem; font-weight: 500;">{status.upper()}</span>'
    )


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
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            output_lines.append(line)
            log_placeholder.code("\n".join(output_lines[-50:]), language="bash")
        process.stdout.close()
        process.wait()

    return process.returncode == 0, "\n".join(output_lines)


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


def inject_keyboard_shortcuts() -> None:
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


def write_packages(
    packages: list[dict[str, Any]],
    removed_ids: list[str] | None = None,
) -> None:
    """Persist packages and drop the Streamlit cache for the next read.

    Saves in merge mode so a concurrent crew.py write (search, apply run) is
    not clobbered: unchanged rows are kept as-is, changed rows resolve
    last-writer-wins, and rows created by the other writer survive. Pass
    ``removed_ids`` for intentional deletions (e.g. dedupe merges).
    """
    save_packages(
        packages, PACKAGES_PATH,
        merge_existing=True,
        removed_ids=removed_ids or (),
    )
    load_package_rows.clear()


def write_history(history: list[dict[str, Any]]) -> None:
    """Persist history records and drop the Streamlit cache for the next read."""
    ApplicationHistory(HISTORY_PATH).replace(history)
    load_history_rows.clear()


def ensure_package(packages: list[dict[str, Any]], job_id: str, job: dict[str, Any]) -> dict[str, Any]:
    """Return an existing package by job ID, or append a fresh draft package."""
    existing = next((p for p in packages if p.get("job_id") == job_id), None)
    if existing is not None:
        return existing
    package = {
        "job_id": job_id,
        "job": job,
        "cover_letter": "",
        "tailored_resume": "",
        "resume_path": "",
        "resume_hash": "",
        "answers": {},
        "status": "draft",
        "notes": "",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    packages.append(package)
    return package


def sync_package_status(
    packages: list[dict[str, Any]], target_job_id: str, job: dict[str, Any], new_status: str
) -> None:
    """Mirror a status change onto the matching package so it lands in the right tab."""
    ensure_package(packages, target_job_id, job)["status"] = new_status
    write_packages(packages)


def block_domain(url: str) -> str:
    """Add a URL's host to the blacklist (bare-domain entry) and return the domain."""
    domain = urlparse(url or "").netloc.lower()
    if domain:
        add_to_blacklist(BLACKLIST_PATH, [domain])
    return domain


def blacklist_entries() -> list[str]:
    return load_blacklist(BLACKLIST_PATH)
