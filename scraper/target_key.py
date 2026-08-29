"""Cross-source identity for an application target.

WHY THIS EXISTS: one real posting reaches us through three doors, and each
door hands us a different primary key -- LinkedIn a bare numeric id, the ATS
watcher "ats:" + sha1(url), the GitHub trackers "gh:" + sha1(apply_url). The
database therefore stores three rows, each defaulting to status 'new', each
independently notifiable. norm_key is the only thing linking them and it is
too brittle to carry that weight on its own: norm_role deliberately keeps
season and year (db.py), and norm_company does not know that "Regions" and
"Regions Bank" are one employer.

This module answers a narrower question that can be answered *definitively*:
"do these two rows point at the same application?" When the apply URL is a
recognisable ATS requisition or a LinkedIn job id, the answer is provable
from the URL alone -- no similarity, no thresholds, no judgement.

RELATIONSHIP TO web/lib/dupes.ts: canonicalTargetKey there has two halves.
The definitive half (LinkedIn / Workday / Greenhouse / Lever / Ashby /
Jobvite / SmartRecruiters / iCIMS) is ported here verbatim. The `url:` and
`row:` fallbacks are NOT, and that is deliberate on both counts:

  - dupes.ts itself does not trust them: groupNearDuplicates only accepts a
    `url:` collision when it ALSO passes guardedMetadataMatch.
  - Returning None instead means target_key stores "a proven target or
    nothing", so the SQL sibling check is trivially safe -- `= NULL` never
    matches, and a row we cannot identify can never suppress another row's
    notification.

All 400+ lines of fuzzy near-duplicate matching stay in TypeScript, used for
display only. Nothing here may drift from it: both languages are asserted
against the same fixtures/canonical_target_keys.json, so changing one
without the other turns a test red in both suites.
"""

import re
from urllib.parse import unquote, urlsplit

# Compiled once. These mirror the regexes in dupes.ts canonicalTargetKey; keep
# them in the same order as that function so the two read side by side.
_LINKEDIN_ID = re.compile(r"/jobs/view/(?:.*?-)?(\d{7,})(?:/|$)", re.I)
_WORKDAY_HOST = re.compile(r"\.myworkdayjobs\.com$")
_WORKDAY_REQ = re.compile(r"_((?:JR|R)[-_]?[A-Z0-9-]+)$", re.I)
_WORKDAY_COMPACT = re.compile(r"^((?:jr|r)\d{4,})-\d+$")
_GREENHOUSE_PATH = re.compile(r"/([^/]+)/jobs/(\d+)", re.I)
_LEVER_ID = re.compile(r"/([0-9a-f]{8}-[0-9a-f-]{27,})(?:/apply)?$", re.I)
_ASHBY = re.compile(r"^/([^/]+)/([0-9a-f]{8}-[0-9a-f-]{27,})$", re.I)
_JOBVITE = re.compile(r"/job/([a-z0-9_-]+)", re.I)
_SMARTRECRUITERS = re.compile(r"/([0-9]{10,})(?:/|$)")
_ICIMS = re.compile(r"/jobs/(\d+)(?:/|$)", re.I)


def application_href(job: dict) -> str:
    """The URL the user would actually apply through.

    Mirrors dupes.ts applicationHref EXACTLY, and that exactness is the whole
    mechanism: main.py fetches a LinkedIn posting's external apply_url before
    insert_job, so a LinkedIn row for a Workday req resolves to the same
    workday: key as the ats: and gh: rows for that req. Prefer apply_url,
    except for Easy Apply where the application really does happen on
    LinkedIn and job["url"] is the target.
    """
    if job.get("is_easy_apply"):
        return job.get("url") or ""
    return job.get("apply_url") or job.get("url") or ""


def _workday_tenant(host: str) -> str:
    return re.sub(r"[^a-z0-9]", "", host.split(".")[0])


def _normalized_workday_req(raw: str) -> str:
    """Workday appends a generated "-1" to a re-posted req; same job."""
    compact = raw.lower()
    if compact.startswith("jr-"):
        compact = "jr" + compact[3:]
    elif compact.startswith("r-"):
        compact = "r" + compact[2:]
    return _WORKDAY_COMPACT.sub(r"\1", compact)


def definitive_target_key(job: dict) -> str | None:
    """A provable identity for this job's application target, or None.

    None is not a failure mode, it is the honest answer for a URL we cannot
    positively identify -- an unrecognised careers page, a redirect shortener,
    a malformed href. Callers must treat None as "no cross-source claim",
    never as "matches other unidentifiable rows".
    """
    raw = (application_href(job) or "").strip()
    if not raw:
        return None

    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if not host:
            return None
        path = unquote(parts.path).rstrip("/") or "/"
    except Exception:
        # A malformed URL is not evidence that two rows are the same.
        return None

    m = _LINKEDIN_ID.search(path)
    if host.endswith("linkedin.com") and m:
        return f"linkedin:{m.group(1)}"

    if _WORKDAY_HOST.search(host):
        m = _WORKDAY_REQ.search(path)
        if m:
            return f"workday:{_workday_tenant(host)}:{_normalized_workday_req(m.group(1))}"

    if "greenhouse.io" in host:
        m = _GREENHOUSE_PATH.search(path)
        if m:
            return f"greenhouse:{m.group(1).lower()}:{m.group(2)}"
        # dupes.ts also accepts the ?gh_jid= form, keyed by host because the
        # board token is not in the path there.
        for key, value in _query_pairs(parts.query):
            if key == "gh_jid" and value:
                return f"greenhouse:{host}:{value}"

    m = _LEVER_ID.search(path)
    if host.endswith("lever.co") and m:
        return f"lever:{m.group(1).lower()}"

    m = _ASHBY.match(path)
    if host.endswith("ashbyhq.com") and m:
        return f"ashby:{m.group(1).lower()}:{m.group(2).lower()}"

    m = _JOBVITE.search(path)
    if "jobvite.com" in host and m:
        return f"jobvite:{m.group(1).lower()}"

    m = _SMARTRECRUITERS.search(path)
    if host.endswith("smartrecruiters.com") and m:
        return f"smartrecruiters:{m.group(1)}"

    m = _ICIMS.search(path)
    if "icims.com" in host and m:
        return f"icims:{host.split('.')[0]}:{m.group(1)}"

    # Everything dupes.ts would answer with `url:` or `row:`. Not definitive,
    # so this module declines to answer rather than inventing an identity.
    return None


def _query_pairs(query: str):
    for chunk in query.split("&"):
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        yield unquote(key).lower(), unquote(value)
