import os
import json
import csv
import argparse
import re
from urllib.parse import urlparse
import urllib.request
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from crewai import Agent, Task, Crew, Process, LLM

from job_automation import ApplicationHistory, load_or_parse_resume, job_id
from job_automation.listings import (
    MAX_LISTING_PAGES,
    MAX_PAGES_PER_DOMAIN,
    KNOWN_JOB_BOARD_DOMAINS,
    PARKED_DOMAIN_MARKERS,
    add_to_blacklist,
    check_listing_url,
    is_blacklisted,
    load_blacklist,
    select_listing_urls,
)
from job_automation.packages import load_packages, save_packages


try:
    from crewai_tools import SerperDevTool
except ImportError:
    SerperDevTool = None

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Patterns that indicate an individual job posting URL
JOB_URL_PATTERNS = [
    r"/jobs?/view/",                         # LinkedIn, generic
    r"/jobs?/\d+",                           # generic /jobs/12345
    r"/job/\d+",                             # generic /job/12345
    r"/jobs/\d+",                            # generic /jobs/12345
    r"/l-job-offer/",                        # Wellfound
    r"/offer/",                              # generic
    r"viewjob[?&]",                          # Indeed
    r"jk=",                                  # Indeed (alternative)
    r"jobview/",                             # Indeed (alt pattern)
    r"greenhouse\.io/jobs/",                 # Greenhouse
    r"lever\.co/[\w-]+/[\w-]+",              # Lever direct posting
    r"boards\.greenhouse\.io/",              # Greenhouse boards
    r"jobs\.lever\.co/",                     # Lever boards
    r"/careers/",                            # generic careers pages (weak, used as fallback)
    r"/open-positions/",                     # generic
    r"/current-openings/",                   # generic
    r"workable\.com/j/",                     # Workable
    r"smartrecruiters\.com/",                # SmartRecruiters
    r"icims\.com/",                          # iCIMS
    r"jobvite\.com/",                        # Jobvite
    r"ashbyhq\.com/@",                       # Ashby
    r"notion\.site/",                        # Notion-hosted boards
    r"linkedin\.com/jobs/view/",             # LinkedIn explicit
    r"indeed\.com/viewjob",                  # Indeed explicit
    r"glassdoor\.com/job-listing/",          # Glassdoor
    r"dice\.com/jobs/detail/",               # Dice
    r"ziprecruiter\.com/jobs/",              # ZipRecruiter
    r"remoteok\.com/remote-jobs/",           # Remote OK
    r"weworkremotely\.com/remote-jobs/",     # We Work Remotely
    r"builtin\.com/jobs/",                   # Built In
    r"simplyhired\.com/search",              # SimplyHired search results
    r"careerbuilder\.com/job/",              # CareerBuilder
    r"monster\.com/job/",                    # Monster
]

# Fallback: keywords in the URL path that suggest a job posting
JOB_URL_KEYWORDS = [
    "/jobs/view/", "/job/", "/position/", "/opening/",
    "/role/", "/vacancy/", "/requisition/", "/posting/",
]

load_dotenv()

_missing = [var for var in ("OLLAMA_MODEL",) if not os.getenv(var)]
if _missing:
    print(f"ERROR: Missing required environment variables: {', '.join(_missing)}")
    print("Set them in .env or export them before running crew.py.")
    raise SystemExit(1)

SHORTLIST_JSON = "output/shortlist.json"
PACKAGES_JSON = "output/application_packages.json"
HISTORY_JSON = "output/application_history.json"
HISTORY_CSV = "output/application_history.csv"
RESUME_CACHE = "output/resume_profile.json"
BLACKLIST_JSON = "output/blacklist.json"

history_store = ApplicationHistory(HISTORY_JSON)

def create_llm() -> LLM:
    model = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    return LLM(
        model=f"ollama/{model}",
        base_url=base_url,
        temperature=0.2,
    )

class JobItem(BaseModel):
    title: str
    company: str
    location: str
    url: str
    score: float
    rationale: str


class JobList(BaseModel):
    jobs: list[JobItem]


