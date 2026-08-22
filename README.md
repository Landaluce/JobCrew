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
7. Email addresses are automatically extracted from job posting pages and Gmail inbox for follow-up automation.

## Commands

```bash
# Show all options
.venv/bin/python crew.py -h

# Create reviewable packages (safe default; no browser automation)
.venv/bin/python crew.py --resume data/resume.pdf --query "backend engineer" --location Remote

# Limit listing pages crawled (defaults: 8 total, 2 per domain)
.venv/bin/python crew.py --resume data/resume.pdf --query "backend engineer" --location Remote --max-listing-pages 10 --max-pages-per-domain 3

# Add a single job package manually
.venv/bin/python crew.py --add-package "https://..." --title "Dev" --company "Acme" --email "hr@acme.com"

# Review approved packages in a visible browser, without submitting
.venv/bin/python crew.py --apply-existing --playwright --review

# Submit previously generated packages without running a new search
.venv/bin/python crew.py --apply-existing --playwright --review --auto-submit

# Generate a cover letter for an approved package (the dashboard does this for you)
.venv/bin/python crew.py --generate-cover JOB_ID

# Open the review queue and tracking dashboard
.venv/bin/streamlit run dashboard.py

# Generate summary reports
.venv/bin/python report_weekly.py

# Follow-up automation
.venv/bin/python monitor.py --after-days 7                    # List follow-up candidates
.venv/bin/python monitor.py --extract-emails                  # Extract emails from URLs + inbox
.venv/bin/python monitor.py --extract-emails --inbox-only     # Inbox search only
.venv/bin/python monitor.py --after-days 7 --send-email      # Auto-send follow-up emails
.venv/bin/python monitor.py --after-days 7 --notify          # Desktop notification
.venv/bin/python monitor.py --after-days 7 --dry-run         # Preview without sending

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
| `--search` | flag | false | Run CrewAI search, crawl listings, create packages |
| `--generate-cover JOB_ID` | str | — | Generate cover letter for an approved package |
| `--add-package URL` | str | — | Manually add a job package from a URL |
| `--title` | str | `Untitled` | Job title (with `--add-package`) |
| `--company` | str | `Unknown` | Company name (with `--add-package`) |
| `--email` | str | `""` | Contact email (with `--add-package`) |
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

## Email and follow-up setup

Add to `.env` for follow-up email automation:

```bash
# SMTP (for sending)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASS=your_app_password
FROM_EMAIL=your_email@gmail.com

# IMAP (for inbox search)
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=your_email@gmail.com
IMAP_PASS=your_app_password
```

For Gmail, generate an [app password](https://myaccount.google.com/apppasswords) (required with 2FA).

Emails are automatically extracted from:

- Job posting pages during crawl (regex scan for `email@domain.com`)
- Gmail inbox search by company name

## Dashboard tabs

| Tab | Shows |
| ----- | ------- |
| **Needs attention** | Failed jobs, follow-ups due, draft packages awaiting review |
| **Submitted** | Applications marked submitted |
| **Review queue** | Draft packages to approve/reject |
| **Ready to apply** | Approved packages for browser automation |
| **History** | Full editable history with email column |

## Data conventions

Job identity is a SHA-256-derived ID based on the canonical job URL (tracking parameters removed), with company/title/location as a fallback. This prevents most duplicate applications when listings are rediscovered through a different tracking URL.

History uses defined lifecycle statuses: `draft`, `approved`, `prepared`, `submitted`, `follow_up`, `interview`, `offer`, `withdrawn`, `rejected`, `failed`, and related legacy statuses. Generated output and local credentials are ignored by Git.

## Project layout

- `src/job_automation/` — reusable resume, identity, package, history, and listing-selection primitives.
- `crew.py` — CLI entrypoint: CrewAI agent workflow, approval gate, listing-page crawl with pre-crawl liveness checks and blacklist persistence, Playwright apply flow.
- `crew.py` — agent workflow, CLI approval gate, and `--add-package` for manual entry.
- `dashboard.py` — Streamlit review queue, tracking view, and email management.
- `playwright_sites.py` — conservative site handlers; add an adapter per application system.
- `monitor.py` — follow-up checker with email extraction, auto-send, and desktop notifications.
- `report_weekly.py` — weekly metrics (Markdown + PDF).
- `tests/` — cache, history, identity, and package tests.
