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

The default LLM configuration uses Ollama. Set `OLLAMA_MODEL` and optionally `OLLAMA_BASE_URL` in `.env`. Job search queries the **Serper** API directly (`SERPER_API_KEY` in `.env` — free tier at serper.dev) so every listing URL is a real Google result; the LLM is only used later for optional fit scores and generated cover letters/tailored resumes.

## Workflow

1. Resume text is extracted and cached by SHA-256 hash.
2. A Serper web search returns real job listing/search-result URLs.
3. A Playwright crawl extracts individual job postings from those pages (skipping dead, parked, and blacklisted ones), optionally scored for fit by the LLM.
4. `output/application_packages.json` is written. Every package includes the job, resume hash, notes, and review status.
5. Review and approve packages in the dashboard. Generate a tailored cover letter only after approval, from the **Ready to apply** tab.
6. Optional Playwright assistance uploads the resume and fills a cover letter. It opens a visible browser and does not submit unless `--auto-submit` is specified.
7. Application events are stored in `output/application_history.json`; use the dashboard and weekly report to track them.

## Commands

```bash
# Show all options
.venv/bin/python crew.py -h

# Create reviewable packages (safe default; no browser automation).
# A bare --resume/--query/--location invocation implies --search (no run-mode flag = search).
.venv/bin/python crew.py --resume data/resume.pdf --query "backend engineer" --location Remote

# Limit listing pages crawled (defaults: 8 total, 2 per domain)
.venv/bin/python crew.py --resume data/resume.pdf --query "backend engineer" --location Remote --max-listing-pages 10 --max-pages-per-domain 3

# Add a single job package manually
.venv/bin/python crew.py --add-package "https://..." --title "Dev" --company "Acme"

# Review approved packages in a visible browser, without submitting
.venv/bin/python crew.py --apply-existing --playwright --review

# Submit previously generated packages without running a new search
.venv/bin/python crew.py --apply-existing --playwright --review --auto-submit

# Generate a cover letter for an approved package (the dashboard does this for you)
.venv/bin/python crew.py --generate-cover JOB_ID

# Generate cover letters for all approved packages that don't have one yet
.venv/bin/python crew.py --generate-cover-all

# Generate a per-job tailored resume for an approved package
.venv/bin/python crew.py --generate-resume JOB_ID

# Lint and type-check
.venv/bin/ruff check .
.venv/bin/mypy src/job_automation crawler.py events.py cli_ui.py monitor.py playwright_sites.py report_weekly.py crew.py applier.py dashboard.py dashboard_app

# Open the review queue and tracking dashboard
.venv/bin/streamlit run dashboard.py

# Generate summary reports for the last 7 days (--days changes the window)
.venv/bin/python report_weekly.py
.venv/bin/python report_weekly.py --days 30

# Report days since submission for submitted applications
.venv/bin/python monitor.py

# Run the test suite
.venv/bin/python -m pytest -q
```

## CLI flags reference (crew.py)

| Flag | Type | Default | Description |
| ------ | ------ | --------- | ------------- |
| `-h`, `--help` | flag | — | Show help and exit |
| `--resume` | path | `data/resume.pdf` | Resume PDF/TXT/MD path |
| `--query` | str | `python developer remote` | Job search query |
| `--location` | str | `Remote` | Target location |
| `--search` | flag | false | Search Serper, crawl listings, create packages (implied when no run-mode flag is given) |
| `--generate-cover JOB_ID` | str | — | Generate cover letter for an approved package |
| `--generate-cover-all` | flag | false | Generate cover letters for all approved packages missing one |
| `--generate-resume JOB_ID` | str | — | Generate a per-job tailored resume for an approved package |
| `--add-package URL` | str | — | Manually add a job package from a URL |
| `--title` | str | `Untitled` | Job title (with `--add-package`) |
| `--company` | str | `Unknown` | Company name (with `--add-package`) |
| `--job-id JOB_ID` | str | — | Apply one specific approved package without prompting |
| `--playwright` | flag | false | Enable browser automation |
| `--auto-submit` | flag | false | Allow automatic submit (opt-in only) |
| `--review` | flag | false | Pause for manual review before submitting |
| `--skip-review` | flag | false | Auto-approve all draft packages (bypass gate) |
| `--full-cycle` | flag | false | Search + auto-approve + apply in one command |
| `--max-applications` | int | `3` | Max approved jobs to apply to |
| `--max-listing-pages` | int | `8` | Max listing pages to crawl per search |
| `--max-pages-per-domain` | int | `2` | Max listing pages per domain |
| `--debug` | flag | false | Extra diagnostics during listing crawl |

**Listing-page crawl**: before opening a browser, each listing page URL is checked with a fast HEAD request. Dead/parked/unreachable pages are skipped and recorded in `output/blacklist.json` so future runs skip them instantly.

## Dashboard tabs

| Tab | Shows |
| ----- | ------- |
| **Needs attention** | Failed applications and draft packages awaiting review |
| **Submitted** | Applications marked submitted |
| **Review queue** | Draft packages to approve/reject |
| **Ready to apply** | Approved packages for browser automation |
| **History** | Full editable history |

## Data conventions

Job identity is a SHA-256-derived ID based on the canonical job URL (tracking parameters removed), with company/title/location as a fallback. This prevents most duplicate applications when listings are rediscovered through a different tracking URL.

History uses defined lifecycle statuses: `draft`, `approved`, `prepared`, `submitted`, `interview`, `offer`, `withdrawn`, `rejected`, `failed`, `skipped_invalid_url`, `error`, and related legacy statuses. Generated output and local credentials are ignored by Git.

## Project layout

- `src/job_automation/` — reusable resume, identity, package, history, listing-selection, and Serper-client primitives.
- `crew.py` — CLI entrypoint: Serper search → crawl → package creation, approval gate, LLM content generation, and `--add-package` for manual entry.
- `crawler.py` — listing-page crawling with pre-crawl liveness checks and blacklist persistence, plus job-URL extraction.
- `applier.py` — the Playwright apply flow (resume upload, cover-letter fill, opt-in submit).
- `cli_ui.py` / `events.py` — terminal UI helpers and history/CSV event logging.
- `dashboard.py` — Streamlit review queue, tracking view, and history management.
- `playwright_sites.py` — conservative site handlers (Greenhouse); add an adapter per application system.
- `monitor.py` — days-since-submission report.
- `report_weekly.py` — weekly metrics (Markdown + PDF).
- `tests/` — cache, history, identity, and package tests.
