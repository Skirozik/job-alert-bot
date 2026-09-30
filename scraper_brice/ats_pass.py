"""ATS pass for the Brice pipeline: 26 company career boards, listing fields only.

collect_ats_candidates() sweeps ats_boards.ATS_BOARDS through the vendored
ats_sources.fetch_all_listings() -- the public Greenhouse, Ashby and Workday
APIs, 12 boards at a time -- and keeps only the rows that pass
title_gate.source_gate(): a U.S. (or unknown) location, an allowed role family
or an early-career program title, then the entry-level title gate. It opens no
database, calls no Claude and fetches nothing per job, so everything a row is
judged on here is its title, company, location and URL.

fetch_workday_description() is this pass's only per-job request, and main.py
calls it only for a kept row that lacks a description, after the
already-stored guard: one GET to Workday's CXS job endpoint, refused for any
other host. There is deliberately no generic HTML fallback -- nothing in this
fork fetches an arbitrary page, and never jobright.ai. A Workday row whose
fetch fails is classified on its title, and the classifier's title-only cap
keeps it off a loud APPLY. Greenhouse and Ashby rows arrive with their
descriptions (see ats_sources.py).

Never filter on posted_at: Workday's postedOn resets when a posting is re-posted.
"""

import logging
import re
import time
from collections import Counter
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import ats_sources
import families
import title_gate
from ats_boards import ATS_BOARDS
from db import make_norm_key   # a pure function: importing it creates no client

log = logging.getLogger(__name__)

DUPLICATE_RULE = "duplicate within the sweep"   # another copy of the same company + title (or id) was kept
SAMPLES_PER_RULE = 40                           # dropped titles kept per rule, for the dry-run report


def collect_ats_candidates(boards: Optional[dict] = None) -> tuple[list[dict], dict]:
    """Fetch every board and keep only the rows that pass source_gate. No DB, no Claude, no per-job fetch.

    boards defaults to ats_boards.ATS_BOARDS ({company: {"platform", "token"}}).

    Returns (candidates, stats). A candidate is the listing as ats_sources built it (id
    "ats:<sha1(url)[:16]>", title, company, location, url, apply_url, posted_at, description -- None on
    Workday) plus source="ats", family=<source_gate label>, search_term="ats:<company>" and
    norm_key=db.make_norm_key(company, title). One candidate per norm_key: a board lists one title once per
    city, and the database keeps one row per company + title, so the sweep keeps the first copy whose
    location is a confirmed U.S. one (families.is_us is True), else the first copy.

    stats: boards, boards_with_listings, listings, kept, dropped_by (Counter of rules; listings == kept +
    sum(dropped_by)), dropped_samples ({rule: ["company | title | location", ...]}), kept_by_family,
    by_board ({company: {"platform", "listings", "kept"}}) and empty_boards (boards that returned no
    listing: an ATS error, a bad token or an empty board -- ats_sources logs which).
    """
    boards = ATS_BOARDS if boards is None else boards
    started = time.monotonic()
    try:
        listings = ats_sources.fetch_all_listings(boards)
    except Exception as exc:  # noqa: BLE001 -- every fetcher is fail-soft; this guards the sweep itself
        # An empty sweep, not a crashed run: LinkedIn and jobright still run, and main alerts the owner
        # that no board returned anything.
        log.error("ATS sweep failed (%s) — no board read this run", type(exc).__name__)
        listings = []
    # fetch_all_listings returns boards in completion order. Board order instead, so which copy of a
    # posting wins and the order of the run's queue do not depend on which host answered first; the sort
    # is stable, so each board keeps its own order.
    rank = {company: i for i, company in enumerate(boards)}
    listings = sorted(listings, key=lambda job: rank.get(job.get("company"), len(rank)))

    by_board = {company: {"platform": cfg.get("platform", "?"), "listings": 0, "kept": 0}
                for company, cfg in boards.items()}
    dropped_by: Counter = Counter()
    dropped_samples: dict[str, list[str]] = {}

    def drop(rule: str, job: dict) -> None:
        dropped_by[rule] += 1
        samples = dropped_samples.setdefault(rule, [])
        if len(samples) < SAMPLES_PER_RULE:
            samples.append(f"{job.get('company') or ''} | {job.get('title') or ''} | {job.get('location') or ''}")

    passed = []      # (job, label, norm_key, U.S.-confirmed) for each row the gate keeps, in board order
    for job in listings:
        company = job.get("company") or ""
        title = job.get("title") or ""
        location, url = job.get("location") or "", job.get("url") or ""
        by_board.setdefault(company, {"platform": "?", "listings": 0, "kept": 0})["listings"] += 1
        keep, label = title_gate.source_gate(title, company, location, url, program_passthrough=True)
        if keep:
            passed.append((job, label, make_norm_key(company, title), families.is_us(location, url, title) is True))
        else:
            drop(label, job)

    # One copy per posting. Which copy matters: the ping links it and Claude judges its location. HPE, for
    # one, lists "Cloud Engineer Graduate" in Puerto Rico (is_us: unknown) ahead of its ten-city U.S. copy.
    chosen: dict[str, int] = {}
    for index, (_job, _label, nk, us) in enumerate(passed):
        if nk not in chosen or (us and not passed[chosen[nk]][3]):
            chosen[nk] = index
    winners = set(chosen.values())

    kept_by_family: Counter = Counter()
    candidates: list[dict] = []
    seen_ids: set[str] = set()
    for index, (job, label, nk, _us) in enumerate(passed):
        if index not in winners or job["id"] in seen_ids:
            drop(DUPLICATE_RULE, job)
            continue
        seen_ids.add(job["id"])
        company = job.get("company") or ""
        job.update(source="ats", family=label, search_term=f"ats:{company}", norm_key=nk)
        candidates.append(job)
        by_board[company]["kept"] += 1
        kept_by_family[label] += 1

    empty = [company for company, b in by_board.items() if not b["listings"]]
    stats = {
        "boards": len(boards),
        "boards_with_listings": sum(1 for b in by_board.values() if b["listings"]),
        "listings": len(listings),
        "kept": len(candidates),
        "dropped_by": dropped_by,
        "dropped_samples": dropped_samples,
        "kept_by_family": kept_by_family,
        "by_board": by_board,
        "empty_boards": empty,
    }
    log.info("ATS sweep: %d listings from %d/%d boards in %.0f s | kept %d (%s)", stats["listings"],
             stats["boards_with_listings"], stats["boards"], time.monotonic() - started, len(candidates),
             ", ".join(f"{f} {n}" for f, n in kept_by_family.most_common()) or "none")
    if candidates:
        log.info("ATS kept per board: %s", ", ".join(f"{c} {b['kept']}" for c, b in by_board.items() if b["kept"]))
    if empty:
        log.warning("ATS boards with no listings this run (an error or an empty board): %s", ", ".join(empty))
    return candidates, stats