class CoverLetterResult(BaseModel):
    cover_letter: str


def ensure_output_dir():
    os.makedirs("output", exist_ok=True)


def load_history() -> list[dict[str, Any]]:
    return history_store.records()


def sync_csv(history: list):
    ensure_output_dir()
    rows = []
    for e in history:
        job = e.get("job", {})
        details = e.get("details", {})
        rows.append({
            "timestamp": e.get("timestamp", ""),
            "status": e.get("status", ""),
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "url": job.get("url", ""),
            "source": job.get("source", ""),
            "score": job.get("score", ""),
            "site": details.get("site", ""),
            "error": details.get("error", ""),
        })
    if rows:
        with open(HISTORY_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)


def log_event(
    status: str,
    job: dict[str, Any],
    details: dict[str, Any] | None = None,
) -> None:
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "job": job,
        "details": details or {},
    }

    history_store.append(event)
    sync_csv(history_store.records())

def was_already_applied(job: dict[str, Any]) -> bool:
    identity = job_id(job)

    final_statuses = {
        "submitted",
        "applied",
        "success",
    }

    for event in history_store.records():
        prior_job = event.get("job", {})

        if (
            job_id(prior_job) == identity
            and event.get("status") in final_statuses
        ):
            return True

    return False


def save_shortlist_from_result(result) -> list[dict]:
    """
    Find the JobList Pydantic output produced by rank_task and save it.

    Do not use result.json here: CrewOutput represents the final task
    (application_task), which currently produces raw text rather than JSON.
    """
    ensure_output_dir()
    shortlist: list[dict] = []

    for task_output in result.tasks_output:
        pydantic_output = getattr(task_output, "pydantic", None)

        if isinstance(pydantic_output, JobList):
            shortlist = [
                job.model_dump()
                for job in pydantic_output.jobs
            ]
            break

    if not shortlist:
        raise RuntimeError(
            "Could not find a structured JobList from rank_task. "
            "Check the Job Ranking Agent output in output/crew_result.txt."
        )

    with open(SHORTLIST_JSON, "w", encoding="utf-8") as f:
        json.dump(shortlist, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(shortlist)} ranked jobs to {SHORTLIST_JSON}")
    return shortlist


def is_valid_job_url(url: str) -> bool:
    """Filter out placeholder/example URLs, parked domains, and blacklisted URLs."""
    if not url:
        return False
    invalid_domains = ["example.com", "example.org", "example.net", "test.com", "localhost"]
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if any(d in domain for d in invalid_domains):
        return False
    if any(marker in domain for marker in PARKED_DOMAIN_MARKERS):
        return False
    blacklist = load_blacklist(BLACKLIST_JSON)
    url_lower = url.lower()
    for entry in blacklist:
        entry_lower = entry.lower()
        if entry_lower in url_lower or entry_lower in domain:
            return False
    return True


