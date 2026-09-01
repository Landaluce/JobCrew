import os
import json
import argparse
import re
import sys
import urllib.request
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from crewai import Agent, Task, Crew, Process, LLM

from job_automation import load_config, load_or_parse_resume, job_id
from job_automation.listings import MAX_LISTING_PAGES, MAX_PAGES_PER_DOMAIN
from job_automation.packages import load_packages, save_packages
from cli_ui import Colors, Spinner, log_debug, log_error, log_info, log_success, log_warning
from crawler import crawl_all_listings, is_valid_job_url, verify_url_resolves
from events import ensure_output_dir, history_store, log_event
from applier import apply_with_playwright

CONFIG = load_config()


def get_help_examples():
    return f"""
{Colors.BOLD}Examples:{Colors.RESET}
  {Colors.CYAN}# Search for jobs and create review packages{Colors.RESET}
  python crew.py --search --resume data/resume.pdf --query "python developer" --location Remote

  {Colors.CYAN}# Full cycle: search + auto-approve + apply (opens browser){Colors.RESET}
  python crew.py --full-cycle --resume data/resume.pdf --query "python developer" --location Remote

  {Colors.CYAN}# Apply to existing approved packages with browser review{Colors.RESET}
  python crew.py --apply-existing --playwright --review

  {Colors.CYAN}# Apply without interactive approval (auto-approve all){Colors.RESET}
  python crew.py --apply-existing --playwright --review --skip-review

  {Colors.CYAN}# Auto-submit applications (opt-in only){Colors.RESET}
  python crew.py --apply-existing --playwright --review --auto-submit

  {Colors.CYAN}# Generate cover letter for a specific approved package{Colors.RESET}
  python crew.py --generate-cover JOB_ID

  {Colors.CYAN}# Dry run to preview what would happen{Colors.RESET}
  python crew.py --search --resume data/resume.pdf --query "python developer" --dry-run

  {Colors.CYAN}# Verbose output for debugging{Colors.RESET}
  python crew.py --search --resume data/resume.pdf --query "python developer" --verbose
"""


class CustomHelpFormatter(argparse.HelpFormatter):
    def __init__(self, prog, indent_increment=2, max_help_position=40, width=None):
        super().__init__(prog, indent_increment, max_help_position, width)

    def format_help(self):
        help_text = super().format_help()
        return help_text + get_help_examples()


try:
    from crewai_tools import SerperDevTool
except ImportError:
    SerperDevTool = None

SHORTLIST_JSON = "output/shortlist.json"
PACKAGES_JSON = "output/application_packages.json"
RESUME_CACHE = "output/resume_profile.json"
BLACKLIST_JSON = "output/blacklist.json"


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