# ── Workday descriptions ─────────────────────────────────────────────────────
# Vendored from scraper/external_descriptions.py (HEADERS, MAX_LEN, _html_to_text, _fetch_workday) so this
# fork stays self-contained. This fork's own additions: the host guard (nothing but a Workday host is ever
# requested), the CXS URL is built from the checked hostname, and every failure returns None here.

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}
MAX_LEN = 12000                          # the main pipeline's description cap
TIMEOUT = 15
WORKDAY_HOST_SUFFIX = ".myworkdayjobs.com"
_LOCALE_RE = re.compile(r"^[a-zA-Z]{2}(-[a-zA-Z]{2})?$")


def _html_to_text(raw_html: str) -> str:
    return BeautifulSoup(raw_html, "lxml").get_text(separator=" ", strip=True)[:MAX_LEN]


def workday_host(url: Optional[str]) -> Optional[str]:
    """The URL's hostname if it is a Workday job board (*.myworkdayjobs.com over http/https), else None."""
    try:
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not host.endswith(WORKDAY_HOST_SUFFIX):
        return None
    return host


def fetch_workday_description(url: str) -> Optional[str]:
    """One GET to Workday's CXS job endpoint for a posting URL; the description as plain text, or None.

    Returns None -- without any request -- unless the host ends with '.myworkdayjobs.com' and the path has
    a /job/ segment, and None on any failure. A posting URL is
    https://<tenant>.<wdN>.myworkdayjobs.com/[<locale>/]<site>/job/<location>/<slug>; the CXS endpoint is
    https://<same host>/wday/cxs/<tenant>/<site>/job/<location>/<slug>, whose JSON carries the HTML in
    jobPostingInfo.jobDescription. With a leading locale segment the site is tried with and without it.
    """
    host = workday_host(url)
    if host is None:
        return None
    path = urlparse(url).path.strip("/")
    if "/job/" not in path:
        return None
    tenant = host.split(".")[0]
    pre_job, job_path = path.split("/job/", 1)
    sites = [pre_job]
    segments = pre_job.split("/")
    if len(segments) > 1 and _LOCALE_RE.match(segments[0]):
        sites.append("/".join(segments[1:]))      # drop a leading locale segment ("en-US/<site>")

    status = None
    try:
        for site in sites:
            resp = requests.get(f"https://{host}/wday/cxs/{tenant}/{site}/job/{job_path}",
                                headers=HEADERS, timeout=TIMEOUT)
            status = resp.status_code
            if not resp.ok:
                continue
            info = resp.json().get("jobPostingInfo") or {}
            desc_html = info.get("jobDescription") or ""
            if desc_html:
                return _html_to_text(desc_html) or None
    except Exception as exc:  # noqa: BLE001 -- best effort: the row is classified on its title instead
        log.info("  Workday description fetch failed (%s)", type(exc).__name__)
        return None
    if status is not None and status >= 400:
        log.info("  Workday description fetch failed (HTTP %s)", status)
    return None