def verify_url_resolves(url: str, timeout: int = 10) -> bool:
    """Send a HEAD request to check whether a URL resolves to a real page."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, method="HEAD", headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        })
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        final_url = resp.geturl()
        final_domain = urlparse(final_url).netloc.lower()
        if any(marker in final_domain for marker in PARKED_DOMAIN_MARKERS):
            return False
        return resp.status < 400
    except Exception:
        return False


def extract_email_from_url(url: str, timeout: int = 10) -> str:
    """Fetch a page and extract the first email address found."""
    email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
    skip_domains = {"example.com", "sentry.io", "wixpress.com", "w3.org", "schema.org"}
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        })
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        content = resp.read(50_000).decode("utf-8", errors="ignore")
        for match in email_pattern.finditer(content):
            email = match.group(0).lower()
            domain = email.split("@")[1]
            if not any(skip in domain for skip in skip_domains):
                return email
    except Exception:
        pass
    return ""


def save_packages_from_shortlist(shortlist: list[dict], resume_path: str, resume_hash: str) -> list[dict]:
    """Create review packages; cover letters are generated only after approval."""
    valid_jobs = [job for job in shortlist if is_valid_job_url(job.get("url", ""))]
    if len(valid_jobs) < len(shortlist):
        print(f"Filtered out {len(shortlist) - len(valid_jobs)} jobs with invalid/placeholder URLs.")
    verified_jobs = [job for job in valid_jobs if verify_url_resolves(job.get("url", ""))]
    if len(verified_jobs) < len(valid_jobs):
        print(f"Filtered out {len(valid_jobs) - len(verified_jobs)} jobs that redirect to parked domains.")
    packages = [{
        "job_id": job_id(job), "job": job, "cover_letter": "", "answers": {},
        "resume_path": resume_path, "resume_hash": resume_hash, "status": "draft", "notes": "",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    } for job in verified_jobs]
    save_packages(packages, PACKAGES_JSON)
    print(f"Saved {len(packages)} review packages to {PACKAGES_JSON}")
    return packages


def generate_cover_letter(job: dict[str, Any], resume_text: str, llm: LLM) -> str:
    agent = Agent(
        role="Cover Letter Generator",
        goal="Write a concise, truthful, tailored cover letter.",
        backstory="You never invent qualifications and use only supplied resume evidence.",
        llm=llm,
        verbose=True,
    )
    task = Task(
        description=f"""Write a concise cover letter for this job. Use only resume evidence and do not invent facts.
Job: {json.dumps(job, ensure_ascii=False)}
Resume: {resume_text}""",
        expected_output="A tailored cover letter.",
        agent=agent,
        output_pydantic=CoverLetterResult,
    )
    result = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=True).kickoff()
    parsed = getattr(result, "pydantic", None)
    if not isinstance(parsed, CoverLetterResult):
        raise RuntimeError("Cover-letter generation did not return structured output.")
    return parsed.cover_letter


def generate_cover_for_saved_package(package_id: str, resume_text: str) -> None:
    packages = load_packages(PACKAGES_JSON)
    package = next((item for item in packages if item.get("job_id") == package_id), None)
    if package is None:
        raise ValueError(f"No package found for job ID: {package_id}")
    if package.get("status") != "approved":
        raise ValueError("Approve a package before generating its cover letter.")
    package["cover_letter"] = generate_cover_letter(package["job"], resume_text, create_llm())
    save_packages(packages, PACKAGES_JSON)
    print("Cover letter generated and saved.")


def approval_gate(
    packages_path: str = PACKAGES_JSON,
    max_applications: int | None = None,
    skip_review: bool = False,
) -> list[dict]:
    if not os.path.exists(packages_path):
        print(f"No application packages found at {packages_path}")
        return []

    approved = []
    packages = load_packages(packages_path)
    for package in packages:
        if max_applications is not None and len(approved) >= max_applications:
            break
        # Accept legacy shortlist entries too, so existing output files do not
        # crash the review flow after upgrading to application packages.
        if "job" not in package:
            job = package
            package = {
                "job_id": job_id(job),
                "job": job,
                "cover_letter": "",
                "answers": {},
                "resume_path": "",
                "resume_hash": "",
                "status": "draft",
                "notes": "",
            }
        else:
            job = package["job"]
        if not is_valid_job_url(job.get("url", "")):
            print(f"\n=== SKIPPING PLACEHOLDER URL ===")
            print(f"{job.get('title')} @ {job.get('company')}")
            print(f"URL: {job.get('url')}")
            continue
        if was_already_applied(job):
            print("\n=== SKIPPING PREVIOUSLY PROCESSED JOB ===")
            print(f"{job.get('title')} @ {job.get('company')}")
            print(f"URL: {job.get('url')}")
            continue

        if skip_review:
            package["status"] = "approved"
            approved.append(package)
            log_event("approved", job, {"approval_turnaround_hours": 0, "package_id": package["job_id"]})
            print(f"\n=== AUTO-APPROVED ===")
            print(f"{job.get('title')} @ {job.get('company')}")
            print(f"URL: {job.get('url')}")
            continue

        print("\n=== JOB ===")
        print(f"{job.get('title')} @ {job.get('company')}")
        print(f"Location: {job.get('location')}")
        print(f"Score: {job.get('score')}")
        print(f"URL: {job.get('url')}")
        ans = input("Approve this job for application? [y/N]: ").strip().lower()
        if ans == "y":
            package["status"] = "approved"
            approved.append(package)
            log_event("approved", job, {"approval_turnaround_hours": 0, "package_id": package["job_id"]})
    save_packages(packages, packages_path)
    return approved


def build_crew(
    resume_text: str,
    resume_source: str,
    resume_hash: str, query: str,
    location: str,
    llm=None,):

    tools = []
    if SerperDevTool is not None:
        tools.append(SerperDevTool())

    resume_agent = Agent(
        role="Resume Parser",
        goal="Extract structured candidate profile from the resume.",
        backstory="You identify skills, experience, roles, and keywords from resumes.",
        llm=llm,
        verbose=True,
    )

    search_agent = Agent(
        role="Job Search Agent",
        goal="Find relevant jobs based on the candidate profile and search query.",
        backstory="You search for roles that match the candidate's background and preferences.",
        tools=tools,
        llm=llm,
        verbose=True,
    )

    rank_agent = Agent(
        role="Job Ranking Agent",
        goal="Score jobs against the resume and produce a shortlist.",
        backstory="You compare role requirements with resume evidence and rank opportunities carefully.",
        llm=llm,
        verbose=True,
    )

    resume_task = Task(
    description=f"""
    Analyze the resume text below and extract a structured candidate profile.

    Return skills, work experience, industries, education, target roles,
    seniority, location preferences, and job-search keywords.

    Resume source: {resume_source}
    Resume SHA-256: {resume_hash}

    Resume text:
    {resume_text}
    """,
        expected_output="Structured candidate profile.",
        agent=resume_agent,
    )
    search_task = Task(
        description=f"Find relevant job listing pages for query '{query}' and location '{location}'. Return listing page URLs (search result pages from job boards like LinkedIn, Indeed, etc.), title, company, and location for each listing found.",
        expected_output="List of job listing page URLs.",
        agent=search_agent,
    )

    rank_task = Task(
        description="""
