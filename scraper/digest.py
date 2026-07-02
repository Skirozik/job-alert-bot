"""Phase 2: twice-daily email digest of APPLY/MAYBE jobs, via Resend.

Run from the scraper directory (or on its own GitHub Actions schedule):
    cd scraper && python digest.py

Tracks the last successful send in a `bot_state` row so each digest only
covers jobs found since the previous one (falls back to a 24h lookback if
no prior send is recorded — e.g. the first run after enabling this).
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# See main.py for why: Windows console encoding can't print emoji/non-ASCII job data.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config import RESEND_API_KEY, RESEND_FROM, ALERT_EMAIL
from db import get_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

STATE_KEY = "last_digest_sent_at"
DEFAULT_LOOKBACK_HOURS = 24
_TIER_EMOJI = {"APPLY": "🟢", "MAYBE": "🟡"}


def _get_last_sent_at() -> str:
    client = get_client()
    try:
        result = client.table("bot_state").select("value").eq("key", STATE_KEY).execute()
        if result.data:
            return result.data[0]["value"]
    except Exception as exc:
        log.warning("bot_state table unavailable (%s) — using %dh lookback. See README to add it.",
                    exc, DEFAULT_LOOKBACK_HOURS)
    return (datetime.now(timezone.utc) - timedelta(hours=DEFAULT_LOOKBACK_HOURS)).isoformat()


def _set_last_sent_at(ts: str) -> None:
    client = get_client()
    try:
        client.table("bot_state").upsert({"key": STATE_KEY, "value": ts}, on_conflict="key").execute()
    except Exception as exc:
        log.error("Failed to record digest send time: %s", exc)


def _fetch_jobs_since(since: str) -> list[dict]:
    client = get_client()
    result = (
        client.table("jobs")
        .select("title, company, location, url, apply_url, tier, reason, salary, is_easy_apply")
        .in_("tier", ["APPLY", "MAYBE"])
        .gte("found_at", since)
        .order("tier")
        .execute()
    )
    return result.data or []


def _render_html(jobs: list[dict]) -> str:
    rows = []
    for job in jobs:
        emoji = _TIER_EMOJI.get(job["tier"], "🟡")
        link = job.get("apply_url") or job.get("url") or "#"
        salary = f' · <span style="color:#16a34a">{job["salary"]}</span>' if job.get("salary") else ""
        reason = f'<div style="color:#6b7280;font-size:13px;margin-top:2px">{job.get("reason", "")}</div>' if job.get("reason") else ""
        rows.append(f"""
        <div style="padding:12px 0;border-bottom:1px solid #e5e7eb">
          <div>{emoji} <strong>{job["tier"]}</strong> — <a href="{link}">{job["title"]}</a></div>
          <div style="color:#374151;font-size:14px">{job["company"]}{f' · {job["location"]}' if job.get("location") else ""}{salary}</div>
          {reason}
        </div>""")
    body = "".join(rows) if rows else "<p>No new APPLY/MAYBE jobs this period.</p>"
    return f"""<html><body style="font-family:sans-serif;max-width:600px;margin:0 auto">
    <h2>Job Digest — {len(jobs)} new match{"es" if len(jobs) != 1 else ""}</h2>
    {body}
    </body></html>"""


def _send_email(html: str, count: int) -> bool:
    if not RESEND_API_KEY or not RESEND_FROM or not ALERT_EMAIL:
        log.warning("RESEND_API_KEY/RESEND_FROM/ALERT_EMAIL not fully set — skipping digest send")
        return False
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={
                "from": RESEND_FROM,
                "to": [ALERT_EMAIL],
                "subject": f"Job Digest — {count} new match{'es' if count != 1 else ''}",
                "html": html,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.error("Digest email send failed: %s", exc)
        return False


def run():
    now = datetime.now(timezone.utc).isoformat()
    since = _get_last_sent_at()
    log.info("Building digest for jobs found since %s", since)

    jobs = _fetch_jobs_since(since)
    log.info("Found %d APPLY/MAYBE jobs to include", len(jobs))

    if not jobs:
        log.info("Nothing new — skipping send, still advancing the watermark")
        _set_last_sent_at(now)
        return

    html = _render_html(jobs)
    if _send_email(html, len(jobs)):
        log.info("Digest sent to %s", ALERT_EMAIL)
        _set_last_sent_at(now)
    else:
        log.warning("Digest not sent — watermark left unchanged so this period is retried next run")


if __name__ == "__main__":
    run()
