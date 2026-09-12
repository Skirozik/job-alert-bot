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
#
# RANGE-AWARE, and that is not a nicety. The second bound of a range often
# carries no dollar sign of its own -- "$20-71/hr" -- and a pattern that
# requires one finds a single amount, whose median is itself. The band then
# ranks at its FLOOR, silently reverting the median rule for the commonest way
# a range is written: 123 distinct live strings, 253 rows.
#
# The shape comes from classifier.py's tool schema, which tells the model to
# emit "e.g. '$20-30/hr'". Do NOT go fix it there: that schema sits inside a
# ~10K-token cached prefix, so editing it invalidates the cache for every job
# (see this module's docstring), and the model would produce the shape anyway.
#
# The k suffix is captured on BOTH bounds: "$200k-$260k" otherwise reads as 200,
# falls to the magnitude branch as an hourly rate, and annualises to $416,000.
_MONEY = re.compile(
    r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(k\b)?"
    r"(?:\s*[-–—]\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(k\b)?)?", re.I)
# A "k" written once governs BOTH bounds: "$39.7-72.8k/yr" is 39,700 to 72,800,
# not 39.70 to 72,800. Only propagated onto a bound below 1000, which is what
# stops "$100,000 - 401k" inheriting it.
_K_MAX = 1000
# The slash form is its OWN alternative, outside the \b...\b wrapper. Inside it,
# the leading \b demanded a word character before the "/", so "$45.00 / hr"
# never matched and fell through to the magnitude branch -- dead for 199 live
# rows, and it let the artifact guard below be bypassed. "/hour" must be spelt
# out too; jobView.ts has always written it this way.
_HOURLY_HINT = re.compile(r"\b(?:per\s*hour|hourly|an\s*hour)\b|/\s*(?:hr|hour)\b", re.I)
# Biweekly is tested BEFORE weekly or "biweekly" reads as weekly and doubles.
_BIWEEKLY = re.compile(r"\bbi-?weekly\b|\bevery\s+two\s+weeks\b", re.I)
_PER_WEEK = re.compile(r"\b(?:per\s*week|weekly)\b|/\s*w(?:k|eek)\b", re.I)
_PER_MONTH = re.compile(r"\b(?:per\s*month|monthly)\b|/\s*mo(?:nth)?\b", re.I)
_PER_YEAR = re.compile(r"\b(?:per\s*year|annually|annualized|annualised|a\s*year)\b"
                       r"|/\s*(?:yr|year)\b", re.I)
# A bonus written after the band, never part of it.
_ADDER = re.compile(r"\b(?:plus|additional)\b|\+", re.I)
# Text that looks like money or a unit and is neither. All stripped before the
# band is read:
#   "20 hrs/week"  -- a WORKLOAD. _PER_WEEK matches the "/week" inside it, and
#                     an hourly rate then gets multiplied by 52 instead of 2080:
#                     "$29.32 - $43.99/hr (part-time, 20 hrs/week)" came out at
#                     $1,906 a year.
#   "401k", "403b" -- a retirement plan. The range-aware _MONEY above reads the
#                     bare second operand, so "$60,000 - 401k match" became a
#                     $60,000-$401,000 band with a median of $230,500.
#   "15%"          -- a bonus percentage, read as the number 15 and wrecking the
#                     band's span. Stripped rather than excluded by a lookahead:
#                     a (?!\s*%) guard makes the engine backtrack the NUMBER to
#                     satisfy it, so "$55,000 - 15% bonus" matches the "1" and
#                     yields [55000, 1] instead of failing cleanly.
_NOISE = re.compile(
    r"\d[\d\s–—.-]*\s*(?:hrs?|hours)\s*(?:/|per\s+|a\s+)\s*(?:wk|week)s?\b"
    r"|\b40[13]\s*\(?[kb]\)?"
    r"|\d[\d.,]*\s*%", re.I)
# A figure we cannot compare to a USD bar. Returning None -- "this posting
# states no pay we can judge" -- is the honest answer; converting would need a
# rate table that goes stale silently and tells nobody. Switching to the median
# is what pushed marginal CAD bands over the line: "$68,250-$78,000 CAD"
# medians to 73,125, clears a 72,800 USD bar, and is really about US$53,000.
# A Canadian role at a listed company still stars on COMPANY; it just stops
# claiming high stated pay. (Non-dollar currencies never parsed anyway -- no "$".)
#
# The symbol forms need a guard on the character BEFORE them, or "S$" matches
# inside "US$120,000" and every American posting written that way vanishes. A
# lookbehind would be the obvious tool and is the wrong one here: JS lookbehind
# is a PARSE-time SyntaxError on Safari < 16.4, so the mirrored regex in
# goldStar.ts would take down the whole client bundle rather than just the
# salary column. (?:^|[^A-Za-z]) is equivalent and universally supported.
_NONUSD = re.compile(r"\b(?:CAD|AUD|NZD|SGD|HKD|MXN|EUR|GBP|INR)\b"
                     r"|(?:^|[^A-Za-z])(?:CA|A|NZ|S|HK)\$", re.I)

