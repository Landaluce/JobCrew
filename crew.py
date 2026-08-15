import os
import json
import csv
import argparse
import re
# import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from crewai import Agent, Task, Crew, Process, LLM

from job_automation import ApplicationHistory, load_or_parse_resume, job_id
from job_automation.packages import load_packages, save_packages


try:
    from crewai_tools import SerperDevTool
except Exception:
    SerperDevTool = None

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

load_dotenv()

SHORTLIST_JSON = "output/shortlist.json"
PACKAGES_JSON = "output/application_packages.json"
HISTORY_JSON = "output/application_history.json"
HISTORY_CSV = "output/application_history.csv"
RESUME_CACHE = "output/resume_profile.json"

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
    jobs: List[JobItem]


class CoverLetterResult(BaseModel):
    cover_letter: str


def ensure_output_dir():
    os.makedirs("output", exist_ok=True)


# def hash_file(path: str, block_size: int = 65536) -> str:
#     h = hashlib.sha256()
#     with open(path, "rb") as f:
#         for chunk in iter(lambda: f.read(block_size), b""):
#             h.update(chunk)
#     return h.hexdigest()


# def extract_text_from_pdf(path: str) -> str:
#     try:
#         from pypdf import PdfReader
#     except ImportError:
#         raise RuntimeError("Missing dependency: pypdf. Install it with: pip install pypdf")

#     parts = []
#     with open(path, "rb") as f:
#         reader = PdfReader(f)
#         for page in reader.pages:
#             parts.append(page.extract_text() or "")
#     return "\n".join(parts).strip()


# def load_resume(path: str) -> str:
#     if not os.path.exists(path):
#         raise FileNotFoundError(f"Resume file not found: {path}")
#     ext = os.path.splitext(path.lower())[1]
#     if ext == ".pdf":
#         return extract_text_from_pdf(path)
#     if ext in {".txt", ".md", ".rst"}:
#         with open(path, "r", encoding="utf-8") as f:
#             return f.read().strip()
#     raise ValueError(f"Unsupported resume format: {ext}")


# def load_resume_cache():
#     if not os.path.exists(RESUME_CACHE):
#         return None
#     with open(RESUME_CACHE, "r", encoding="utf-8") as f:
#         return json.load(f)


# def save_resume_cache(resume_path: str, profile: Any):
#     ensure_output_dir()
#     payload = {
#         "resume_hash": hash_file(resume_path),
#         "resume_path": resume_path,
#         "profile": profile,
#         "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
#     }
#     with open(RESUME_CACHE, "w", encoding="utf-8") as f:
#         json.dump(payload, f, indent=2, ensure_ascii=False)


def load_history() -> list[dict[str, Any]]:
    return history_store.records()


# def save_history(history: list):
#     ensure_output_dir()
#     with open(HISTORY_JSON, "w", encoding="utf-8") as f:
#         json.dump(history, f, indent=2, ensure_ascii=False)


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
    job: Dict[str, Any],
    details: Optional[Dict[str, Any]] = None,
) -> None:
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "job": job,
        "details": details or {},
    }

    history_store.append(event)
    sync_csv(history_store.records())

def was_already_applied(job: Dict[str, Any]) -> bool:
    identity = job_id(job)

    final_statuses = {
        "submitted",
        "prepared",
        "approved_not_submitted",
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


def save_packages_from_shortlist(shortlist: list[dict], resume_path: str, resume_hash: str) -> list[dict]:
    """Create review packages; cover letters are generated only after approval."""
    packages = [{
        "job_id": job_id(job), "job": job, "cover_letter": "", "answers": {},
        "resume_path": resume_path, "resume_hash": resume_hash, "status": "draft", "notes": "",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    } for job in shortlist]
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
) -> List[dict]:
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
        if was_already_applied(job):
            print("\n=== SKIPPING PREVIOUSLY PROCESSED JOB ===")
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


# def build_crew(resume_text: str, query: str, location: str, llm=None):
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

    # resume_task = Task(
    #     description=f"Parse this resume and extract skills, experience, industries, and keywords.\nResume:\n{resume_text}",
    #     expected_output="Structured candidate profile.",
    #     agent=resume_agent,
    # )
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
        description=f"Find relevant jobs for query '{query}' and location '{location}'. Return title, company, location, url, description.",
        expected_output="List of jobs.",
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


def apply_with_playwright(job: Dict[str, Any], resume_path: str, cover_letter: str, auto_submit: bool = False, review_mode: bool = True):
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
    run_group.add_argument("--apply", action="store_true", help="Enable approval gate and application flow")
    run_group.add_argument(
        "--apply-existing",
        action="store_true",
        help="Apply saved packages without running a new search or overwriting the queue",
    )
    run_group.add_argument("--generate-cover", metavar="JOB_ID", help="Generate a letter for one approved saved package")
    run_group.add_argument("--job-id", help="Apply one specific approved saved package without prompting")
    run_group.add_argument("--playwright", action="store_true", help="Enable browser automation stub")
    run_group.add_argument("--auto-submit", action="store_true", help="Allow automatic submit in Playwright stub")
    run_group.add_argument("--review", action="store_true", help="Pause for manual review before submitting")
    run_group.add_argument("--max-applications", type=int, default=3, help="Maximum number of approved jobs to apply to")

    args = parser.parse_args()

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
    if not args.apply_existing and not args.job_id:
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

        shortlist = save_shortlist_from_result(result)
        save_packages_from_shortlist(shortlist, args.resume, resume_profile.source_hash)
        print(result)
    elif args.apply_existing:
        print(f"Using saved application packages from {PACKAGES_JSON}; no new search will run.")

    if args.job_id:
        approved = [
            package for package in load_packages(PACKAGES_JSON)
            if package.get("job_id") == args.job_id and package.get("status") == "approved"
        ]
        if not approved:
            raise ValueError("The selected package does not exist or is not approved.")
    elif args.apply or args.apply_existing:
        approved = approval_gate(max_applications=args.max_applications)
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
    elif args.apply or args.apply_existing or args.job_id:
        print("No applications were approved. Browser automation will not start.")


if __name__ == "__main__":
    main()
