"""Gold star: which postings are worth a hand-curated resume.

A star does NOT mean "good job" -- the whole list is already filtered to jobs
worth applying to. It means high marginal return on spending 30-60 minutes
tailoring a resume: P(curation flips the decision) x value of the job.

THE RULE:  (big-name company OR high stated salary OR mobile role)
           AND NOT is_easy_apply

The Easy Apply term is a GATE, not another OR, and that asymmetry is the point.
LinkedIn Easy Apply reuses whatever resume is already on file, so a resume
curated for one is effort that never reaches a human. Treated as an OR it would
also star every ATS job -- thousands of them -- and a star on everything is a
star on nothing.

Rules and thresholds live in fixtures/star_rules.json, shared with
web/lib/goldStar.ts. Both implementations assert every `cases` entry in that
file, so the phone and the dashboard cannot disagree about what is starred --
the same anti-drift contract fixtures/canonical_target_keys.json provides for
target_key.

Derived, never stored: no column, no migration, no backfill, and no classifier
change (which matters -- classifier.py caches a ~10K-token prefix that includes
the tool schema, so editing that schema invalidates the cache for every job).
"""

import json
import re
from pathlib import Path

# In web/lib/, not fixtures/: Vercel deploys with web/ as its root, so a
# file outside it is not in the bundle and goldStar.ts could not import it.
# Python is unconstrained here -- Actions checks out the whole repo.
_RULES_PATH = Path(__file__).parent.parent / "web" / "lib" / "star_rules.json"

# Mirrors db.norm_company. Imported rather than duplicated would be better, but
# this module is also read by web/ tooling expectations as the reference Python
# implementation, and db.py pulls in supabase at import time -- too heavy for a
# pure rule function. The noise list is copied verbatim; if db.py's changes,
# the parity fixture will not catch it, so keep them in sync by hand.
_COMPANY_NOISE = {
    "inc", "llc", "corp", "co", "company", "international", "electronics",
    "financial", "technologies", "technology", "labs", "group", "holdings",
    "solutions", "software", "ltd", "plc", "industries", "services", "systems",
    "digital", "global", "ventures",
}

_MOBILE_TITLE = re.compile(r"\b(ios|swift|swiftui|android|mobile|react native)\b", re.I)

# "$45.00", "$45", "$120,000" -- the money shapes that actually appear in the
# salary column, which is free display text and never a number.
# The k suffix is captured: "$200k-$260k" otherwise reads as 200, falls to the
# magnitude branch as an hourly rate, and annualises to $416,000.
_MONEY = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(k\b)?", re.I)
_HOURLY_HINT = re.compile(r"\b(per\s*hour|/\s*hr|hourly|an\s*hour)\b", re.I)
# Biweekly is tested BEFORE weekly or "biweekly" reads as weekly and doubles.
_BIWEEKLY = re.compile(r"\bbi-?weekly\b|\bevery\s+two\s+weeks\b", re.I)
_PER_WEEK = re.compile(r"\b(per\s*week|weekly)\b|/\s*w(k|eek)\b", re.I)
_PER_MONTH = re.compile(r"\b(per\s*month|monthly)\b|/\s*mo(nth)?\b", re.I)
_PER_YEAR = re.compile(r"\b(per\s*year|annually|annualized|annualised|a\s*year)\b"
                       r"|/\s*(yr|year)\b", re.I)
# A bonus written after the band, never part of it.
_ADDER = re.compile(r"\b(plus|additional)\b|\+", re.I)

_HOURS_PER_YEAR = 2080

# Above this, the figure is a scraper artifact rather than pay. Intel posts
# "$91,198-$91,202/hr" -- annual numbers mislabelled hourly -- which annualises
# to $189,691,840 and clears any threshold trivially.
_MAX_PLAUSIBLE_ANNUAL = 500_000

_rules = None


def _load() -> dict:
    global _rules
    if _rules is None:
        _rules = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    return _rules


def _norm_company(c: str) -> str:
    c = (c or "").lower().strip()
    c = re.sub(r"\(yc.*?\)", "", c)
    c = re.sub(r"'s\b", "", c)
    c = re.sub(r"[^a-z0-9 ]", " ", c)
    toks = [t for t in c.split() if t]
    if toks and toks[0] == "the":
        toks = toks[1:]
    stripped = [t for t in toks if t not in _COMPANY_NOISE]
    return " ".join(stripped if stripped else toks).strip()


def _starred_companies() -> set:
    return {_norm_company(name) for name in _load()["companies"]}


