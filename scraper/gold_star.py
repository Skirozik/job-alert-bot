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
_MONEY = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
_HOURLY_HINT = re.compile(r"\b(per\s*hour|/\s*hr|hourly|an\s*hour)\b", re.I)

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


def _salary_clears_bar(salary) -> bool:
    """True when the LOWER bound of a stated range clears the threshold.

    Lower bound, not upper, and not the average: a posting advertising
    "$20 - $70/hr" is a $20/hr job with a ceiling, and starring it on the
    ceiling would be exactly the kind of false positive that turns the star
    into wallpaper.
    """
    text = (salary or "").strip()
    if not text:
        return False
    amounts = []
    for raw in _MONEY.findall(text):
        try:
            amounts.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    if not amounts:
        return False

    low = min(amounts)
    th = _load()["thresholds"]
    # Decide the unit from the text where it says so, and fall back to
    # magnitude. A four-figure-plus number is never an hourly rate.
    if _HOURLY_HINT.search(text):
        return low >= th["hourly"]
    if low >= 1000:
        return low >= th["annual"]
    return low >= th["hourly"]


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
