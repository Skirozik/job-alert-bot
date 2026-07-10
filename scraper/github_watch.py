"""Fast-path GitHub tracker watcher.

The main scan (main.py) covers both LinkedIn and the GitHub tracker repos
on the same 20-minute cycle. LinkedIn's own search doesn't reliably return
newest-first (confirmed live — see main.py's MAX_PAGES_PER_SEARCH comment),
so tightening that cycle mostly just raises LinkedIn request volume/block
risk without proportionally cutting real-world latency. The GitHub tracker
sources don't have that problem — each is a plain README fetch with no
rate limiting and no relevance-ranking delay, so the only latency source
for them is "how often do we check." This module checks far more often
(every couple minutes, via Modal — see modal_app.py) using each repo's own
commit Atom feed (a cheap, official, unauthenticated GitHub endpoint with
none of LinkedIn's block risk) instead of re-fetching and re-parsing the
full README every time. Only when a repo's latest commit id has actually
changed since the last check does it do real work: fetch, dedup, classify,
store, and notify — via process_job(), the exact same logic the main scan
uses, not a second copy of it.

Uses the same run-lock as the main scan (db.start_run/finish_run) so the
two can never process the same freshly-added job concurrently — a fast
check with nothing new to do finishes in a couple seconds, so this doesn't
meaningfully compete with the main scan's own cadence.
"""

import logging
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import requests

from github_sources import fetch_github_listings, _SOURCES
from db import get_client, load_dedup_index, make_norm_key, start_run, finish_run
from main import process_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

_STATE_KEY_PREFIX = "gh_watch_sha:"
_README_URL_RE = re.compile(r"raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/")


def _atom_url(readme_url: str) -> str | None:
    m = _README_URL_RE.search(readme_url)
    if not m:
        return None
    owner, repo, branch = m.group(1), m.group(2), m.group(3)
    return f"https://github.com/{owner}/{repo}/commits/{branch}.atom"


def _latest_commit_id(atom_url: str) -> str | None:
    try:
        resp = requests.get(atom_url, timeout=10)
        resp.raise_for_status()
        ids = re.findall(r"<id>(.*?)</id>", resp.text)
        return ids[1] if len(ids) > 1 else None
    except Exception as exc:
        log.warning("Commit feed check failed for %s: %s", atom_url, exc)
        return None


def _get_last_seen(client, source_name: str) -> str | None:
    try:
        result = client.table("bot_state").select("value").eq("key", _STATE_KEY_PREFIX + source_name).execute()
        return result.data[0]["value"] if result.data else None
    except Exception as exc:
        log.warning("bot_state read failed for %s (%s) — treating as changed", source_name, exc)
        return None


def _set_last_seen(client, source_name: str, commit_id: str) -> None:
    try:
        client.table("bot_state").upsert(
            {"key": _STATE_KEY_PREFIX + source_name, "value": commit_id}, on_conflict="key"
        ).execute()
    except Exception as exc:
        log.error("Failed to record last-seen commit for %s: %s", source_name, exc)


def run():
    client = get_client()

    changed = []
    for readme_url, source_name in _SOURCES:
        atom_url = _atom_url(readme_url)
        if atom_url is None:
            log.warning("Could not derive commit-feed URL for %s — skipping", source_name)
            continue
        latest = _latest_commit_id(atom_url)
        if latest is None:
            continue
        if _get_last_seen(client, source_name) != latest:
            changed.append(source_name)
        _set_last_seen(client, source_name, latest)

    if not changed:
        log.info("No new commits on any tracked GitHub source.")
        return

    log.info("New commit(s) detected: %s — processing immediately", ", ".join(changed))

    run_id = start_run()
    if run_id is None:
        log.warning("Main scan appears to be in progress — skipping this fast-check pass "
                     "(it'll pick up the same commit(s) on its own next cycle regardless).")
        return

    notified = 0
    new_jobs = 0
    try:
        known_ids, known_norm_keys = load_dedup_index()
        for j in fetch_github_listings():
            nk = make_norm_key(j["company"], j["title"])
            if j["id"] in known_ids or nk in known_norm_keys:
                continue
            known_ids.add(j["id"])
            known_norm_keys.add(nk)
            j["norm_key"] = nk
            new_jobs += 1
            if process_job(j):
                notified += 1
    finally:
        finish_run(run_id, total_raw=0, new_jobs=new_jobs, rate_limited=0, notified=notified)

    log.info("=== GitHub fast-check complete: %d new, %d notified ===", new_jobs, notified)


if __name__ == "__main__":
    run()