def was_already_applied(job: dict[str, Any]) -> bool:
    identity = job_id(job)

    final_statuses = {
        "submitted",
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


def _fetch_page_text(url: str, max_chars: int = 6000) -> str:
    """Fetch a page and return crude visible text for LLM scoring."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        })
        html = urllib.request.urlopen(req, timeout=15, context=ctx).read(200_000).decode("utf-8", errors="ignore")
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()[:max_chars]
    except Exception:
        return ""


def score_job_with_llm(job: dict[str, Any], resume_text: str, llm: Any) -> tuple[float, str] | None:
    """Ask the LLM to score one job's fit (0-100). Returns (score, rationale) or None."""
    page_text = _fetch_page_text(job.get("url", ""))
    parts = [
        f"Title: {job.get('title', '')}",
        f"Company: {job.get('company', '')}",
        f"Location: {job.get('location', '')}",
    ]
    if page_text:
        parts.append(f"Posting excerpt: {page_text}")
    listing = "\n".join(parts)
    if len(listing.strip()) < 40:
        return None
    try:
        reply = str(llm.call(
            f"Candidate resume summary:\n{resume_text[:3000]}\n\n"
            f"Job posting:\n{listing}\n\n"
            "Score this job's fit for the candidate from 0 to 100. "
            "Reply on ONE line starting with the number, then ' - ' and a short reason. "
            "Example: 82 - strong Python match"
        ))
        match = re.match(r"\D*(\d{1,3})\s*(?:[-—:.]\s*)?(.*)", reply.strip())
        if not match:
            return None
        score = float(min(100, max(0, int(match.group(1)))))
        return score, match.group(2).strip()[:300]
    except Exception as exc:
        log_warning(f"LLM scoring failed: {exc}")
        return None


def save_packages_from_shortlist(shortlist: list[dict], resume_path: str, resume_hash: str, verbose: bool = False, verify: bool = True) -> list[dict]:
    """Create review packages; cover letters are generated only after approval."""
    valid_jobs = [job for job in shortlist if is_valid_job_url(job.get("url", ""), BLACKLIST_JSON)]
    if len(valid_jobs) < len(shortlist):
        log_warning(f"Filtered out {len(shortlist) - len(valid_jobs)} jobs with invalid/placeholder URLs.", verbose)
    if verify:
        verified_jobs = [job for job in valid_jobs if verify_url_resolves(job.get("url", ""))]
        if len(verified_jobs) < len(valid_jobs):
            log_warning(f"Filtered out {len(valid_jobs) - len(verified_jobs)} jobs that redirect to parked domains.", verbose)
            # Fallback: if HEAD verification filters all valid jobs (common for search result pages
            # that block HEAD), keep valid jobs so Serper results still become packages.
            if not verified_jobs and valid_jobs:
                log_warning("HEAD verification filtered all jobs — keeping valid URLs as packages.", verbose)
                verified_jobs = valid_jobs
    else:
        verified_jobs = valid_jobs
        log_info("Skipping HEAD verification for search result URLs.", verbose)
    packages = [{
        "job_id": job_id(job), "job": job, "cover_letter": "", "answers": {},
        "resume_path": resume_path, "resume_hash": resume_hash, "status": "draft", "notes": "",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    } for job in verified_jobs]
    if not packages:
        log_warning(
            "No valid jobs produced review packages — "
            "existing application_packages.json left untouched."
        )
        return []
    save_packages(packages, PACKAGES_JSON)
    log_success(f"Saved {len(packages)} review packages to {PACKAGES_JSON}", verbose)
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


def generate_cover_for_saved_package(package_id: str, resume_text: str, verbose: bool = False) -> None:
    packages = load_packages(PACKAGES_JSON)
    package = next((item for item in packages if item.get("job_id") == package_id), None)
    if package is None:
        raise ValueError(f"No package found for job ID: {package_id}")
    if package.get("status") != "approved":
        raise ValueError("Approve a package before generating its cover letter.")
    package["cover_letter"] = generate_cover_letter(package["job"], resume_text, create_llm())
    save_packages(packages, PACKAGES_JSON)
    log_success("Cover letter generated and saved.", verbose)


def approval_gate(
    packages_path: str = PACKAGES_JSON,
    max_applications: int | None = None,
    skip_review: bool = False,
    verbose: bool = False,
) -> list[dict]:
    if not os.path.exists(packages_path):
        log_warning(f"No application packages found at {packages_path}", verbose)
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
        if not is_valid_job_url(job.get("url", ""), BLACKLIST_JSON):
            log_warning(f"\n=== SKIPPING PLACEHOLDER URL ===", verbose)
            log_warning(f"{job.get('title')} @ {job.get('company')}", verbose)
            log_warning(f"URL: {job.get('url')}", verbose)
            continue
        if was_already_applied(job):
            log_warning("\n=== SKIPPING PREVIOUSLY PROCESSED JOB ===", verbose)
            log_warning(f"{job.get('title')} @ {job.get('company')}", verbose)
            log_warning(f"URL: {job.get('url')}", verbose)
            continue

        if skip_review:
            package["status"] = "approved"
            approved.append(package)
            log_event("approved", job, {"approval_turnaround_hours": 0, "package_id": package["job_id"]})
            log_success(f"\n=== AUTO-APPROVED ===", verbose)
            log_success(f"{job.get('title')} @ {job.get('company')}", verbose)
            log_success(f"URL: {job.get('url')}", verbose)
            continue

        log_info("\n=== JOB ===", verbose)
        log_info(f"{job.get('title')} @ {job.get('company')}", verbose)
        log_info(f"Location: {job.get('location')}", verbose)
        log_info(f"Score: {job.get('score')}", verbose)
        log_info(f"URL: {job.get('url')}", verbose)
        try:
            ans = input("Approve this job for application? [y/N]: ").strip().lower()
        except EOFError:
            # No interactive terminal available (e.g. launched from the dashboard);
            # leave the remaining packages as drafts instead of crashing.
            log_warning("No interactive input available — leaving remaining packages as drafts.", verbose)
            break
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


def main():
    parser = argparse.ArgumentParser(
        description="CrewAI job search and application assistant",
        formatter_class=CustomHelpFormatter,
    )

    # Get config defaults
    resume_default = CONFIG.get("resume", {}).get("path", "data/resume.pdf")
    query_default = CONFIG.get("search", {}).get("query", "python developer remote")
    location_default = CONFIG.get("search", {}).get("location", "Remote")
    max_apps_default = CONFIG.get("application", {}).get("max_applications", 3)
    max_listing_pages_default = CONFIG.get("search", {}).get("max_listing_pages", MAX_LISTING_PAGES)
    max_pages_per_domain_default = CONFIG.get("search", {}).get("max_pages_per_domain", MAX_PAGES_PER_DOMAIN)

    input_group = parser.add_argument_group("Input")
    input_group.add_argument("--resume", default=resume_default, help="Path to resume PDF/TXT/MD")
    input_group.add_argument("--query", default=query_default, help="Job search query")
    input_group.add_argument("--location", default=location_default, help="Target job location")

    run_group = parser.add_argument_group("Run Mode")
    run_group.add_argument("--search", action="store_true", help="Run CrewAI search, crawl listings, and create review packages")
    run_group.add_argument(
        "--apply-existing",
        action="store_true",
        help="Apply to existing saved packages (requires --playwright)",
    )
    run_group.add_argument("--generate-cover", metavar="JOB_ID", help="Generate a letter for one approved saved package")
    run_group.add_argument("--add-package", metavar="URL", help="Add a single job package from a URL")
    run_group.add_argument("--title", default="Untitled", help="Job title (used with --add-package)")
    run_group.add_argument("--company", default="Unknown", help="Company name (used with --add-package)")
    run_group.add_argument("--job-id", help="Apply one specific approved saved package without prompting")
    run_group.add_argument("--playwright", action="store_true", help="Enable browser automation")
    run_group.add_argument("--auto-submit", action="store_true", help="Allow automatic submit in Playwright flow")
    run_group.add_argument("--review", action="store_true", help="Pause for manual review before submitting")
    run_group.add_argument("--skip-review", action="store_true", help="Auto-approve all draft packages (skip interactive approval gate)")
    run_group.add_argument("--full-cycle", action="store_true", help="Search + auto-approve + apply in one run")
    run_group.add_argument("--max-applications", type=int, default=max_apps_default, help="Maximum number of approved jobs to apply to")
    run_group.add_argument("--debug", action="store_true", help="Show extra diagnostics during listing crawl")
    run_group.add_argument("--dry-run", action="store_true", help="Preview actions without making changes")
    run_group.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    run_group.add_argument(
        "--max-listing-pages", type=int, default=max_listing_pages_default,
        help="Maximum number of listing pages to crawl per search",
    )
    run_group.add_argument(
        "--max-pages-per-domain", type=int, default=max_pages_per_domain_default,
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

    # Validate required files early
    if args.search or args.full_cycle:
        resume_path = Path(args.resume)
        if not resume_path.exists():
            log_error(f"Resume file not found: {resume_path}")
            log_error("Create a resume file or specify a different path with --resume")
            raise SystemExit(1)

    log_info(f"Resume: {args.resume}", args.verbose)
    log_info(f"Query: {args.query}", args.verbose)
    log_info(f"Location: {args.location}", args.verbose)

    resume_profile = load_or_parse_resume(
        resume_path=Path(args.resume),
        cache_path=Path(RESUME_CACHE),
    )

    log_success(f"Resume source: {resume_profile.source_file}", args.verbose)
    log_success(f"Resume SHA-256: {resume_profile.source_hash}", args.verbose)

    if args.dry_run:
        log_warning("DRY RUN MODE - No changes will be made", args.verbose)

    if args.generate_cover:
        if args.dry_run:
            log_info(f"Would generate cover letter for package: {args.generate_cover}", args.verbose)
            return
        generate_cover_for_saved_package(args.generate_cover, resume_profile.data["text"], args.verbose)
        return

    if args.add_package:
        if args.dry_run:
            log_info(f"Would add package for {args.title} at {args.company} ({args.add_package})", args.verbose)
            return
        job = {"url": args.add_package, "title": args.title, "company": args.company, "location": args.location}
        spinner = Spinner("Scoring job fit...", args.verbose)
        spinner.start()
        scored = score_job_with_llm(job, resume_profile.data["text"], create_llm())
        if scored:
            job["score"], job["rationale"] = scored
            spinner.stop(True, f"Fit score: {job['score']:.0f}")
        else:
            spinner.stop(False, "Scoring unavailable — package saved without score")
        packages = load_packages(PACKAGES_JSON)
        new_package = {
            "job_id": job_id(job), "job": job, "cover_letter": "", "answers": {},
            "resume_path": args.resume, "resume_hash": resume_profile.source_hash,
            "status": "draft", "notes": "",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        packages.append(new_package)
        save_packages(packages, PACKAGES_JSON)
        log_success(f"Added package {new_package['job_id']} for {args.title} at {args.company}")
        return

    if args.search:
        resume_text = resume_profile.data["text"]
        log_info(f"Resume characters loaded: {len(resume_text)}", args.verbose)

        spinner = Spinner("Building CrewAI agents...", args.verbose)
        spinner.start()
        crew = build_crew(
            resume_text=resume_text,
            resume_source=resume_profile.source_file,
            resume_hash=resume_profile.source_hash,
            query=args.query,
            location=args.location,
            llm=create_llm(),
        )
        spinner.stop(True, "CrewAI agents ready")

        if args.dry_run:
            log_info("Would run CrewAI search and create review packages", args.verbose)
            return

        try:
            spinner = Spinner("Running CrewAI search (this may take a while)...", args.verbose)
            spinner.start()
            result = crew.kickoff()
            spinner.stop(True, "CrewAI search complete")
        except Exception as exc:
            spinner.stop(False, "CrewAI search failed")
            log_error(f"CrewAI kickoff failed: {exc}")
            if "Connection refused" in str(exc) or "connect" in str(exc).lower():
                log_error("Is Ollama running? Start it with: ollama serve")
            elif "SerperDevTool" in str(exc):
                log_error("Serper API key missing. Set SERPER_API_KEY in .env")
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
            for match in re.finditer(r'https?://[^\s,)\]"\'">]+', raw):
                url = match.group(0).rstrip(".,;:")
                if url not in seen_urls:
                    seen_urls.add(url)
                    search_results.append({"url": url, "title": "", "company": "", "location": ""})

        log_info(f"Found {len(search_results)} listing page URLs to crawl", args.verbose)

        # Whether to HEAD-verify shortlist URLs (skip for search result fallbacks that block HEAD)
        verify_shortlist = True
        # Crawl listing pages to extract individual job posting URLs
        if search_results:
            crawled_jobs = crawl_all_listings(
                search_results,
                debug=args.debug,
                verbose=args.verbose,
                max_pages=args.max_listing_pages,
                max_per_domain=args.max_pages_per_domain,
                blacklist_path=BLACKLIST_JSON,
            )
            if crawled_jobs:
                shortlist = crawled_jobs
                ensure_output_dir()
                with open(SHORTLIST_JSON, "w", encoding="utf-8") as f:
                    json.dump(shortlist, f, indent=2, ensure_ascii=False)
                log_success(f"\nUsing {len(shortlist)} crawled individual job URLs for packages.")
            else:
                log_warning("\nCrawl returned no individual job URLs. Using search results directly.")
                verify_shortlist = False
                # Try LLM-ranked shortlist first (has titles/company), fallback to search_results URLs
                shortlist = []
                try:
                    llm_shortlist = save_shortlist_from_result(result)
                    valid_llm = [j for j in llm_shortlist if is_valid_job_url(j.get("url", ""), BLACKLIST_JSON)]
                    if valid_llm:
                        shortlist = valid_llm
                        log_success(f"Using {len(shortlist)} LLM-ranked jobs for packages.")
                    else:
                        log_warning("LLM shortlist contained only placeholder/filtered URLs — using Serper URLs.")
                except Exception as exc:
                    log_warning(f"Could not load LLM shortlist ({exc}) — using Serper URLs.")
                if not shortlist:
                    # Build from search_results; enrich with LLM titles where possible via URL map
                    url_map = {}
                    try:
                        for t in result.tasks_output:
                            pj = getattr(t, "pydantic", None)
                            if isinstance(pj, JobList):
                                for j in pj.jobs:
                                    url_map[j.url] = j.model_dump()
                                break
                    except Exception:
                        pass
                    shortlist = []
                    for s in search_results:
                        url = s.get("url", "")
                        if not url:
                            continue
                        enriched = url_map.get(url, {})
                        shortlist.append({
                            "title": enriched.get("title") or s.get("title", "") or "Untitled",
                            "company": enriched.get("company") or s.get("company", "") or "Unknown",
                            "location": enriched.get("location") or s.get("location", ""),
                            "url": url,
                            "score": enriched.get("score", 75.0),
                            "rationale": enriched.get("rationale", ""),
                        })
                    if not shortlist:
                        log_warning("No search result URLs available — falling back to LLM shortlist.")
                        shortlist = save_shortlist_from_result(result)
                    else:
                        ensure_output_dir()
                        with open(SHORTLIST_JSON, "w", encoding="utf-8") as f:
                            json.dump(shortlist, f, indent=2, ensure_ascii=False)
                        log_success(f"Using {len(shortlist)} search result URLs for packages.")
        else:
            log_warning("\nNo search result URLs found to crawl. Falling back to shortlist.")
            shortlist = save_shortlist_from_result(result)

        if args.dry_run:
            log_info(f"Would create {len(shortlist)} review packages", args.verbose)
            return

        save_packages_from_shortlist(shortlist, args.resume, resume_profile.source_hash, args.verbose, verify=verify_shortlist)
        log_success("Review packages created successfully")
    elif args.apply_existing:
        log_info(f"Using saved application packages from {PACKAGES_JSON}; no new search will run.", args.verbose)

    if args.job_id:
        approved = [
            package for package in load_packages(PACKAGES_JSON)
            if package.get("job_id") == args.job_id
            and package.get("status") == "approved"
            and is_valid_job_url(package.get("job", {}).get("url", ""), BLACKLIST_JSON)
        ]
        if not approved:
            raise ValueError("The selected package does not exist or is not approved.")
    elif args.search or args.apply_existing:
        approved = approval_gate(max_applications=args.max_applications, skip_review=args.skip_review, verbose=args.verbose)
    else:
        approved = []

    if approved:
        applied = 0
        failed = 0
        for package in approved:
            job = package["job"]
            if args.playwright:
                if args.dry_run:
                    log_info(f"Would apply to: {job.get('title')} @ {job.get('company')}", args.verbose)
                    continue
                try:
                    apply_with_playwright(
                        job,
                        package.get("resume_path") or args.resume,
                        package["cover_letter"],
                        auto_submit=args.auto_submit,
                        review_mode=args.review,
                        verbose=args.verbose,
                    )
                    applied += 1
                except Exception as exc:
                    # One broken application must not abort the remaining batch;
                    # the failure was already logged to history by the apply flow.
                    failed += 1
                    log_error(f"Skipping to next package after error: {exc}", args.verbose)
            else:
                log_info(f"Skipping {job.get('title', 'Untitled')} — Playwright disabled; approved package left as-is.", args.verbose)
        if args.playwright and not args.dry_run:
            log_info(f"Apply run complete: {applied} processed, {failed} failed, {len(approved) - applied - failed} skipped.")
    elif args.search or args.apply_existing or args.job_id:
        log_warning("No applications were approved. Browser automation will not start.")


if __name__ == "__main__":
    main()
