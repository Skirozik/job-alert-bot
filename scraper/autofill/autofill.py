"""Autofill a job application — fills what it can, always stops before the
final Submit/Apply button, leaves you to review and submit yourself.

Usage (from the scraper directory):
    python -m autofill.autofill <job_id>

Supported platforms so far: Greenhouse. Others (Lever, Ashby, Workday,
iCIMS) fall outside this first phase — see the plan this was built from
for the intended build order. LinkedIn Easy Apply is deliberately out of
scope entirely (see plan: account-suspension risk, zero current jobs use it).
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import get_client
from autofill.profile_loader import load_profile, ProfileError
from autofill.browser import launch_browser, has_visible_captcha_challenge
from autofill.platforms.dispatch import detect_platform, get_filler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def run(job_id: str):
    try:
        profile = load_profile()
    except ProfileError as exc:
        log.error(str(exc))
        return

    client = get_client()
    rows = client.table("jobs").select("*").eq("id", job_id).execute().data
    if not rows:
        log.error("Job %s not found in the database.", job_id)
        return
    job = rows[0]

    if not job.get("apply_url"):
        log.error("Job %s has no apply_url — nothing to autofill.", job_id)
        return

    platform = detect_platform(job["apply_url"])
    filler = get_filler(platform)
    if filler is None:
        log.error(
            "Platform '%s' isn't supported yet (only Greenhouse is built so far). "
            "Apply_url: %s", platform, job["apply_url"],
        )
        return

    log.info("Job: %s @ %s [%s]", job["title"], job["company"], platform)
    log.info("Resume: %s", job.get("suggested_resume", "General"))

    pw, context, page = launch_browser()
    try:
        report = filler(page, job, profile)

        log.info("=== Filled ===")
        for item in report["filled"]:
            log.info("  %s", item)

        if report["unmapped"]:
            log.warning("=== NOT filled — needs your input in the browser ===")
            for item in report["unmapped"]:
                log.warning("  %s", item)
        else:
            log.info("Nothing unmapped — every field the form asked for was filled.")

        if has_visible_captcha_challenge(page):
            log.warning("A visible CAPTCHA challenge appeared — solve it yourself in the browser window.")

        if report["submit_button_text"]:
            log.info(
                "Found a '%s' button — NOT clicking it. Review the form yourself and submit "
                "when ready.", report["submit_button_text"],
            )
        else:
            log.info("No submit button located on this page yet (may be further down after unmapped fields).")

        log.info("Browser will stay open. Press Enter here once you've reviewed/submitted (or Ctrl+C to just close).")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass

        answer = input("Did you actually submit this application? [y/N]: ").strip().lower()
        if answer == "y":
            client.table("jobs").update({"status": "applied"}).eq("id", job_id).execute()
            log.info("Marked as applied in the dashboard.")

    finally:
        context.close()
        pw.stop()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m autofill.autofill <job_id>")
        sys.exit(1)
    run(sys.argv[1])
