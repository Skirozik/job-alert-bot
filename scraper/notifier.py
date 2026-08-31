"""Push notifications (ntfy.sh) and canary alerts."""

import logging
import requests
from config import NTFY_TOPIC
from gold_star import is_starred, reason_summary

log = logging.getLogger(__name__)

NTFY_BASE = "https://ntfy.sh"

_TIER_EMOJI = {"APPLY": "🟢", "APPLY_CAVEAT": "🟢"}
_TIER_TAGS = {"APPLY": "green_circle", "APPLY_CAVEAT": "warning"}
# APPLY_CAVEAT is high priority too. It is on the same list as APPLY and is
# meant to reach him the same way — the caveat is context, not a downgrade.
# Before this it fell through to "default", which ntfy delivers silently:
# the push was recorded as sent and never alerted the phone.
_TIER_PRIORITY = {"APPLY": "high", "APPLY_CAVEAT": "high"}


def push_job(job: dict) -> None:
    """Send a push notification for a single APPLY or APPLY_CAVEAT job."""
    if not NTFY_TOPIC:
        log.warning("NTFY_TOPIC not set — skipping push for job %s", job.get("id"))
        return

    tier = job.get("tier", "APPLY_CAVEAT")
    emoji = _TIER_EMOJI.get(tier, "🟡")
    title = f"{emoji} {job.get('company', 'Unknown')} — {job.get('title', 'Unknown')}"

    # Gold star: this one is worth a hand-written resume rather than a variant.
    # Derived here from the job dict that is already in hand -- no column, no
    # query, no classifier call. See gold_star.py for the rule.
    starred = is_starred(job)

    body_lines = []
    if starred:
        # Emoji belong in the BODY only. The Title header below is encoded
        # latin-1/ascii, so a star there arrives as "?" -- see the header note.
        body_lines.append("⭐ GOLD — write a custom resume for this one")
        summary = reason_summary(job)
        if summary:
            body_lines.append(summary)
    body_lines += [f"{emoji} {tier}", job.get("location", "")]
    if job.get("reason"):
        body_lines.append(f"Why: {job['reason']}")
    if job.get("suggested_resume"):
        body_lines.append(f"Resume: {job['suggested_resume']}")
    body = "\n".join(line for line in body_lines if line)

    try:
        # Post to topic URL; emoji stays in the body (UTF-8), not the Title header (latin-1)
        resp = requests.post(
            f"{NTFY_BASE}/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={
                # "GOLD" as a WORD, never the emoji: this is ascii-encoded
                # with errors="replace", so a star here would arrive as "?".
                "Title": (("GOLD - " if starred else "")
                          + f"{job.get('company', '')} - {job.get('title', '')}"
                          ).encode("ascii", "replace").decode("ascii"),
                # Priority deliberately unchanged. "urgent" is reserved for the
                # scraper-broken canary in push_canary; if starred jobs also
                # went urgent, the alert that means "your pipeline is down"
                # would stop standing out, which is the more expensive failure.
                "Priority": _TIER_PRIORITY.get(tier, "default"),
                # ntfy renders the "star" shortcode as a real star in the
                # notification list, so it is scannable without opening it.
                "Tags": ("star," if starred else "") + _TIER_TAGS.get(tier, "yellow_circle"),
                "Click": job.get("url", ""),
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Push sent: %s", title)
    except Exception as exc:
        log.error("ntfy push failed for job %s: %s", job.get("id"), exc)


def push_canary(message: str) -> None:
    """Send a canary/warning alert — used when the scraper looks broken."""
    if not NTFY_TOPIC:
        return
    try:
        requests.post(
            f"{NTFY_BASE}/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": "Job scraper alert",
                "Priority": "urgent",
                "Tags": "warning,robot",
            },
            timeout=10,
        )
        log.warning("Canary sent: %s", message)
    except Exception as exc:
        log.error("Canary push failed: %s", exc)