_HOURS_PER_YEAR = 2080

# Above this, the figure is a scraper artifact rather than pay. Intel posts
# "$91,198-$91,202/hr" -- annual numbers mislabelled hourly -- which annualises
# to $189,691,840 and clears any threshold trivially.
_MAX_PLAUSIBLE_ANNUAL = 500_000
# And below this it is a total, not a rate. The cap alone is one-sided: a $3,840
# lump-sum stipend picked up beside a workload clause multiplies to $199,680,
# which is wrong but perfectly plausible, so nothing catches it. jobView.ts has
# carried this floor since the sort shipped; the star copies never did.
_MIN_PLAUSIBLE_ANNUAL = 10_000

# What a figure quoted in each unit can plausibly BE. A posting states its rate
# and then restates it -- "$730/week (~$18.25/hr)", "$22.50-$29.00/hr
# ($46,800-$60,320 annualized)" -- and a median across both magnitudes is
# meaningless. Filtering to the window of the unit we settled on keeps the band
# and drops the restatement, whichever way round they were written.
#
# Ranges are [low, high). The hourly ceiling of 1000 is not a real constraint:
# 1000 x 2080 already exceeds _MAX_PLAUSIBLE_ANNUAL, so the effective hourly
# ceiling was always ~$240/hr and no genuine rate is lost.
_UNIT_WINDOW = {
    "hour": (1, 1_000),
    "week": (50, 20_000),
    "biweek": (100, 40_000),
    "month": (200, 100_000),
    "year": (1_000, float("inf")),
}
_UNIT_MULT = {"hour": _HOURS_PER_YEAR, "week": 52, "biweek": 26, "month": 12, "year": 1}

# A band whose floor is at hourly scale and whose ceiling is at annual scale is
# two units written as one range, not a generous employer: "$17.98-$135,700",
# "$37.22 - $150,000". Both conditions AND the ratio must hold, so a merely wide
# band survives -- the widest legitimate one measured is 80x
# ("$1,500-$2,500/month part-time; $80,000-$120,000/yr full-time"), and a bare
# ratio test at 100x leaves only 1.25x of margin before it starts eating real
# postings silently.
_CROSS_SCALE_LOW, _CROSS_SCALE_HIGH, _CROSS_SCALE_RATIO = 1_000, 10_000, 100

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


