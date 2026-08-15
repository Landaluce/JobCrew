# JobCrew

JobCrew is a human-reviewed job-search and application workflow. It finds and ranks roles, creates a tailored application package for each role, and can assist with browser form filling after an explicit review step.

It does not submit applications unless `--auto-submit` is explicitly passed. Review generated content before sharing it with an employer.

## Quick start

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
playwright install chromium
cp .env.example .env  # or create .env with your local LLM/search settings
.venv/bin/python crew.py --resume data/resume.pdf --query "Python developer" --location Remote
```

The default LLM configuration uses Ollama. Set `OLLAMA_MODEL` and optionally `OLLAMA_BASE_URL` in `.env`. To enable web search with CrewAI, configure the provider credentials required by `SerperDevTool`; without them, the workflow can still run with the tools available to CrewAI.

## Workflow

1. Resume text is extracted and cached by SHA-256 hash.
2. Agents search and rank roles using explicit task context.
3. A structured shortlist and `output/application_packages.json` are written. Every package includes the job, rationale, resume hash, notes, and review status.
4. Review and approve packages in the dashboard. Generate a tailored cover letter only after approval, from the **Ready to apply** tab.
5. Optional Playwright assistance uploads the resume and fills a cover letter. It opens a visible browser and does not submit unless `--auto-submit` is specified.
6. Application events are stored in `output/application_history.json`; use the dashboard and weekly report to track them.

## Commands

```bash
# Create reviewable packages (safe default; no browser automation)
.venv/bin/python crew.py --resume data/resume.pdf --query "backend engineer" --location Remote

# Review approved packages in a visible browser, without submitting
.venv/bin/python crew.py --apply --playwright --review

# Submit previously generated packages without running a new search
.venv/bin/python crew.py --apply-existing --playwright --review --auto-submit

# Generate a cover letter for an approved package (the dashboard does this for you)
.venv/bin/python crew.py --generate-cover JOB_ID

# Open the review queue and tracking dashboard
.venv/bin/streamlit run dashboard.py

# Generate summary reports and list follow-up candidates
.venv/bin/python report_weekly.py
.venv/bin/python monitor.py --after-days 7

# Run the test suite
.venv/bin/python -m pytest -q
```

## Data conventions

Job identity is a SHA-256-derived ID based on the canonical job URL (tracking parameters removed), with company/title/location as a fallback. This prevents most duplicate applications when listings are rediscovered through a different tracking URL.

History uses defined lifecycle statuses: `draft`, `approved`, `prepared`, `submitted`, `follow_up`, `interview`, `offer`, `withdrawn`, `rejected`, `failed`, and related legacy statuses. Generated output and local credentials are ignored by Git.

## Project layout

- `src/job_automation/` — reusable resume, identity, package, and history primitives.
- `crew.py` — agent workflow and explicit CLI approval gate.
- `dashboard.py` — Streamlit review queue and editable tracking view.
- `playwright_sites.py` — conservative site handlers; add an adapter per application system.
- `monitor.py` / `report_weekly.py` — follow-up and reporting utilities.
- `tests/` — cache, history, identity, and package tests.
