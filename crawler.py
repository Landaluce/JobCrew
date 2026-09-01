"""Listing-page crawling and job-URL extraction for JobCrew."""

from __future__ import annotations

import re
import ssl
import urllib.request
from typing import Any
from urllib.parse import urlparse

from cli_ui import log_debug, log_info, log_warning
from job_automation.listings import (
    KNOWN_JOB_BOARD_DOMAINS,
    MAX_LISTING_PAGES,
    MAX_PAGES_PER_DOMAIN,
    PARKED_DOMAIN_MARKERS,
    add_to_blacklist,
    check_listing_url,
    domain_of,
    is_blacklisted,
    select_listing_urls,
)

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
    # Search result pages (used as fallback when crawl extracts from search pages)
    r"indeed\.com/jobs\?",                   # Indeed search results
    r"linkedin\.com/jobs/search",            # LinkedIn job search
    r"salesforce\.com/jobs/search",          # Salesforce search
    r"amazon\.jobs/.*search",                # Amazon search
    r"tesla\.com/jobs/search",               # Tesla search
]

# Fallback: keywords in the URL path that suggest a job posting
JOB_URL_KEYWORDS = [
    "/jobs/view/", "/job/", "/position/", "/opening/",
    "/role/", "/vacancy/", "/requisition/", "/posting/",
    "/jobs?", "/search", "/results",
]

INVALID_JOB_DOMAINS = ["example.com", "example.org", "example.net", "test.com", "localhost"]


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


def is_valid_job_url(url: str, blacklist_path: str = "output/blacklist.json") -> bool:
    """Filter out placeholder/example URLs, parked domains, and blacklisted URLs."""
    if not url:
        return False
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if any(d in domain for d in INVALID_JOB_DOMAINS):
        return False
    if any(marker in domain for marker in PARKED_DOMAIN_MARKERS):
        return False
    if is_blacklisted(blacklist_path, url):
        return False
    return True


def crawl_listing_page(url: str, debug: bool = False) -> list[dict[str, str]]:
    """Visit a listing page with Playwright and extract individual job posting URLs."""
    if not PLAYWRIGHT_AVAILABLE:
        print(f"Playwright not available, skipping crawl of {url}")
        return []

    job_links: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    all_found_urls: list[str] = []

    try:
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
                href_domain = domain_of(href)
                listing_domain = domain_of(url)
                is_same_domain = href_domain == listing_domain or listing_domain in href_domain or href_domain in listing_domain
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
    verbose: bool = False,
    max_pages: int = MAX_LISTING_PAGES,
    max_per_domain: int = MAX_PAGES_PER_DOMAIN,
    blacklist_path: str = "output/blacklist.json",
) -> list[dict[str, Any]]:
    """Crawl listing page URLs from search results and return individual job postings."""
    if not PLAYWRIGHT_AVAILABLE:
        log_warning("Playwright not available, skipping listing crawl.", verbose)
        return []

    all_jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    selected = select_listing_urls(search_results, max_pages=max_pages, max_per_domain=max_per_domain)
    total_urls = len({item.get("url", "").strip() for item in search_results if item.get("url", "").strip()})
    skipped_by_caps = total_urls - len(selected)
    log_info(f"\n=== CRAWLING {len(selected)} LISTING PAGES ({skipped_by_caps} skipped by caps/domain limits) ===", verbose)

    pages_with_results = 0
    pages_empty = 0
    pages_skipped = 0
    for item in selected:
        listing_url = item.get("url", "")
        if is_blacklisted(blacklist_path, listing_url):
            log_warning(f"\nSkipping blacklisted listing page: {listing_url}", verbose)
            pages_skipped += 1
            continue

        ok, reason = check_listing_url(listing_url)
        if not ok:
            log_warning(f"\nSkipping unreachable listing page ({reason}): {listing_url}", verbose)
            add_to_blacklist(blacklist_path, [listing_url])
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

    log_info(f"\n=== CRAWL COMPLETE: {len(all_jobs)} unique job URLs found ===", verbose)
    log_info(f"  Pages with results: {pages_with_results}/{len(selected)}", verbose)
    if pages_skipped:
        log_info(f"  Pages skipped (blacklisted/unreachable): {pages_skipped}/{len(selected)}", verbose)
    if pages_empty:
        log_info(f"  Pages with no results: {pages_empty}/{len(selected)}", verbose)
        if pages_empty + pages_skipped == len(selected):
            log_warning("  All pages returned zero job URLs. Possible causes:", verbose)
            log_warning("    - Page failed to render (JS-heavy / anti-bot)", verbose)
            log_warning("    - URL patterns don't match this job board", verbose)
            log_warning("    - Page is a search results page (not a listing page)", verbose)
            log_warning("  Tip: run with --debug to see raw URLs found on each page", verbose)
    log_info("", verbose)

    # Post-crawl verification: HEAD-request each URL to filter dead/parked domains
    if all_jobs:
        log_info(f"=== VERIFYING {len(all_jobs)} URLs (HEAD request) ===", verbose)
        verified_jobs: list[dict[str, Any]] = []
        rejected = 0
        for job in all_jobs:
            if verify_url_resolves(job["url"]):
                verified_jobs.append(job)
            else:
                rejected += 1
                if debug:
                    log_debug(f"  Rejected (unreachable/parked): {job['url']}", verbose)
        all_jobs = verified_jobs
        log_info(f"  Verified: {len(all_jobs)} | Rejected: {rejected}", verbose)

    return all_jobs
