import re
import json as _json
import time
import random
import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

# LinkedIn renders the pay chip as its own element, and it is NOT always backed
# by JSON-LD. Confirmed live on job 4444793701 (Quadrillion, "Software
# Engineering Intern (Summer 2027)"): the page shows "$4,000.00/wk - $6,000.00/wk"
# in this div while returning ZERO application/ld+json blocks, so the baseSalary
# parser below never ran and the salary was silently dropped. The description
# body had no pay figure either — its only "$" mentions were the company's
# venture funding ("$18.3M from Conviction"), which the extractor correctly
# ignores — so nothing downstream could recover it.
_SALARY_EL_CLASSES = ("compensation__salary", "salary")

# "$4,000.00/wk - $6,000.00/wk" -> lo, hi, unit. Both sides repeat the unit;
# we keep one. The decimals are always ".00" on these chips.
_SALARY_RANGE_RE = re.compile(
    r"\$\s*([\d,]+)(?:\.00)?\s*/?\s*(hr|hour|wk|week|mo|month|yr|year)?\s*[-–—]\s*"
    r"\$\s*([\d,]+)(?:\.00)?\s*/?\s*(hr|hour|wk|week|mo|month|yr|year)?",
    re.I,
)
_SALARY_SINGLE_RE = re.compile(
    r"\$\s*([\d,]+)(?:\.00)?\s*/?\s*(hr|hour|wk|week|mo|month|yr|year)?", re.I
)


def _salary_from_html(soup) -> Optional[str]:
    """Read the rendered pay chip when JSON-LD is absent.

    Deliberately scoped to the compensation element rather than regexing the
    page: postings routinely quote funding rounds, revenue, and customer
    figures in dollars, and a page-wide scan would happily report "$18.3M" as
    an internship's pay.
    """
    for cls in _SALARY_EL_CLASSES:
        el = soup.find(class_=cls)
        if not el:
            continue
        text = " ".join((el.get_text(" ", strip=True) or "").split())
        if "$" not in text:
            continue
        m = _SALARY_RANGE_RE.search(text)
        if m:
            lo, u1, hi, u2 = m.group(1), m.group(2), m.group(3), m.group(4)
            unit = (u1 or u2 or "").lower()
            suffix = f"/{unit}" if unit else ""
            # LinkedIn renders a fixed rate as a degenerate range ("$25.00/hr -
            # $25.00/hr"); collapse it rather than showing "$25–$25/hr".
            return (f"${lo}" if lo == hi else f"${lo}–${hi}") + suffix
        m = _SALARY_SINGLE_RE.search(text)
        if m:
            unit = (m.group(2) or "").lower()
            return f"${m.group(1)}" + (f"/{unit}" if unit else "")
        return text[:60]
    return None

# Was 4000/2000 — a live DB check found 54.5% of stored descriptions were
# hitting the 4000 cap exactly (median length == cap), silently truncating
# out whatever came after it, which commonly includes the
# requirements/eligibility section near the end of a posting. See the same
# constant/comment in external_descriptions.py.
MAX_LEN = 12000
MAX_LEN_CRITERIA_FALLBACK = 6000

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


MAX_RETRIES = 2  # on a 429, retry with backoff this many times before giving up

# Detail requests stay 2-3.5 seconds apart, but the first one in a process can
# start immediately. The old implementation slept before every request,
# including the first and including retries that had already backed off. A
# start-to-start limiter preserves the same sustained request rate without
# adding dead time to the first notification in a run.
_DETAIL_NEXT_REQUEST_AT = 0.0


def _wait_for_detail_slot() -> None:
    global _DETAIL_NEXT_REQUEST_AT
    now = time.monotonic()
    if _DETAIL_NEXT_REQUEST_AT > now:
        time.sleep(_DETAIL_NEXT_REQUEST_AT - now)
    _DETAIL_NEXT_REQUEST_AT = time.monotonic() + random.uniform(2.0, 3.5)


def fetch_listings(
    keyword: str, location: str, lookback_seconds: int = 7200, start: int = 0
) -> tuple[list[dict], Optional[str]]:
    """Fetch one page of job listings from LinkedIn guest search API.

    Returns (jobs_list, error_string_or_None).
    error = "rate_limited" means back off (after retrying); other strings are
    network/parse errors.
    """
    params = {
        "keywords": keyword,
        "location": location,
        "f_E": "1",              # Internship experience level
        "f_TPR": f"r{lookback_seconds}",
        "start": str(start),
    }
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=15)
            if resp.status_code == 429:
                if attempt < MAX_RETRIES:
                    backoff = _backoff_seconds(attempt)
                    log.warning("Rate limited: '%s' in %s — retry %d/%d in %.1fs",
                                keyword, location, attempt + 1, MAX_RETRIES, backoff)
                    time.sleep(backoff)
                    continue
                log.warning("Rate limited: '%s' in %s — giving up after %d retries",
                            keyword, location, MAX_RETRIES)
                return [], "rate_limited"
            resp.raise_for_status()
            jobs = _parse_listings(resp.text)
            return jobs, None
        except Exception as exc:
            log.error("Search error '%s'/%s: %s", keyword, location, exc)
            return [], str(exc)
    return [], "rate_limited"


