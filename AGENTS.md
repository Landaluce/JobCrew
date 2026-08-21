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

## Architecture

- `src/job_automation/` — installable library (resume parsing, history, identity, packages). No heavy dependencies; tests live in `tests/`.
- `crew.py` — CLI entrypoint: CrewAI agent workflow, approval gate, Playwright apply flow, and listing-page crawl step.
- `dashboard.py` — Streamlit app with 5 tabs: Needs attention, Submitted, Review queue, Ready to apply, Application tracking.
- `playwright_sites.py` — site-specific form-filling handlers (Greenhouse, Lever). Add new adapters here.
- `monitor.py` — follow-up checker. `report_weekly.py` — weekly metrics (Markdown + PDF).
- `output/` — all generated data (packages, history, metrics, screenshots). Git-ignored.

## Key conventions

- Python 3.10+, no type-checker or linter is currently configured.
- Tests use pytest with `tmp_path` fixtures; no external services required.
- Package status lifecycle: `draft` → `approved` → `prepared` → `submitted`.
- Job IDs are SHA-256 hashes of the canonical URL (tracking params stripped).
- Playwright launches Chrome (`channel="chrome"`) in headed mode with `slow_mo=250`.
- Dashboard runs subprocess calls to `crew.py` for search/apply/cover-letter generation.
- All output under `output/` is git-ignored. `.env` and `.env.*` are also git-ignored.

## Gotchas

- `crew.py` imports `crewai`, `crewai-tools`, `playwright`, `streamlit`, and `pydantic` at the top level — if any are missing, the entire CLI fails at import time.
- The default LLM is local Ollama (`llama3.2:3b`). If Ollama isn't running, CrewAI agent calls will fail.
- The search agent returns listing/search page URLs, not individual job postings. A Playwright crawl step (`crawl_all_listings()`) visits each listing page and extracts individual job posting URLs using URL pattern matching before creating application packages.
- The crawl runs in headless Chrome and scrolls pages to trigger lazy-loaded content. It extracts links matching patterns like `/jobs/view/`, `/job/\d+`, `greenhouse.io/jobs/`, and `lever.co/company/role`.
- `application_packages.json` is both read by the dashboard and written by `crew.py` — never edit it by hand while the dashboard is open.
- History `replace()` writes atomically via a `.tmp` file, but the dashboard reads with a 15s cache (`st.cache_data`).
- The Playwright apply flow uses `input()` for review-mode prompts — it cannot run inside Streamlit directly; it opens a separate terminal via `launch_in_terminal()`.
- Tests only cover the `src/job_automation/` library — no tests exist for `crew.py`, `dashboard.py`, or `playwright_sites.py`.

## Environment

- `.env` is required: set `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, and optionally `SERPER_API_KEY`.
- `playwright install chromium` is needed for browser automation (even though Chrome channel is used, Chromium browsers are still required by Playwright internals).
- The venv is expected at `.venv/`.
