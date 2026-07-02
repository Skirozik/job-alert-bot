"""One-off/repeatable sync: mark jobs as 'applied' in Supabase based on the
manual tracker.csv (kept outside this repo, in the internship-2026 folder).

Cross-references every tracker row with Status == "Applied" against the
`jobs` table by two independent signals — since either one alone can miss a
real match — and updates status only (nothing else on the row is touched):
  1. Normalized apply URL (tracker Link vs jobs.url / jobs.apply_url)
  2. Normalized company+role key (jobs.norm_key), via the same norm_company/
     norm_role logic the scraper itself uses for dedup

Run from the scraper directory:
    cd scraper && python sync_applied_from_tracker.py
"""

import csv
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from db import get_client, make_norm_key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

TRACKER_CSV_PATH = Path(r"C:\Users\inyan\OneDrive\Desktop\internship-2026\tracker.csv")


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/")
    return f"{netloc}{path}"


def load_applied_tracker_rows() -> list[dict]:
    with open(TRACKER_CSV_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if (r.get("Status") or "").strip() == "Applied"]


def load_all_jobs() -> list[dict]:
    client = get_client()
    jobs = []
    page_size = 1000
    offset = 0
    while True:
        result = (
            client.table("jobs")
            .select("id, company, title, url, apply_url, norm_key, status")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = result.data or []
        jobs.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return jobs


def run():
    tracker_rows = load_applied_tracker_rows()
    log.info("tracker.csv: %d rows marked Applied", len(tracker_rows))

    jobs = load_all_jobs()
    log.info("Supabase jobs table: %d total rows", len(jobs))

    url_index: dict[str, dict] = {}
    norm_key_index: dict[str, dict] = {}
    for job in jobs:
        for u in (job.get("url"), job.get("apply_url")):
            nu = normalize_url(u)
            if nu:
                url_index.setdefault(nu, job)
        nk = job.get("norm_key")
        if nk:
            norm_key_index.setdefault(nk, job)

    updated = 0
    already_applied = 0
    unmatched = []

    for row in tracker_rows:
        company = row.get("Company", "")
        role = row.get("Role", "")
        link = row.get("Link", "")

        match = url_index.get(normalize_url(link))
        match_method = "url" if match else None
        if not match:
            nk = make_norm_key(company, role)
            match = norm_key_index.get(nk)
            match_method = "norm_key" if match else None

        if not match:
            unmatched.append(f"{company} | {role}")
            continue

        if match.get("status") == "applied":
            already_applied += 1
            continue

        client = get_client()
        try:
            client.table("jobs").update({"status": "applied"}).eq("id", match["id"]).execute()
            log.info("Marked applied (%s match): %s | %s [%s]", match_method, company, role, match["id"])
            updated += 1
        except Exception as exc:
            log.error("Failed to update %s | %s: %s", company, role, exc)

    log.info("=== Done: %d newly marked applied, %d already applied, %d no DB match ===",
              updated, already_applied, len(unmatched))
    if unmatched:
        log.info("--- Tracker 'Applied' rows with no match in the bot's DB (likely found outside it) ---")
        for entry in unmatched:
            log.info("  %s", entry)


if __name__ == "__main__":
    run()