def _annual_salary(salary):
    """The posting's pay as one annual number, or None when it states none.

    THE MEDIAN OF THE BAND, not its floor (changed 2026-09-11). The floor was
    chosen to stop a high ceiling creating false stars, and it did -- but it
    also sank every wide band regardless of its midpoint, and a wide band is
    how the best-paying employers post. IBM's "$61,200-$138,600" and Cisco's
    "$44,000-$185,000" both failed the bar on a floor that is the
    rising-sophomore end of the range. Measured on the live table: 110 open
    APPLY rows clear the bar on the median that the floor denied.

    EVERY UNIT ANNUALISES. The old rule knew only hourly and treated anything
    else as a yearly figure, so Composio's "$10,000/mo" read as a $10,000-a-year
    job and earned no star against a $120,000 reality. Weekly, biweekly and
    monthly are now read explicitly, and an explicit unit always beats the
    magnitude fallback.
    """
    text = (salary or "").strip()
    if not text:
        return None

    # Amounts come from the part BEFORE any adder: "$21.80-$29.10/hr plus
    # $5.09/hr differential" is a band and a bonus, and counting the bonus
    # drags the median down. Units are read from the whole string, since a
    # "plus" clause sometimes carries the only "/hr" in the text.
    band = _ADDER.split(text)[0]
    amounts = []
    for raw, k in _MONEY.findall(band):
        try:
            amounts.append(float(raw.replace(",", "")) * (1000 if k else 1))
        except ValueError:
            continue
    if not amounts:
        return None
    # A $0 floor is a placeholder, and the median hides it: "$0 - $200,000"
    # medians to a perfectly plausible $100,000. No real posting floors at zero.
    if min(amounts) == 0:
        return None

    amounts.sort()
    n = len(amounts)
    mid = amounts[n // 2] if n % 2 else (amounts[n // 2 - 1] + amounts[n // 2]) / 2

    if _BIWEEKLY.search(text):
        return mid * 26
    if _PER_WEEK.search(text):
        return mid * 52
    if _PER_MONTH.search(text):
        return mid * 12
    if _HOURLY_HINT.search(text):
        return mid * _HOURS_PER_YEAR
    if _PER_YEAR.search(text):
        return mid
    # No unit stated. Decided on the band's FLOOR, not its median: a
    # "$900 - $1,200" band medians above 1000 and would flip to annual on the
    # midpoint alone. A four-figure-plus number is never an hourly rate.
    return mid if amounts[0] >= 1000 else mid * _HOURS_PER_YEAR


def _salary_clears_bar(salary) -> bool:
    """True when the annualised median clears the threshold."""
    annual = _annual_salary(salary)
    if annual is None:
        return False
    # Garbage clears any floor trivially, and switching to a median made that
    # worse rather than better, so it is rejected rather than starred.
    if annual > _MAX_PLAUSIBLE_ANNUAL:
        return False
    return annual >= _load()["thresholds"]["annual"]


def star_reasons(job: dict) -> list:
    """Why this job is starred, or [] when it is not.

    Reasons rather than a bool so the UI and the push can say WHY, which is the
    difference between a badge someone trusts and one they learn to ignore.
    Order is stable (company, salary, mobile) so the parity fixture can compare
    lists directly.
    """
    # TWO gates, both checked before any signal, so no signal can survive them
    # and short-circuiting makes the ordering impossible to break by accident.
    #
    # 1. Easy Apply reuses the resume already on file, so one curated for it is
    #    effort that never reaches a human.
    # 2. APPLY only, never APPLY_CAVEAT. A caveat job already carries a known
    #    reservation -- that is what the tier MEANS -- so it is a strange
    #    candidate for an hour of tailoring. Reserving the star for clean fits
    #    is also what keeps it scarce: company matching alone was marking 15.8%
    #    of the review queue.
    if job.get("is_easy_apply"):
        return []
    if job.get("tier") != "APPLY":
        return []

    reasons = []
    if _norm_company(job.get("company", "")) in _starred_companies():
        reasons.append("company")
    if _salary_clears_bar(job.get("salary")):
        reasons.append("salary")
    if (job.get("suggested_resume") == "Mobile"
            or _MOBILE_TITLE.search(job.get("title") or "")):
        reasons.append("mobile")
    return reasons


def is_starred(job: dict) -> bool:
    return bool(star_reasons(job))


_REASON_LABEL = {
    "company": "top-tier company",
    "salary": "high stated pay",
    "mobile": "mobile role - your App Store app is the differentiator",
}


def reason_summary(job: dict) -> str:
    """One short human line for the push body."""
    return ", ".join(_REASON_LABEL.get(r, r) for r in star_reasons(job))
