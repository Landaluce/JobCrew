from __future__ import annotations

import json
import os
import socket
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MAX_LISTING_PAGES = 8
MAX_PAGES_PER_DOMAIN = 2

PARKED_DOMAIN_MARKERS = [
    "hugedomains.com", "sedo.com", "afternic.com", "dan.com",
    "go daddy.com", "godaddy.com", "namecheap.com", "flippa.com",
    "buydomains.com", "domainmarket.com", "undeveloped.com",
    "parkingcrew.net", "above.com", "bodis.com", "porkbun.com",
]

# Domains that host legitimate job postings (used to prioritize listing pages)
KNOWN_JOB_BOARD_DOMAINS = [
    "linkedin.com", "indeed.com", "glassdoor.com", "greenhouse.io",
    "lever.co", "workable.com", "smartrecruiters.com", "icims.com",
    "jobvite.com", "ashbyhq.com", "notion.site", "dice.com",
    "ziprecruiter.com", "remoteok.com", "weworkremotely.com",
    "builtin.com", "simplyhired.com", "careerbuilder.com", "monster.com",
    "wellfound.com", "justremote.co", "himalayas.app",
    "coursera.org", "jooble.org", "adzuna.com", "reed.co.uk",
]


def domain_of(url: str) -> str:
    """Return the lowercase hostname of a URL with a leading 'www.' removed."""
    return urlparse(url).netloc.lower().removeprefix("www.")


def is_known_job_board(url: str) -> bool:
    domain = domain_of(url)
    return any(board in domain for board in KNOWN_JOB_BOARD_DOMAINS)


def load_blacklist(path: str | Path) -> list[str]:
    """Load blacklisted URLs from a JSON file."""
    path = Path(path)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(entry) for entry in data]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def add_to_blacklist(path: str | Path, entries: list[str]) -> list[str]:
    """Append unique entries to the blacklist and persist atomically."""
    existing = load_blacklist(path)
    seen = {entry.lower() for entry in existing}
    merged = list(existing)
    for entry in entries:
        entry = str(entry).strip()
        if entry and entry.lower() not in seen:
            seen.add(entry.lower())
            merged.append(entry)
    if merged != existing:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    return merged


def remove_from_blacklist(path: str | Path, entries: list[str]) -> list[str]:
    """Remove entries (case-insensitively) from the blacklist and persist atomically."""
    existing = load_blacklist(path)
    removed_lower = {str(entry).strip().lower() for entry in entries if str(entry).strip()}
    remaining = [entry for entry in existing if entry.lower() not in removed_lower]
    if remaining != existing:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(remaining, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    return remaining


def is_blacklisted(path: str | Path, url: str) -> bool:
    """Check whether a URL is covered by a blacklist entry.

    Bare-domain entries (e.g. ``dead.example.com``, written by the dashboard's
    "block domain" action) match the URL's host exactly or as a subdomain.
    Full-URL entries match the URL as a substring. Plain substring matching of
    a bare domain would over-block unrelated hosts like ``notdead.example.computer``.
    """
    url = (url or "").strip()
    if not url:
        return False
    url_lower = url.lower()
    parsed_domain = urlparse(url).netloc.lower().removeprefix("www.")
    for entry in load_blacklist(path):
        entry = entry.strip().lower()
        if not entry:
            continue
        if "." in entry and not entry.startswith(("http://", "https://")):
            if parsed_domain and (parsed_domain == entry or parsed_domain.endswith("." + entry)):
                return True
        elif entry in url_lower:
            return True
    return False


def select_listing_urls(
    search_results: list[dict[str, Any]],
    max_pages: int = MAX_LISTING_PAGES,
    max_per_domain: int = MAX_PAGES_PER_DOMAIN,
) -> list[dict[str, Any]]:
    """Pick which listing pages to crawl.

    Dedupes URLs, ranks known job boards first, caps pages per domain,
    then caps the total number of pages.
    """
    deduped: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in search_results:
        url = (item.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(item)

    known = [item for item in deduped if is_known_job_board(item["url"])]
    others = [item for item in deduped if not is_known_job_board(item["url"])]

    selected: list[dict[str, Any]] = []
    per_domain: dict[str, int] = {}
    for item in known + others:
        if len(selected) >= max_pages:
            break
        domain = domain_of(item["url"])
        if per_domain.get(domain, 0) >= max_per_domain:
            continue
        per_domain[domain] = per_domain.get(domain, 0) + 1
        selected.append(item)
    return selected


def check_listing_url(url: str, timeout: int = 10) -> tuple[bool, str]:
    """Cheap HEAD-request liveness check for a listing page.

    Returns (ok, reason); reason is "" when ok. Reasons distinguish
    permanent failures ("parked", "http_404") from transient ones
    ("dns_error", "timeout", "unreachable").
    """
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, method="HEAD", headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        })
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except urllib.error.HTTPError as exc:
        return False, f"http_{exc.code}"
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.gaierror):
            return False, "dns_error"
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return False, "timeout"
        return False, "unreachable"
    except TimeoutError:
        return False, "timeout"
    except Exception:
        return False, "unreachable"

    final_url = resp.geturl().lower()
    if any(marker in final_url for marker in PARKED_DOMAIN_MARKERS):
        return False, "parked"
    if resp.status >= 400:
        return False, f"http_{resp.status}"
    return True, ""