def _backoff_seconds(attempt: int) -> float:
    return 5 * (2 ** attempt) + random.uniform(0, 2)


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

            is_easy_apply = "easy apply" in card.get_text(separator=" ").lower()

            jobs.append({
                "id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "url": url,
                "posted_at": posted_at,
                "description": None,
                "is_easy_apply": is_easy_apply,
            })
        except Exception as exc:
            log.debug("Card parse error: %s", exc)
    return jobs


def _text(soup, tag: str, cls: str) -> str:
    el = soup.find(tag, class_=cls)
    return el.get_text(strip=True) if el else ""


def fetch_description(job_id: str) -> tuple[Optional[str], Optional[str], Optional[str], bool, Optional[str]]:
    """Fetch job description, logo, apply URL, Easy Apply flag, and salary.

    Returns (description, logo_url, apply_url, is_easy_apply, salary).
    Spaces request starts to avoid rate limiting; retries with backoff on a 429
    before giving up. The first request in a process does not sleep.
    """
    resp = None
    for attempt in range(MAX_RETRIES + 1):
        _wait_for_detail_slot()
        try:
            resp = requests.get(
                DETAIL_URL.format(job_id), headers=HEADERS, timeout=15
            )
            if resp.status_code == 429:
                if attempt < MAX_RETRIES:
                    backoff = _backoff_seconds(attempt)
                    log.warning("Rate limited on detail fetch: job %s — retry %d/%d in %.1fs",
                                job_id, attempt + 1, MAX_RETRIES, backoff)
                    time.sleep(backoff)
                    continue
                log.warning("Rate limited on detail fetch: job %s — giving up after %d retries",
                            job_id, MAX_RETRIES)
                return None, None, None, False, None
            resp.raise_for_status()
            break
        except Exception as exc:
            log.warning("Description fetch failed for job %s: %s", job_id, exc)
            return None, None, None, False, None

    try:
        soup = BeautifulSoup(resp.text, "lxml")

        # ── Description ────────────────────────────────────────────────────
        description = None
        desc_el = soup.find("div", class_="show-more-less-html__markup")
        if desc_el:
            description = desc_el.get_text(separator=" ", strip=True)[:MAX_LEN]
        else:
            criteria_el = soup.find("ul", class_="description__job-criteria-list")
            if criteria_el:
                description = criteria_el.get_text(separator=" ", strip=True)[:MAX_LEN_CRITERIA_FALLBACK]

        # ── Company logo ────────────────────────────────────────────────────
        logo_url = None
        for img in soup.find_all("img"):
            src = img.get("data-delayed-url") or img.get("src") or ""
            if "media.licdn.com" in src and "company-logo" in src:
                logo_url = src
                break

        # ── Apply URL + Easy Apply + Salary ─────────────────────────────────
        apply_url = None
        is_easy_apply = False
        salary = None

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = _json.loads(script.string or "")
                if data.get("@type") == "JobPosting":
                    is_easy_apply = bool(data.get("directApply", False))
                    if not is_easy_apply:
                        ext = data.get("url", "")
                        if ext and "linkedin.com" not in ext:
                            apply_url = ext
                    bs = data.get("baseSalary", {}).get("value", {})
                    lo = bs.get("minValue")
                    hi = bs.get("maxValue")
                    unit = bs.get("unitText", "")
                    # minValue/maxValue can come back as comma-formatted strings
                    # (e.g. "41,600") — strip commas before int() or this raises
                    # and the salary is silently dropped by the except below.
                    lo = int(str(lo).replace(",", "")) if lo not in (None, "") else None
                    hi = int(str(hi).replace(",", "")) if hi not in (None, "") else None
                    if lo and hi:
                        salary = f"${lo:,}–${hi:,}{(' ' + unit) if unit else ''}"
                    elif lo:
                        salary = f"${lo:,}{(' ' + unit) if unit else ''}"
                    break
            except Exception:
                pass

        # JSON-LD is the better source when present (structured min/max/unit),
        # but it is frequently absent on these guest pages. Fall back to the
        # rendered chip rather than losing the figure entirely.
        if not salary:
            salary = _salary_from_html(soup)

        if not is_easy_apply and "easy apply" in resp.text.lower():
            is_easy_apply = True

        return description, logo_url, apply_url, is_easy_apply, salary
    except Exception as exc:
        log.warning("Description fetch failed for job %s: %s", job_id, exc)
        return None, None, None, False, None
