"""Supplementary job source: community-maintained GitHub internship trackers.

Unlike LinkedIn, these don't rate-limit — they're plain README fetches — so
they're a free hedge against LinkedIn blocking the scraper's IP. Two markup
styles are in use across these repos and both are parsed:
  - SimplifyJobs: literal <table> HTML embedded in the README
  - speedyapply: markdown pipe tables (with HTML links inside cells)

In both, the row shape is consistently
  [Company, Role, Location, ...(optional Salary)..., Apply link, Age]
so the apply-link cell is always second-to-last and the age is always last.
"""

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_SOURCES = [
    ("https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/README.md",
     "SimplifyJobs/Summer2026-Internships"),
    ("https://raw.githubusercontent.com/speedyapply/2026-SWE-College-Jobs/main/README.md",
     "speedyapply/2026-SWE-College-Jobs"),
    ("https://raw.githubusercontent.com/speedyapply/2026-AI-College-Jobs/main/README.md",
     "speedyapply/2026-AI-College-Jobs"),
]

# Only pull recently-posted rows. These lists carry the entire season's
# backlog (hundreds of rows); without this, the first run after enabling
# this source would try to classify all of them in one go and blow the
# GitHub Actions job timeout. Anything older was either already caught by
# an earlier run (dedup handles it) or isn't "new" in any useful sense.
MAX_AGE_DAYS = 7

_AGE_RE = re.compile(r"^(\d+)\s*d$", re.IGNORECASE)


def fetch_github_listings() -> list[dict]:
    """Fetch and parse job rows from the tracked GitHub README lists.

    Returns job dicts shaped like linkedin.py's output. Best-effort: a fetch
    or parse failure on one source is logged and skipped, never raised —
    this is a bonus source layered on top of the primary LinkedIn search.
    """
    jobs: list[dict] = []
    for url, source_name in _SOURCES:
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            source_jobs = _parse_readme(resp.text, source_name)
            log.info("GitHub source %s: %d recent listings", source_name, len(source_jobs))
            jobs.extend(source_jobs)
        except Exception as exc:
            log.error("GitHub source fetch failed for %s: %s", source_name, exc)
    return jobs


def _parse_readme(markdown: str, source_name: str) -> list[dict]:
    jobs = _parse_html_tables(markdown, source_name)
    jobs.extend(_parse_pipe_tables(markdown, source_name))
    return jobs


def _parse_html_tables(markdown: str, source_name: str) -> list[dict]:
    soup = BeautifulSoup(markdown, "lxml")
    jobs = []
    for table in soup.find_all("table"):
        last_company = None
        body = table.find("tbody") or table
        for row in body.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            try:
                company_text = cells[0].get_text(strip=True)
                if company_text in ("↳", "") and last_company:
                    company = last_company
                else:
                    company = company_text
                    last_company = company

                title = cells[1].get_text(strip=True)
                location = cells[2].get_text(strip=True) if len(cells) > 4 else ""
                link_el = cells[-2].find("a", href=True)
                apply_url = link_el["href"] if link_el else None
                age_text = cells[-1].get_text(strip=True)

                job = _make_job(company, title, location, apply_url, age_text, source_name)
                if job:
                    jobs.append(job)
            except Exception as exc:
                log.debug("GitHub HTML row parse error (%s): %s", source_name, exc)
    return jobs


_SEPARATOR_LINE_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _parse_pipe_tables(markdown: str, source_name: str) -> list[dict]:
    jobs = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines) - 1:
        if lines[i].strip().startswith("|") and _SEPARATOR_LINE_RE.match(lines[i + 1]):
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if len(cells) >= 5:
                    try:
                        company = BeautifulSoup(cells[0], "lxml").get_text(strip=True)
                        title = BeautifulSoup(cells[1], "lxml").get_text(strip=True)
                        location = BeautifulSoup(cells[2], "lxml").get_text(strip=True)
                        link_soup = BeautifulSoup(cells[-2], "lxml")
                        link_el = link_soup.find("a", href=True)
                        apply_url = link_el["href"] if link_el else None
                        age_text = BeautifulSoup(cells[-1], "lxml").get_text(strip=True)

                        job = _make_job(company, title, location, apply_url, age_text, source_name)
                        if job:
                            jobs.append(job)
                    except Exception as exc:
                        log.debug("GitHub pipe-row parse error (%s): %s", source_name, exc)
                i += 1
        else:
            i += 1
    return jobs


def _make_job(
    company: str, title: str, location: str, apply_url: Optional[str], age_text: str, source_name: str
) -> Optional[dict]:
    if not company or not title or not apply_url:
        return None

    age_days = _age_days(age_text)
    if age_days is None or age_days > MAX_AGE_DAYS:
        return None

    job_id = "gh:" + hashlib.sha1(apply_url.encode("utf-8")).hexdigest()[:16]
    posted_at = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()

    return {
        "id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "url": apply_url,
        "apply_url": apply_url,
        "posted_at": posted_at,
        "description": None,
        "is_easy_apply": False,
        "search_term": f"github:{source_name}",
    }


def _age_days(age_text: str) -> Optional[int]:
    m = _AGE_RE.match(age_text.strip())
    return int(m.group(1)) if m else None