def _first_unit(text):
    """Which pay unit this text states, or None -- the one written FIRST.

    Not a fixed precedence. Every mixed-unit posting in the wild is written
    "<rate> (<restatement>)", so the headline unit is the earlier one, and any
    fixed order gets half of them backwards: hourly-before-weekly reads
    "$730/week (~$18.25/hr)" as a $374/hr job, and weekly-before-hourly reads
    "$22.50/hr ($46,800 annualized)" as annual.

    Ties are impossible between different units at the same offset except for
    biweekly, where "bi-weekly" also contains "weekly" three characters in --
    and position already resolves that correctly (0 < 3). The explicit ordering
    of the pairs below is only there to document the hazard.
    """
    best, at = None, None
    for unit, pattern in (("biweek", _BIWEEKLY), ("week", _PER_WEEK),
                          ("month", _PER_MONTH), ("hour", _HOURLY_HINT),
                          ("year", _PER_YEAR)):
        m = pattern.search(text)
        if m and (at is None or m.start() < at):
            best, at = unit, m.start()
    return best


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

    The order of the steps below IS the fix for most of what was wrong here.
    Each one is commented where it happens.
    """
    text = (salary or "").strip()
    if not text or _NONUSD.search(text):
        return None

    clean = _NOISE.sub(" ", text)
    # Amounts come from the part BEFORE any adder: "$21.80-$29.10/hr plus
    # $5.09/hr differential" is a band and a bonus, and counting the bonus
    # drags the median down.
    band = _ADDER.split(clean)[0]
    amounts = []
    for lo, lo_k, hi, hi_k in _MONEY.findall(band):
        pair = []
        for raw, k in ((lo, lo_k), (hi, hi_k)):
            if not raw:
                pair.append(None)
                continue
            try:
                pair.append([float(raw.replace(",", "")) * (1000 if k else 1), bool(k)])
            except ValueError:
                pair.append(None)
        # "$39.7-72.8k" states the k once and means it twice. Only a bound still
        # under 1000 inherits it, which is what stops "$100,000 - 401k" -- were
        # it not already stripped as noise -- from becoming a $401,000 ceiling.
        a, b = pair
        if a and b:
            if b[1] and not a[1] and a[0] < _K_MAX:
                a[0] *= 1000
            elif a[1] and not b[1] and b[0] < _K_MAX:
                b[0] *= 1000
        amounts.extend(v[0] for v in (a, b) if v)
    if not amounts:
        return None
    # A $0 floor is a placeholder, and the median hides it: "$0 - $200,000"
    # medians to a perfectly plausible $100,000. No real posting floors at zero.
    if min(amounts) == 0:
        return None

    # THE UNIT COMES FROM THE BAND, falling back to the cleaned string. Reading
    # the whole string unconditionally let an adder clause's unit win over one
    # the band had already stated: "$25.00/hr + $2,000/month housing stipend"
    # multiplied $25 by 12 and returned $300. The one fallback is still needed
    # for the reason the old comment gave -- a "plus" clause sometimes carries
    # the only "/hr" in the text.
    #
    # There is deliberately NO further fallback to the RAW text. It would let a
    # workload clause act as the pay unit of last resort, which sounds
    # reasonable and is not: every live string whose only unit sits inside a
    # workload clause is a lump sum or states no rate at all -- "$3,840 stipend
    # (14-16 weeks, 20 hrs/week)" would become $199,680 a year, and "Paid (15-25
    # hrs/week); rate not specified" quotes no rate to annualise. Measured: 4
    # such strings live, 4 of them wrong under that fallback, 0 helped.
    unit = _first_unit(band) or _first_unit(clean)

    # Not a fixed precedence -- THE UNIT WHOSE TOKEN APPEARS FIRST. A posting
    # writes its headline rate and then parenthesises the restatement, so
    # position is the one principled tie-break available: "$730/week
    # (~$18.25/hr)" is a weekly job and "$45/hr ($93,600/yr)" is an hourly one.
    # Where a posting is self-consistent the choice does not matter, because the
    # window below discards the restatement either way.
    # A floor at hourly scale under a ceiling at annual scale is two units
    # written as one range, not a generous employer. Checked on the figures AS
    # WRITTEN, before the window below removes half the evidence: for
    # "$37.22 - $150,000/yr" the yearly window drops the $37.22 and what is left
    # looks like a perfectly ordinary $150,000 salary.
    #
    # Any string carrying an hourly token is exempt, not merely one whose unit
    # RESOLVED to hourly. A rate and its annualised restatement legitimately
    # span 2080x, and they are written in both orders: "$45/hr ($93,600/yr)"
    # resolves to hourly, "$93,600/yr ($45/hr)" resolves to yearly, and both are
    # the same well-formed posting. The presence of "/hr" anywhere is what says
    # a sub-1000 figure is a rate rather than a malformed bound -- and the
    # strings this guard exists for, "$17.98-$135,700" and "$37.22 - $150,000",
    # carry no hourly token at all. The window below removes the restatement.
    lo_v, hi_v = min(amounts), max(amounts)
    if (not _HOURLY_HINT.search(clean)
            and lo_v < _CROSS_SCALE_LOW <= _CROSS_SCALE_HIGH <= hi_v
            and hi_v / lo_v >= _CROSS_SCALE_RATIO):
        return None

    if unit:
        low, high = _UNIT_WINDOW[unit]
        kept = [v for v in amounts if low <= v < high]
        # Everything fell outside the window this unit can plausibly hold, so
        # the unit and the figures contradict each other and neither can be
        # trusted: "$91,198-$91,202/hr" is annual pay mislabelled hourly.
        if not kept:
            return None
        amounts = kept

    amounts.sort()
    n = len(amounts)
    mid = amounts[n // 2] if n % 2 else (amounts[n // 2 - 1] + amounts[n // 2]) / 2

    if unit:
        annual = mid * _UNIT_MULT[unit]
    else:
        # No unit stated. Decided on the band's FLOOR, not its median: a
        # "$900 - $1,200" band medians above 1000 and would flip to annual on
        # the midpoint alone. A four-figure-plus number is never an hourly rate.
        annual = mid if amounts[0] >= 1000 else mid * _HOURS_PER_YEAR

    # The plausibility band lives HERE, not in the caller, so that all three
    # copies of this function return the same value for the same string and a
    # parity test can compare them directly.
    if not _MIN_PLAUSIBLE_ANNUAL <= annual <= _MAX_PLAUSIBLE_ANNUAL:
        return None
    return annual


def _salary_clears_bar(salary) -> bool:
    """True when the annualised median clears the threshold."""
    annual = _annual_salary(salary)
    # The plausibility band is applied inside _annual_salary now, so anything
    # that reaches here is already a number worth comparing.
    if annual is None:
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
