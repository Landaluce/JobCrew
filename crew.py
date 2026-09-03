import argparse
import json
import os
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from applier import apply_with_playwright
from cli_ui import Colors, Spinner, log_error, log_info, log_success, log_warning
from crawler import crawl_all_listings, is_valid_job_url, verify_url_resolves
from events import ensure_output_dir, history_store, log_event
from job_automation import (
    job_id,
    load_config,
    load_or_parse_resume,
)
from job_automation.listings import MAX_LISTING_PAGES, MAX_PAGES_PER_DOMAIN
from job_automation.llm import llm_server_online
from job_automation.packages import load_packages, save_packages
from job_automation.resume_render import render_resume_pdf, tailored_pdf_path
from job_automation.serper import serper_search

# crewai/pydantic are heavy and must stay optional at import time: the CLI
# (help text, apply-only runs, dashboard launches) must work even when they
# are not installed. They are imported inside the functions that need them.
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

if load_dotenv is not None:
    load_dotenv()

try:
    from pydantic import BaseModel
except ImportError:  # pragma: no cover
    BaseModel = None  # type: ignore[assignment,misc]

CONFIG = load_config()


class _UnavailableModel:
    """Stand-in for output schemas when pydantic is not installed.

    ``isinstance(anything, _UnavailableModel)`` is always False, so the
    structured-output checks below simply never match and the code falls
    back to its raw-text recovery paths with a clear error.
    """


if BaseModel is not None:

    class CoverLetterResult(BaseModel):
        cover_letter: str

    class TailoredResumeResult(BaseModel):
        tailored_resume: str

else:  # pragma: no cover - exercised only without pydantic installed
    CoverLetterResult = _UnavailableModel  # type: ignore[misc,assignment]
    TailoredResumeResult = _UnavailableModel  # type: ignore[misc,assignment]


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

  {Colors.CYAN}# Generate cover letters for all approved packages missing one{Colors.RESET}
  python crew.py --generate-cover-all

  {Colors.CYAN}# Generate a per-job tailored resume for an approved package{Colors.RESET}
  python crew.py --generate-resume JOB_ID

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


SHORTLIST_JSON = "output/shortlist.json"
PACKAGES_JSON = "output/application_packages.json"
RESUME_CACHE = "output/resume_profile.json"
BLACKLIST_JSON = "output/blacklist.json"
# Cap on review packages created from one crawl (the full crawl is kept in
# shortlist.json) so a big crawl does not flood the review queue.
MAX_QUEUE_PACKAGES = 25


def create_llm() -> Any:
    """Build the configured CrewAI LLM; crewai is imported lazily."""
    from crewai import LLM

    model = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    return LLM(
        model=f"ollama/{model}",
        base_url=base_url,
        temperature=0.2,
    )

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


def default_run_mode(args: argparse.Namespace) -> argparse.Namespace:
    """When no run mode is given, default to a search instead of a silent no-op.

    README/AGENTS.md document the search command without an explicit ``--search``
    flag, so a bare ``--resume/--query/--location`` invocation must search.
    """
    modes = (
        args.search, args.apply_existing, args.generate_cover, args.generate_cover_all,
        args.generate_resume, args.add_package, args.full_cycle, args.job_id,
    )
    if not any(modes):
        args.search = True
    return args


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