Rank the jobs and return JSON with this structure:
{
  "jobs": [
    {
      "title": "...",
      "company": "...",
      "location": "...",
      "url": "...",
      "score": 0,
      "rationale": "..."
    }
  ]
}
Score each job from 0 to 100 by fit.
""",
        expected_output="JSON shortlist.",
        agent=rank_agent,
        context=[resume_task, search_task],
        output_pydantic=JobList,
    )

    return Crew(
        agents=[resume_agent, search_agent, rank_agent],
        tasks=[resume_task, search_task, rank_task],
        process=Process.sequential,
        verbose=True,
    )


def crawl_listing_page(url: str, debug: bool = False) -> list[dict[str, str]]:
    """Visit a listing page with Playwright and extract individual job posting URLs."""
    if not PLAYWRIGHT_AVAILABLE:
        print(f"Playwright not available, skipping crawl of {url}")
        return []

    job_links: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})

            print(f"\nCrawling listing page: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as exc:
                print(f"  Failed to load {url}: {exc}")
                browser.close()
                return []

            page.wait_for_timeout(3_000)

            # Scroll down to trigger lazy-loaded content
            for _ in range(3):
                page.evaluate("window.scrollBy(0, 800)")
                page.wait_for_timeout(1_000)

            all_found_urls: list[str] = []
            links = page.locator("a[href]").all()
            for link in links:
                try:
                    href = link.get_attribute("href", timeout=2000)
                except Exception:
                    continue
                if not href:
                    continue

                # Skip non-http links
                if not href.startswith(("http://", "https://", "/")):
                    continue

                # Resolve relative URLs
                if href.startswith("/"):
                    parsed_url = urlparse(url)
                    href = f"{parsed_url.scheme}://{parsed_url.netloc}{href}"

                # Strip fragments
                if "#" in href:
                    href = href.split("#")[0]

                if href in seen_urls:
                    continue

                seen_urls.add(href)
                all_found_urls.append(href)

                # Skip tracking/utility links
                if any(skip in href.lower() for skip in ["/login", "/signup", "/auth", "/help", "javascript:", "/about", "/contact", "/privacy", "/terms", "/cookie"]):
                    continue

                # Check if URL matches a job posting pattern
                is_job_url = any(re.search(pat, href, re.IGNORECASE) for pat in JOB_URL_PATTERNS)

                # Fallback: check for job-related keywords in URL path
                if not is_job_url:
                    path = urlparse(href).path.lower()
                    is_job_url = any(kw in path for kw in JOB_URL_KEYWORDS)

                if not is_job_url:
                    continue

                # Domain sanity check: reject URLs from parked/suspicious domains
                # and cross-domain links that aren't from known job boards
                href_domain = urlparse(href).netloc.lower()
                listing_domain = urlparse(url).netloc.lower().lstrip("www.")
                href_domain_stripped = href_domain.lstrip("www.")
                is_same_domain = href_domain_stripped == listing_domain or listing_domain in href_domain_stripped or href_domain_stripped in listing_domain
                is_known_job_board = any(kb in href_domain for kb in KNOWN_JOB_BOARD_DOMAINS)
                is_parked = any(marker in href_domain for marker in PARKED_DOMAIN_MARKERS)
                if is_parked:
                    continue
                if not is_same_domain and not is_known_job_board:
                    continue

                # Try to get the link text as the job title
                title = ""
                try:
                    title = link.inner_text(timeout=1000).strip()
                except Exception:
                    pass
                if not title or len(title) > 200:
                    title = href.split("/")[-1] or href.split("/")[-2] or href

                job_links.append({"title": title, "url": href})

            browser.close()

    except Exception as exc:
        print(f"  Crawl error for {url}: {exc}")

    # Diagnostic output
    print(f"  Found {len(job_links)} individual job URLs from {url}")
    if not job_links and debug:
        print(f"  [debug] Total links on page: {len(all_found_urls)}")
        if all_found_urls:
            print(f"  [debug] First 10 URLs found:")
            for u in all_found_urls[:10]:
                print(f"    {u}")
        else:
            print(f"  [debug] No links found — page may have failed to render or is JS-heavy")

    return job_links


def crawl_all_listings(
    search_results: list[dict[str, Any]],
    debug: bool = False,
    max_pages: int = MAX_LISTING_PAGES,
    max_per_domain: int = MAX_PAGES_PER_DOMAIN,
) -> list[dict[str, Any]]:
    """Crawl listing page URLs from search results and return individual job postings."""
    if not PLAYWRIGHT_AVAILABLE:
        print("Playwright not available, skipping listing crawl.")
        return []

    all_jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    selected = select_listing_urls(search_results, max_pages=max_pages, max_per_domain=max_per_domain)
    total_urls = len({item.get("url", "").strip() for item in search_results if item.get("url", "").strip()})
    skipped_by_caps = total_urls - len(selected)
    print(f"\n=== CRAWLING {len(selected)} LISTING PAGES ({skipped_by_caps} skipped by caps/domain limits) ===")

    pages_with_results = 0
    pages_empty = 0
    pages_skipped = 0
    for item in selected:
        listing_url = item.get("url", "")
        if is_blacklisted(BLACKLIST_JSON, listing_url):
            print(f"\nSkipping blacklisted listing page: {listing_url}")
            pages_skipped += 1
            continue

        ok, reason = check_listing_url(listing_url)
        if not ok:
            print(f"\nSkipping unreachable listing page ({reason}): {listing_url}")
            add_to_blacklist(BLACKLIST_JSON, [listing_url])
            pages_skipped += 1
            continue

        extracted = crawl_listing_page(listing_url, debug=debug)
        if extracted:
            pages_with_results += 1
        else:
            pages_empty += 1
        for job in extracted:
            if job["url"] not in seen_urls:
                seen_urls.add(job["url"])
                all_jobs.append({
                    "title": job["title"],
                    "company": "",
                    "location": "",
                    "url": job["url"],
                })

    print(f"\n=== CRAWL COMPLETE: {len(all_jobs)} unique job URLs found ===")
    print(f"  Pages with results: {pages_with_results}/{len(selected)}")
    if pages_skipped:
        print(f"  Pages skipped (blacklisted/unreachable): {pages_skipped}/{len(selected)}")
    if pages_empty:
        print(f"  Pages with no results: {pages_empty}/{len(selected)}")
        if pages_empty + pages_skipped == len(selected):
            print("  All pages returned zero job URLs. Possible causes:")
            print("    - Page failed to render (JS-heavy / anti-bot)")
            print("    - URL patterns don't match this job board")
            print("    - Page is a search results page (not a listing page)")
            print("  Tip: run with --debug to see raw URLs found on each page")
    print()

    # Post-crawl verification: HEAD-request each URL to filter parked domains, extract emails
    if all_jobs:
        print(f"=== VERIFYING {len(all_jobs)} URLs (HEAD request + email extraction) ===")
        verified_jobs: list[dict[str, Any]] = []
        rejected = 0
        for job in all_jobs:
            if verify_url_resolves(job["url"]):
                email = extract_email_from_url(job["url"])
                if email:
                    job["email"] = email
                verified_jobs.append(job)
            else:
                rejected += 1
                if debug:
                    print(f"  Rejected (unreachable/parked): {job['url']}")
        all_jobs = verified_jobs
        print(f"  Verified: {len(all_jobs)} | Rejected: {rejected}")

    return all_jobs


def apply_with_playwright(job: dict[str, Any], resume_path: str, cover_letter: str, auto_submit: bool = False, review_mode: bool = True):
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("Playwright not installed. Run: pip install playwright && playwright install")

    details = {"site": "unknown", "steps": [], "submitted": False}
    try:
        from playwright_sites import pick_handler
    except Exception:
        pick_handler = None

    application_url = str(job.get("url") or "").strip()

    if not application_url.startswith(("https://", "http://")):
        error_message = (
            "Cannot open application: this job has no valid application URL. "
            f"Received: {application_url!r}"
        )

        log_event(
            "skipped_invalid_url",
            job,
            {"error": error_message},
        )

        raise ValueError(error_message)

    job = {**job, "url": application_url}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                slow_mo=250,
                args=["--start-maximized"],
            )

            context = browser.new_context(
                viewport={"width": 1440, "height": 1000},
            )

            page = context.new_page()
            page.bring_to_front()

            print(f"\nOpening application URL:\n{job['url']}\n")

            response = page.goto(
                job["url"],
                wait_until="domcontentloaded",
                timeout=60_000,
            )

            page.wait_for_timeout(3_000)

            print(f"Page title: {page.title()}")
            print(f"Current URL: {page.url}")
            print(f"HTTP status: {response.status if response else 'unknown'}")

            details["steps"].append("opened_url")

            handler = pick_handler(job["url"]) if pick_handler else None
            if handler:
                handler(page, job, resume_path, cover_letter)
                details["steps"].append("handler_executed")
            else:
                if page.locator('input[type="file"]').count():
                    page.locator('input[type="file"]').first.set_input_files(resume_path)
                    details["steps"].append("resume_uploaded")
                if page.locator('textarea').count():
                    page.locator('textarea').first.fill(cover_letter)
                    details["steps"].append("cover_letter_filled")

            page.screenshot(path="output/review.png", full_page=True)

            manual_status = "prepared"
            if review_mode:
                details["steps"].append("manual_review")
                choice = input(
                    "\nReview the form in the browser before replying here. "
                    "Type [s] if you submitted it manually, [p] (or Enter) to save it as prepared, "
                    "or [c] to cancel: "
                ).strip().lower()
                if choice == "s":
                    manual_status = "submitted"
                elif choice == "c":
                    details["steps"].append("review_cancelled")
                    browser.close()
                    return {**details, "cancelled": True}
                elif choice not in {"", "p"}:
                    print("Unrecognised choice; saving as prepared.")

            if auto_submit and manual_status != "submitted":
                # Never click a generic `button[type=submit]`: job boards often
                # include hidden search forms (for example, LinkedIn's search bar).
                # Only a visible control with an explicit final-submit label is
                # eligible for automatic submission.
                submit_buttons = page.get_by_role(
                    "button",
                    name=re.compile(r"^\s*(submit application|submit)\s*$", re.IGNORECASE),
                )
                submit_inputs = page.locator('input[type="submit"]:visible')
                submitted = False
                submit_error = None

                for controls in (submit_buttons, submit_inputs):
                    for index in range(controls.count()):
                        control = controls.nth(index)
                        if not control.is_visible() or not control.is_enabled():
                            continue
                        try:
                            control.click(timeout=10_000)
                            submitted = True
                            break
                        except Exception as exc:
                            submit_error = str(exc)
                    if submitted:
                        break

                if submitted:
                    details["steps"].append("submitted")
                    details["submitted"] = True
                    status = "submitted"
                else:
                    details["submit_error"] = submit_error or (
                        "No visible final Submit or Submit application control was found. "
                        "Finish the submission manually in the open browser."
                    )
                    details["steps"].append("manual_submission_required")
                    status = "prepared"
            else:
                status = manual_status

            browser.close()

        log_event(status, job, details)
        return details
    except Exception as e:
        details["error"] = str(e)
        log_event("failed", job, details)
        raise


def main():
    parser = argparse.ArgumentParser(
        description="CrewAI job search and application assistant",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    input_group = parser.add_argument_group("Input")
    input_group.add_argument("--resume", default="data/resume.pdf", help="Path to resume PDF/TXT/MD")
    input_group.add_argument("--query", default="python developer remote", help="Job search query")
    input_group.add_argument("--location", default="Remote", help="Target job location")

    run_group = parser.add_argument_group("Run Mode")
    run_group.add_argument("--search", action="store_true", help="Run CrewAI search, crawl listings, and create review packages")
    run_group.add_argument(
        "--apply-existing",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    run_group.add_argument("--generate-cover", metavar="JOB_ID", help="Generate a letter for one approved saved package")
    run_group.add_argument("--add-package", metavar="URL", help="Add a single job package from a URL")
    run_group.add_argument("--title", default="Untitled", help="Job title (used with --add-package)")
    run_group.add_argument("--company", default="Unknown", help="Company name (used with --add-package)")
    run_group.add_argument("--email", default="", help="Contact email (used with --add-package)")
    run_group.add_argument("--job-id", help="Apply one specific approved saved package without prompting")
    run_group.add_argument("--playwright", action="store_true", help="Enable browser automation")
    run_group.add_argument("--auto-submit", action="store_true", help="Allow automatic submit in Playwright flow")
    run_group.add_argument("--review", action="store_true", help="Pause for manual review before submitting")
    run_group.add_argument("--skip-review", action="store_true", help="Auto-approve all draft packages (skip interactive approval gate)")
    run_group.add_argument("--full-cycle", action="store_true", help="Search + auto-approve + apply in one run")
    run_group.add_argument("--max-applications", type=int, default=3, help="Maximum number of approved jobs to apply to")
    run_group.add_argument("--debug", action="store_true", help="Show extra diagnostics during listing crawl")
    run_group.add_argument(
        "--max-listing-pages", type=int, default=MAX_LISTING_PAGES,
        help="Maximum number of listing pages to crawl per search",
    )
    run_group.add_argument(
        "--max-pages-per-domain", type=int, default=MAX_PAGES_PER_DOMAIN,
        help="Maximum listing pages crawled per domain",
    )

    args = parser.parse_args()

    if args.full_cycle:
        args.search = True
        args.skip_review = True
        args.apply_existing = True
        if not args.playwright:
            args.playwright = True
        if not args.review:
            args.review = True

    ensure_output_dir()

    resume_profile = load_or_parse_resume(
        resume_path=Path(args.resume),
        cache_path=Path(RESUME_CACHE),
    )

    print(f"Resume source: {resume_profile.source_file}")
    print(f"Resume SHA-256: {resume_profile.source_hash}")
    if args.generate_cover:
        generate_cover_for_saved_package(args.generate_cover, resume_profile.data["text"])
        return
    if args.add_package:
        job = {"url": args.add_package, "title": args.title, "company": args.company, "location": args.location, "email": args.email}
        packages = load_packages(PACKAGES_JSON)
        new_package = {
            "job_id": job_id(job), "job": job, "cover_letter": "", "answers": {},
            "resume_path": args.resume, "resume_hash": resume_profile.source_hash,
            "status": "draft", "notes": "",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        packages.append(new_package)
        save_packages(packages, PACKAGES_JSON)
        print(f"Added package {new_package['job_id']} for {args.title} at {args.company}")
        return
    if args.search:
        resume_text = resume_profile.data["text"]
        print(f"Resume characters loaded: {len(resume_text)}")

        crew = build_crew(
            resume_text=resume_text,
            resume_source=resume_profile.source_file,
            resume_hash=resume_profile.source_hash,
            query=args.query,
            location=args.location,
            llm=create_llm(),
        )

        try:
            result = crew.kickoff()
        except Exception as exc:
            print("\n=== CREW KICKOFF FAILED ===")
            print(f"Error type: {type(exc).__name__}")
            print(f"Error: {exc}")
            raise

        with open("output/crew_result.txt", "w", encoding="utf-8") as f:
            f.write(str(result))

        # Extract search results (listing page URLs) from crew output
        search_results = []
        seen_urls: set[str] = set()
        for task_output in result.tasks_output:
            raw = getattr(task_output, "raw", "")
            if not isinstance(raw, str):
                continue
            for match in re.finditer(r'https?://[^\s,)\]"\'>]+', raw):
                url = match.group(0).rstrip(".,;:")
                if url not in seen_urls:
                    seen_urls.add(url)
                    search_results.append({"url": url, "title": "", "company": "", "location": ""})

        # Crawl listing pages to extract individual job posting URLs
        if search_results:
            crawled_jobs = crawl_all_listings(
                search_results,
                debug=args.debug,
                max_pages=args.max_listing_pages,
                max_per_domain=args.max_pages_per_domain,
            )
            if crawled_jobs:
                shortlist = crawled_jobs
                ensure_output_dir()
                with open(SHORTLIST_JSON, "w", encoding="utf-8") as f:
                    json.dump(shortlist, f, indent=2, ensure_ascii=False)
                print(f"\nUsing {len(shortlist)} crawled individual job URLs for packages.")
            else:
                print("\nCrawl returned no individual job URLs. Falling back to search results.")
                shortlist = save_shortlist_from_result(result)
        else:
            print("\nNo search result URLs found to crawl. Falling back to shortlist.")
            shortlist = save_shortlist_from_result(result)

        save_packages_from_shortlist(shortlist, args.resume, resume_profile.source_hash)
        print(result)
    elif args.apply_existing:
        print(f"Using saved application packages from {PACKAGES_JSON}; no new search will run.")

    if args.job_id:
        approved = [
            package for package in load_packages(PACKAGES_JSON)
            if package.get("job_id") == args.job_id
            and package.get("status") == "approved"
            and is_valid_job_url(package.get("job", {}).get("url", ""))
        ]
        if not approved:
            raise ValueError("The selected package does not exist or is not approved.")
    elif args.search or args.apply_existing:
        approved = approval_gate(max_applications=args.max_applications, skip_review=args.skip_review)
    else:
        approved = []

    if approved:
        for package in approved:
            job = package["job"]
            if args.playwright:
                apply_with_playwright(
                    job,
                    package.get("resume_path") or args.resume,
                    package["cover_letter"],
                    auto_submit=args.auto_submit,
                    review_mode=args.review,
                )
            else:
                log_event("approved_not_submitted", job, {"note": "Playwright disabled", "package_id": package["job_id"]})
    elif args.search or args.apply_existing or args.job_id:
        print("No applications were approved. Browser automation will not start.")


if __name__ == "__main__":
    main()
