"""Sidebar environment health panel.

Three quick checks (LLM server reachable, configured resume present, Playwright
installed) turn mid-run failures — a search that dies after minutes because
Ollama is down, an apply terminal that opens and immediately errors — into
instant status visible before clicking anything.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import streamlit as st

from dashboard_app.common import CONFIG
from job_automation.llm import llm_server_online

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def configured_resume_path() -> Path:
    configured: Any = CONFIG.get("resume", {})
    relative = str(configured.get("path", "data/resume.pdf"))
    return PROJECT_ROOT / relative


def resume_available() -> bool:
    return configured_resume_path().exists()


def playwright_installed() -> bool:
    return importlib.util.find_spec("playwright") is not None


def health_checks() -> list[tuple[str, bool, str]]:
    """Return (label, healthy, detail) for each environment check."""
    llm_ok = llm_server_online()
    resume_ok = resume_available()
    pw_ok = playwright_installed()
    return [
        (
            "LLM server (Ollama)",
            llm_ok,
            "reachable" if llm_ok else "start it with `ollama serve`",
        ),
        (
            "Resume file",
            resume_ok,
            "found" if resume_ok else "missing — searches need data/resume.pdf",
        ),
        (
            "Playwright",
            pw_ok,
            "installed" if pw_ok else "missing — apply flow needs `pip install playwright`",
        ),
    ]


def render_health_panel() -> None:
    st.subheader("Environment")
    for label, healthy, detail in health_checks():
        icon = ":material/check_circle:" if healthy else ":material/error:"
        color = "#198754" if healthy else "#dc3545"
        st.markdown(
            f'<span style="color: {color};">{icon}</span> **{label}**'
            f'<span style="color: {color};"> — {detail}</span>',
            unsafe_allow_html=True,
        )
