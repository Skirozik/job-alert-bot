"""Simplify-assisted autofill — same end goal as autofill.py (fill a job
application, stop before Submit, optionally mark applied) but driving the
Simplify Copilot Chrome extension's own "Autofill this page" button instead
of our own field-by-field platform fillers in platforms/.

Kept alongside autofill.py, not replacing it:
  - This path works on whatever ATS platforms Simplify itself supports (not
    just the ones with a fillers/ module here) and benefits from Simplify's
    own resume parsing + AI question-answering.
  - autofill.py's native fillers have no dependency on a third-party
    extension's behavior/availability/login state, and give us full control
    over what gets typed into EEO/demographic fields (see field_matcher.py's
    hard rule: route only, never author an answer).

Usage (from the scraper directory):
    python -m autofill.autofill_simplify <job_id>

Requires Simplify already installed + logged into the dedicated automation
Chrome profile (see browser.py) — this script does not install or
configure Simplify, only drives its already-authorized UI.
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
from autofill.browser import close_browser, launch_browser, has_visible_captcha_challenge, human_pause
from autofill.simplify_assist import is_simplify_active, trigger_autofill, wait_for_fill_to_settle
from autofill.advance import click_next_and_verify, find_next_button

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

_MAX_STEPS = 10  # hard ceiling so a verification bug can't loop forever


def run(job_id: str):
    client = get_client()
    rows = client.table("jobs").select("*").eq("id", job_id).execute().data
    if not rows:
        log.error("Job %s not found in the database.", job_id)
        return
    job = rows[0]

    if not job.get("apply_url"):
        log.error("Job %s has no apply_url — nothing to autofill.", job_id)
        return

    log.info("Job: %s @ %s", job["title"], job["company"])

    pw, context, page = launch_browser(allow_extensions=True)
    try:
        page.goto(job["apply_url"], wait_until="networkidle", timeout=30000)
        human_pause(2.0, 3.0)
        page.mouse.wheel(0, 800)  # scroll the form into view — confirmed live this is what gets Simplify to inject
        human_pause(1.5, 2.5)

        for step in range(1, _MAX_STEPS + 1):
            log.info("--- Step %d ---", step)

            if not is_simplify_active(page, timeout_ms=8000):
                log.warning(
                    "Simplify's autofill button isn't present on this step — it may not support "
                    "this platform/page. Stopping here; fill this step yourself (or trigger "
                    "Simplify by hand) in the open browser window."
                )
                break

            trigger_autofill(page)
            wait_for_fill_to_settle()
            log.info("Simplify's autofill settled.")

            if has_visible_captcha_challenge(page):
                log.warning("A visible CAPTCHA challenge appeared — solve it yourself in the browser window.")
                break

            next_btn = find_next_button(page)
            if next_btn is None:
                log.info(
                    "No Next/Continue button found — this is likely the final review/submit step. "
                    "Stopping here; review and submit yourself, never automated."
                )
                break

            advanced = click_next_and_verify(page)
            if not advanced:
                log.warning("Could not confirm the form advanced after clicking Next — stopping for you to check.")
                break

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
        close_browser(pw, context)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m autofill.autofill_simplify <job_id>")
        sys.exit(1)
    run(sys.argv[1])