def save_packages_from_shortlist(
    shortlist: list[dict],
    resume_path: str,
    resume_hash: str,
    verbose: bool = False,
    verify: bool = True,
) -> list[dict]:
    """Create review packages; cover letters and resumes are tailored only after approval."""
    valid_jobs = [job for job in shortlist if is_valid_job_url(job.get("url", ""), BLACKLIST_JSON)]
    if len(valid_jobs) < len(shortlist):
        log_warning(f"Filtered out {len(shortlist) - len(valid_jobs)} jobs with invalid/placeholder URLs.", verbose)
    if verify:
        verified_jobs = [job for job in valid_jobs if verify_url_resolves(job.get("url", ""))]
        if len(verified_jobs) < len(valid_jobs):
            log_warning(
                f"Filtered out {len(valid_jobs) - len(verified_jobs)} jobs that "
                "redirect to parked domains.",
                verbose,
            )
            # Fallback: if HEAD verification filters all valid jobs (common for search result pages
            # that block HEAD), keep valid jobs so Serper results still become packages.
            if not verified_jobs and valid_jobs:
                log_warning("HEAD verification filtered all jobs — keeping valid URLs as packages.", verbose)
                verified_jobs = valid_jobs
    else:
        verified_jobs = valid_jobs
        log_info("Skipping HEAD verification for search result URLs.", verbose)
    packages = [{
        "job_id": job_id(job), "job": job, "cover_letter": "", "tailored_resume": "", "answers": {},
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


def search_job_listings(
    query: str, location: str, max_results: int = 10, verbose: bool = False
) -> list[dict[str, Any]]:
    """Real job listing/search-result URLs straight from the Serper API.

    This replaces the crewai search agent: local models emit tool calls that
    crewai never executes (the call becomes the agent's final answer), so the
    agent fabricated listing URLs. Serper results are real Google organic
    links; invalid and blacklisted URLs are filtered out here.
    """
    if not query.strip():
        log_error("Search query is empty.", verbose)
        return []
    location_part = (
        location.strip()
        if location and location.strip().lower() != "remote"
        else "remote"
    )
    search_query = f"{query.strip()} {location_part} jobs"
    log_info(f"Serper query: {search_query!r}", verbose)

    results = serper_search(search_query, num=max_results)

    listings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in results:
        url = (item.get("link") or "").strip()
        if not url or url in seen:
            continue
        if not is_valid_job_url(url, BLACKLIST_JSON):
            log_warning(f"Skipping invalid/blacklisted listing URL: {url}", verbose)
            continue
        seen.add(url)
        hostname = urlparse(url).netloc.lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        listings.append({
            "url": url,
            "title": item.get("title") or "Untitled",
            "company": hostname or "Unknown",
            "location": location or "",
            "source": "serper",
        })
    return listings


def _score_crawled_jobs(
    jobs: list[dict[str, Any]], resume_text: str, verbose: bool = False
) -> None:
    """Best-effort per-job LLM fit scores for crawled postings.

    Scoring is bounded so a large crawl (100+ postings) does not stall the
    queue on hundreds of LLM calls; the dashboard sorts by score and the
    approval gate caps applications anyway. Search itself never requires the
    LLM — scoring only runs when Ollama is up and fails gracefully otherwise
    (packages are saved without a score).
    """
    max_to_score = 10
    if not jobs or not llm_server_online():
        return
    scored_target = jobs[:max_to_score]
    llm = create_llm()
    spinner = Spinner(f"Scoring up to {len(scored_target)} jobs with the LLM...", verbose)
    spinner.start()
    scored = 0
    for job in scored_target:
        result = score_job_with_llm(job, resume_text, llm)
        if result:
            job["score"], job["rationale"] = result
            scored += 1
    spinner.stop(True, f"Scored {scored}/{len(scored_target)} jobs")


def _structured_generation(prompt: str, schema: type, llm: Any) -> dict[str, Any]:
    """Run a one-agent structured-generation task and return the pydantic payload.

    crewai is imported lazily so the CLI stays importable without it; the
    callers of this helper only reach it when generation is actually needed.
    """
    from crewai import Agent, Crew, Process, Task

    agent = Agent(
        role="Application Content Generator",
        goal="Write truthful, job-specific application content from resume evidence only.",
        backstory="You never invent qualifications and use only supplied resume evidence.",
        llm=llm,
        verbose=True,
    )
    task = Task(
        description=prompt,
        expected_output="Structured content.",
        agent=agent,
        output_pydantic=schema,
    )
    result = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=True).kickoff()
    parsed = getattr(result, "pydantic", None)
    if not isinstance(parsed, schema):
        raise RuntimeError("Generation did not return structured output.")
    return parsed.model_dump()  # type: ignore[attr-defined]


def generate_cover_letter(job: dict[str, Any], resume_text: str, llm: Any) -> str:
    payload = _structured_generation(
        prompt=(
            "Write a concise cover letter for this job. Use only resume evidence and do not invent facts.\n"
            f"Job: {json.dumps(job, ensure_ascii=False)}\nResume: {resume_text}"
        ),
        schema=CoverLetterResult,
        llm=llm,
    )
    return payload["cover_letter"]


def generate_tailored_resume(job: dict[str, Any], resume_text: str, llm: Any) -> str:
    """Ask the LLM to rewrite the resume's bullets around one posting."""
    payload = _structured_generation(
        prompt=(
            "Rewrite ONLY the work-experience bullet points and summary of the resume so they "
            "align with this job posting. Keep every claim truthful and grounded in the original "
            "resume text: never add skills, titles, or metrics that are not already present. "
            "Return a complete, copy-pasteable tailored resume section (summary + experience bullets).\n"
            f"Job: {json.dumps(job, ensure_ascii=False)}\nResume: {resume_text}"
        ),
        schema=TailoredResumeResult,
        llm=llm,
    )
    return payload["tailored_resume"]


def _approved_package(packages: list[dict], package_id: str, action: str) -> dict:
    package = next((item for item in packages if item.get("job_id") == package_id), None)
    if package is None:
        raise ValueError(f"No package found for job ID: {package_id}")
    if package.get("status") != "approved":
        raise ValueError(f"Approve a package before generating its {action}.")
    return package


def generate_cover_for_saved_package(package_id: str, resume_text: str, verbose: bool = False) -> None:
    packages = load_packages(PACKAGES_JSON)
    package = _approved_package(packages, package_id, "cover letter")
    package["cover_letter"] = generate_cover_letter(package["job"], resume_text, create_llm())
    save_packages(packages, PACKAGES_JSON)
    log_success("Cover letter generated and saved.", verbose)


def generate_covers_for_approved(
    resume_text: str, verbose: bool = False, force: bool = False
) -> int:
    """Generate cover letters for every approved package that lacks one.

    Packages that already have a cover letter (or are not approved) are left
    untouched unless ``force``. Returns the number of letters generated.
    """
    packages = load_packages(PACKAGES_JSON)
    targets = [
        package for package in packages
        if package.get("status") == "approved"
        and (force or not package.get("cover_letter"))
    ]
    if not targets:
        log_info("All approved packages already have cover letters.", verbose)
        return 0
    llm = create_llm()
    spinner = Spinner(f"Generating {len(targets)} cover letters...", verbose)
    spinner.start()
    for package in targets:
        package["cover_letter"] = generate_cover_letter(package["job"], resume_text, llm)
    save_packages(packages, PACKAGES_JSON)
    spinner.stop(True, f"Generated {len(targets)} cover letters")
    log_success(f"Generated {len(targets)} cover letters.", verbose)
    return len(targets)


def generate_resume_for_saved_package(package_id: str, resume_text: str, verbose: bool = False) -> Path:
    """Generate a per-job tailored resume for an approved package.

    The tailored text is stored on the package (``tailored_resume``), written
    to ``output/tailored_resumes/<job_id>.txt`` for review, and rendered to a
    PDF that the apply flow uploads in place of the original resume file.
    """
    packages = load_packages(PACKAGES_JSON)
    package = _approved_package(packages, package_id, "tailored resume")
    job = package["job"]
    package["tailored_resume"] = generate_tailored_resume(job, resume_text, create_llm())
    save_packages(packages, PACKAGES_JSON)

    destination_dir = Path("output/tailored_resumes")
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{package_id}.txt"
    header = (
        f"# Tailored resume — {job.get('title', 'Untitled')} at {job.get('company', 'Unknown')}\n"
        f"# Job: {job.get('url', '')}\n"
        "# Review before using. Once generated, this tailored resume (as a PDF) is what gets uploaded.\n\n"
    )
    destination.write_text(header + package["tailored_resume"], encoding="utf-8")
    log_success(f"Tailored resume saved to {destination}.", verbose)
    return destination


def approval_gate(
    packages_path: str = PACKAGES_JSON,
    max_applications: int | None = None,
    skip_review: bool = False,
    verbose: bool = False,
) -> list[dict]:
    if not os.path.exists(packages_path):
        log_warning(f"No application packages found at {packages_path}", verbose)
        return []

    approved: list[dict] = []
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
                "tailored_resume": "",
                "answers": {},
                "resume_path": "",
                "resume_hash": "",
                "status": "draft",
                "notes": "",
            }
        else:
            job = package["job"]
        if not is_valid_job_url(job.get("url", ""), BLACKLIST_JSON):
            log_warning("\n=== SKIPPING PLACEHOLDER URL ===", verbose)
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
            log_success("\n=== AUTO-APPROVED ===", verbose)
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
    run_group.add_argument(
        "--search", action="store_true",
        help="Run CrewAI search, crawl listings, and create review packages",
    )
    run_group.add_argument(
        "--apply-existing",
        action="store_true",
        help="Apply to existing saved packages (requires --playwright)",
    )
    run_group.add_argument(
        "--generate-cover", metavar="JOB_ID",
        help="Generate a letter for one approved saved package",
    )
    run_group.add_argument(
        "--generate-cover-all",
        action="store_true",
        help="Generate cover letters for all approved packages that do not have one yet",
    )
    run_group.add_argument(
        "--generate-resume", metavar="JOB_ID",
        help="Generate a per-job tailored resume for one approved saved package",
    )
    run_group.add_argument("--add-package", metavar="URL", help="Add a single job package from a URL")
    run_group.add_argument("--title", default="Untitled", help="Job title (used with --add-package)")
    run_group.add_argument("--company", default="Unknown", help="Company name (used with --add-package)")
    run_group.add_argument("--job-id", help="Apply one specific approved saved package without prompting")
    run_group.add_argument("--playwright", action="store_true", help="Enable browser automation")
    run_group.add_argument("--auto-submit", action="store_true", help="Allow automatic submit in Playwright flow")
    run_group.add_argument("--review", action="store_true", help="Pause for manual review before submitting")
    run_group.add_argument(
        "--skip-review", action="store_true",
        help="Auto-approve all draft packages (skip interactive approval gate)",
    )
    run_group.add_argument("--full-cycle", action="store_true", help="Search + auto-approve + apply in one run")
    run_group.add_argument(
        "--max-applications", type=int, default=max_apps_default,
        help="Maximum number of approved jobs to apply to",
    )
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

    args = default_run_mode(args)

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

    # Fail fast when the LLM server is down for content generation instead of
    # burning minutes in a CrewAI kickoff that dies with a connection error.
    # Search is LLM-independent (Serper + crawl) and applies/deep scoring
    # degrade gracefully, so those still work offline.
    if not args.dry_run and (
        args.generate_cover or args.generate_cover_all or args.generate_resume
    ) and not llm_server_online():
        log_error("The LLM server (Ollama) is not reachable.")
        log_error("Check OLLAMA_BASE_URL in .env and start Ollama with: ollama serve")
        raise SystemExit(1)

    if args.dry_run:
        log_warning("DRY RUN MODE - No changes will be made", args.verbose)

    if args.generate_cover:
        if args.dry_run:
            log_info(f"Would generate cover letter for package: {args.generate_cover}", args.verbose)
            return
        generate_cover_for_saved_package(args.generate_cover, resume_profile.data["text"], args.verbose)
        return

    if args.generate_cover_all:
        if args.dry_run:
            log_info("Would generate cover letters for approved packages missing one", args.verbose)
            return
        generate_covers_for_approved(resume_profile.data["text"], args.verbose)
        return

    if args.generate_resume:
        if args.dry_run:
            log_info(f"Would generate tailored resume for package: {args.generate_resume}", args.verbose)
            return
        generate_resume_for_saved_package(args.generate_resume, resume_profile.data["text"], args.verbose)
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
            "job_id": job_id(job), "job": job, "cover_letter": "", "tailored_resume": "", "answers": {},
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

        if args.dry_run:
            log_info(
                "Would search Serper for job listings, crawl them, and create review packages",
                args.verbose,
            )
            return

        # Listing URLs come straight from the Serper API (real Google results),
        # not from an agent: local models can't drive crewai tool calls, so the
        # agent-based search step fabricated URLs. Downstream of this point the
        # pipeline is deterministic and only ever sees real, reachable pages.
        search_results = search_job_listings(
            args.query, args.location, args.max_listing_pages, args.verbose
        )
        if not search_results:
            log_error(
                "Search returned no usable job listing URLs. "
                "Check SERPER_API_KEY in .env and that the query is not blacklisted."
            )
            raise SystemExit(1)
        log_info(f"Found {len(search_results)} real listing page URLs to crawl", args.verbose)

        crawled_jobs = crawl_all_listings(
            search_results,
            debug=args.debug,
            verbose=args.verbose,
            max_pages=args.max_listing_pages,
            max_per_domain=args.max_pages_per_domain,
            blacklist_path=BLACKLIST_JSON,
        )
        verify_shortlist = True
        if crawled_jobs:
            # Persist the full crawl for reference, then trim the review queue
            # to the best-scored subset so a large crawl does not flood it.
            ensure_output_dir()
            with open(SHORTLIST_JSON, "w", encoding="utf-8") as f:
                json.dump(crawled_jobs, f, indent=2, ensure_ascii=False)
            log_success(f"Saved {len(crawled_jobs)} crawled jobs to {SHORTLIST_JSON}", args.verbose)
            _score_crawled_jobs(crawled_jobs, resume_text, args.verbose)
            crawled_jobs.sort(
                key=lambda job: job.get("score") if isinstance(job.get("score"), (int, float)) else 0,
                reverse=True,
            )
            shortlist = crawled_jobs[:MAX_QUEUE_PACKAGES]
            if len(crawled_jobs) > MAX_QUEUE_PACKAGES:
                log_warning(
                    f"Crawl found {len(crawled_jobs)} postings — keeping the top "
                    f"{MAX_QUEUE_PACKAGES} by fit score for the review queue "
                    f"(all of them remain in {SHORTLIST_JSON})."
                )
            log_success(f"Using {len(shortlist)} crawled individual job URLs for packages.")
        else:
            # No individual postings were extracted (JS-heavy/anti-bot board or a
            # search-results page), but the Serper URLs themselves are real and
            # reviewable — package them so the user still gets a queue.
            log_warning(
                "Crawl returned no individual job URLs — packaging the real listing "
                "pages Serper found instead."
            )
            verify_shortlist = False
            shortlist = [{
                "title": s.get("title") or "Untitled",
                "company": s.get("company") or "Unknown",
                "location": s.get("location") or "",
                "url": s["url"],
                "score": 75.0,
                "rationale": "Listing page — the crawler found no individual postings on it.",
            } for s in search_results]
            ensure_output_dir()
            with open(SHORTLIST_JSON, "w", encoding="utf-8") as f:
                json.dump(shortlist, f, indent=2, ensure_ascii=False)
            log_success(f"Saved {len(shortlist)} shortlist entries to {SHORTLIST_JSON}", args.verbose)

        save_packages_from_shortlist(
            shortlist, args.resume, resume_profile.source_hash, args.verbose,
            verify=verify_shortlist,
        )
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
        approved = approval_gate(
            max_applications=args.max_applications,
            skip_review=args.skip_review,
            verbose=args.verbose,
        )
    else:
        approved = []

    if approved:
        applied = 0
        failed = 0
        # Packages are advanced (approved → prepared/submitted) after each
        # successful browser run; snapshot the file so non-approved packages
        # survive the write.
        changed = False
        packages_all = load_packages(PACKAGES_JSON) if (args.playwright and not args.dry_run) else []
        by_job_id = {package.get("job_id"): package for package in packages_all}
        for package in approved:
            job = package["job"]
            if args.playwright:
                if args.dry_run:
                    log_info(f"Would apply to: {job.get('title')} @ {job.get('company')}", args.verbose)
                    continue
                resume_kind = "original"
                upload_path = package.get("resume_path") or args.resume
                if (package.get("tailored_resume") or "").strip():
                    # A reviewed tailored resume replaces the original file for
                    # this run; the PDF is re-rendered fresh so manual edits in
                    # the dashboard are always what gets uploaded.
                    try:
                        tailored_path = tailored_pdf_path(str(package.get("job_id")))
                        render_resume_pdf(package["tailored_resume"], tailored_path)
                        upload_path = str(tailored_path)
                        resume_kind = "tailored"
                        log_info(
                            f"Uploading tailored resume for {job.get('company', 'Unknown')} "
                            f"(PDF: {tailored_path})",
                            args.verbose,
                        )
                    except Exception as exc:
                        log_warning(
                            f"Could not render tailored resume ({exc}) — using the original file.",
                            args.verbose,
                        )
                try:
                    result = apply_with_playwright(
                        job,
                        upload_path,
                        package["cover_letter"],
                        auto_submit=args.auto_submit,
                        review_mode=args.review,
                        resume_kind=resume_kind,
                        verbose=args.verbose,
                    )
                    applied += 1
                    final_status = result.get("status")
                    if final_status in {"prepared", "submitted"}:
                        target = by_job_id.get(package.get("job_id"))
                        if target is not None:
                            target["status"] = final_status
                            target["updated_at"] = (
                                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                            )
                            changed = True
                except Exception as exc:
                    # One broken application must not abort the remaining batch;
                    # the failure was already logged to history by the apply flow.
                    failed += 1
                    log_error(f"Skipping to next package after error: {exc}", args.verbose)
            else:
                log_info(
                    f"Skipping {job.get('title', 'Untitled')} — Playwright disabled; "
                    "approved package left as-is.",
                    args.verbose,
                )
        if changed:
            # Merge so concurrent dashboard edits to other packages survive;
            # per-row last-writer-wins is decided by updated_at.
            save_packages(packages_all, PACKAGES_JSON, merge_existing=True)
        if args.playwright and not args.dry_run:
            skipped = len(approved) - applied - failed
            log_info(f"Apply run complete: {applied} processed, {failed} failed, {skipped} skipped.")
    elif args.search or args.apply_existing or args.job_id:
        log_warning("No applications were approved. Browser automation will not start.")


if __name__ == "__main__":
    main()
