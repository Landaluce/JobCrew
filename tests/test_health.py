"""Tests for the environment health helpers (dashboard_app.health) and the
stdlib LLM reachability check they build on (job_automation.llm).
"""

from __future__ import annotations

import dashboard_app.health as health
import job_automation
import job_automation.llm as llm


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class TestLlmServerOnline:
    def test_online_on_2xx(self, monkeypatch) -> None:
        def fake_urlopen(request, timeout: float = 2.0):
            assert "/api/tags" in request.full_url
            return _FakeResponse(200)

        monkeypatch.setattr(llm, "urlopen", fake_urlopen)
        assert llm.llm_server_online(base_url="http://localhost:11434") is True

    def test_offline_on_non_2xx(self, monkeypatch) -> None:
        monkeypatch.setattr(llm, "urlopen", lambda request, timeout=2.0: _FakeResponse(503))
        assert llm.llm_server_online() is False

    def test_offline_when_connection_fails(self, monkeypatch) -> None:
        def raise_error(request, timeout: float = 2.0):
            raise OSError("Connection refused")

        monkeypatch.setattr(llm, "urlopen", raise_error)
        assert llm.llm_server_online() is False

    def test_default_base_url_env(self, monkeypatch) -> None:
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://example.test:11434")
        assert llm.ollama_base_url() == "http://example.test:11434"

    def test_base_url_has_no_default_env(self, monkeypatch) -> None:
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        assert llm.ollama_base_url() == llm.DEFAULT_OLLAMA_BASE_URL


class TestHealthChecks:
    def test_health_checks_report_each_component(self, monkeypatch) -> None:
        monkeypatch.setattr(health, "llm_server_online", lambda: True)
        monkeypatch.setattr(health, "resume_available", lambda: False)
        monkeypatch.setattr(health, "playwright_installed", lambda: True)
        checks = dict((label, (ok, detail)) for label, ok, detail in health.health_checks())
        assert checks["LLM server (Ollama)"] == (True, "reachable")
        assert checks["Resume file"][0] is False
        assert checks["Resume file"][1].startswith("missing")
        assert checks["Playwright"] == (True, "installed")

    def test_playwright_installed_reflects_importability(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "dashboard_app.health.importlib.util.find_spec",
            lambda name: None if name == "playwright" else object(),
        )
        assert health.playwright_installed() is False


def test_llm_server_online_is_exported_from_library() -> None:
    assert job_automation.llm.llm_server_online is llm.llm_server_online
