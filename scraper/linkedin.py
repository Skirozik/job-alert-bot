import re
import time
import random
import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.linkedin.com/jobs/",
}


def fetch_listings(
    keyword: str, location: str, lookback_seconds: int = 7200
) -> tuple[list[dict], Optional[str]]:
    """Fetch job listings from LinkedIn guest search API.

    Returns (jobs_list, error_string_or_None).
    error = "rate_limited" means back off; other strings are network/parse errors.
    """
    params = {
        "keywords": keyword,
        "location": location,
        "f_E": "1",              # Internship experience level
        "f_TPR": f"r{lookback_seconds}",
        "start": "0",
    }
    try:
        resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=15)
        if resp.status_code == 429:
            log.warning("Rate limited: '%s' in %s", keyword, location)
            return [], "rate_limited"
        resp.raise_for_status()
        jobs = _parse_listings(resp.text)
        return jobs, None
    except Exception as exc:
        log.error("Search error '%s'/%s: %s", keyword, location, exc)
        return [], str(exc)


def _parse_listings(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    jobs = []
    for card in soup.find_all("li"):
        try:
            urn_el = card.find(attrs={"data-entity-urn": True})
            if not urn_el:
                continue
            m = re.search(r"jobPosting:(\d+)", urn_el["data-entity-urn"])
            if not m:
                continue
            job_id = m.group(1)

            title = _text(card, "h3", "base-search-card__title")
            if not title:
                continue  # skip malformed cards

            company = _text(card, "h4", "base-search-card__subtitle")
            location = _text(card, "span", "job-search-card__location")
            time_el = card.find("time")
            posted_at = time_el.get("datetime") if time_el else None
            link_el = card.find("a", class_="base-card__full-link")
            url = (
                link_el["href"].split("?")[0]
                if link_el
                else f"https://www.linkedin.com/jobs/view/{job_id}/"
            )

            jobs.append({
                "id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "url": url,
                "posted_at": posted_at,
                "description": None,
            })
        except Exception as exc:
            log.debug("Card parse error: %s", exc)
    return jobs


def _text(soup, tag: str, cls: str) -> str:
    el = soup.find(tag, class_=cls)
    return el.get_text(strip=True) if el else ""


def fetch_description(job_id: str) -> Optional[str]:
    """Fetch full job description from the LinkedIn job detail endpoint.

    Returns description text (truncated to 4000 chars) or None on failure.
    Always sleeps before the request to avoid rate limiting.
    """
    time.sleep(random.uniform(2.0, 3.5))
    try:
        resp = requests.get(
            DETAIL_URL.format(job_id), headers=HEADERS, timeout=15
        )
        if resp.status_code == 429:
            log.warning("Rate limited on detail fetch: job %s", job_id)
            return None
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        # Primary: expanded description markup
        desc_el = soup.find("div", class_="show-more-less-html__markup")
        if desc_el:
            return desc_el.get_text(separator=" ", strip=True)[:4000]

        # Fallback: job criteria section (seniority, employment type, etc.)
        criteria_el = soup.find("ul", class_="description__job-criteria-list")
        if criteria_el:
            return criteria_el.get_text(separator=" ", strip=True)[:2000]

        return None
    except Exception as exc:
        log.warning("Description fetch failed for job %s: %s", job_id, exc)
        return None
