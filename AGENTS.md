# Agents — JobCrew

## Project

AI-powered job-search and application tool using CrewAI agents, Playwright browser automation, and a Streamlit dashboard. All application steps require human review by default — auto-submit is opt-in only.

## Commands

### Run tests
```bash
.venv/bin/python -m pytest -q
```

### Run a single test file
```bash
.venv/bin/python -m pytest tests/test_history.py -q
```

### Lint and type-check
```bash
.venv/bin/ruff check .
.venv/bin/mypy src/job_automation crawler.py events.py cli_ui.py monitor.py playwright_sites.py report_weekly.py crew.py applier.py dashboard.py dashboard_app
```

(CI runs the same three commands: ruff, mypy, pytest.)

### Run the dashboard
```bash
.venv/bin/streamlit run dashboard.py
```

### Search for jobs (creates review packages)
```bash
.venv/bin/python crew.py --resume data/resume.pdf --query "python developer" --location Remote
```

### Apply from saved packages (opens Chrome via Playwright)
```bash
.venv/bin/python crew.py --apply-existing --playwright --review
```

### Apply without interactive approval gate
```bash
.venv/bin/python crew.py --apply-existing --playwright --review --skip-review
```

### Auto-submit (only when explicitly intended)
```bash
.venv/bin/python crew.py --apply-existing --playwright --review --auto-submit
```

### Full cycle: search + approve + apply in one command
```bash
.venv/bin/python crew.py --full-cycle --resume data/resume.pdf --query "python developer" --location Remote
```

### Generate a cover letter for one package
```bash
.venv/bin/python crew.py --generate-cover JOB_ID
```

### Generate a per-job tailored resume for one package
```bash
.venv/bin/python crew.py --generate-resume JOB_ID
```

(Requires an approved package. Output lands in `output/tailored_resumes/<JOB_ID>.txt` and is editable in the dashboard.)

## Architecture

- `src/job_automation/` — installable library (resume parsing, history, identity, package statuses, shortlist JSON recovery, packages/dedupe, listings/blacklist). No heavy dependencies; tests live in `tests/`.
- `crew.py` — CLI entrypoint: CrewAI agent workflow, approval gate, Playwright apply flow, listing-page crawl step, and per-job cover-letter/tailored-resume generation.
- `dashboard.py` — thin Streamlit entry point (sidebar, metric-card navigation, tab dispatch). Per-tab rendering lives in `dashboard_app/` (`common.py` = shared helpers, `rows.py` = pure table-row builders, `tabs/` = one module per tab).
- `playwright_sites.py` — site-specific form-filling handlers (Greenhouse, Lever, Workday, Ashby + generic fallback). Add new adapters here and register them in `pick_handler()`.
- `monitor.py` — days-since-submission report. `report_weekly.py` — weekly metrics over a configurable window (Markdown + PDF).
- `output/` — all generated data (packages, history, metrics, tailored resumes, screenshots). Git-ignored.

## Key conventions

- Python 3.10+; lint with ruff, type-check with mypy (both configured in `pyproject.toml`; run commands above).
- Tests use pytest with `tmp_path` fixtures; no external services required.
- Package status lifecycle: `draft` → `approved` → `prepared` → `submitted`. Rules live in `src/job_automation/statuses.py` (`validate_transition()`); rejected/withdrawn/failed/error are terminal, and any status can return to `draft`.
- Job IDs are SHA-256 hashes of the canonical URL (tracking params stripped).
- Playwright launches Chrome (`channel="chrome"`) in headed mode with `slow_mo=250`.
- Dashboard runs subprocess calls to `crew.py` for search/apply/cover-letter/tailored-resume generation.
- `application_packages.json` entries may carry `tailored_resume` (per-job resume text; also mirrored to `output/tailored_resumes/<job_id>.txt`).
- All output under `output/` is git-ignored. `.env` and `.env.*` are also git-ignored.

## Gotchas

- `crew.py` lazy-imports `crewai`, `crewai-tools`, `pydantic`, and `python-dotenv`, and structured output schemas degrade to placeholders when pydantic is absent — the CLI (help, apply-only runs, dashboard launches) works without them; only the LLM features need them at runtime. `playwright` is also imported defensively (try/except) by `crawler.py`, `applier.py`, and (via `TYPE_CHECKING`) `playwright_sites.py`.
- The default LLM is local Ollama (`llama3.2:3b`). If Ollama isn't running, CrewAI agent calls will fail.
- The search agent returns listing/search page URLs, not individual job postings. A Playwright crawl step (`crawl_all_listings()`) visits each listing page and extracts individual job posting URLs using URL pattern matching before creating application packages.
- The crawl runs in headless Chrome and scrolls pages to trigger lazy-loaded content. It extracts links matching patterns like `/jobs/view/`, `/job/\d+`, `greenhouse.io/jobs/`, and `lever.co/company/role`.
- `application_packages.json` is both read by the dashboard and written by `crew.py` — never edit it by hand while the dashboard is open.
- History `replace()` writes atomically via a `.tmp` file, but the dashboard reads with a 15s cache (`st.cache_data`).
- The Playwright apply flow uses `input()` for review-mode prompts — it cannot run inside Streamlit directly; it opens a separate terminal via `launch_in_terminal()`.
- Tests cover the `src/job_automation/` library plus pure logic extracted from `crawler.py` (URL matching/domain checks), `crew.py` (shortlist recovery), `playwright_sites.py` (handler selection), and `dashboard_app/rows.py` (funnel row builders) — no browser, LLM, or Streamlit runtime needed. Root-level modules are importable from tests via the root `conftest.py`.

## Environment

- `.env` is required: set `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, and optionally `SERPER_API_KEY`.
- `playwright install chromium` is needed for browser automation (even though Chrome channel is used, Chromium browsers are still required by Playwright internals).
- The venv is expected at `.venv/`.
