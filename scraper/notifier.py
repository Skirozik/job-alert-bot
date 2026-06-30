"""Push notifications (ntfy.sh) and canary alerts."""

import logging
import requests
from config import NTFY_TOPIC

log = logging.getLogger(__name__)

NTFY_BASE = "https://ntfy.sh"

_TIER_EMOJI = {"APPLY": "🟢", "MAYBE": "🟡"}
_TIER_TAGS = {"APPLY": "green_circle", "MAYBE": "yellow_circle"}
_TIER_PRIORITY = {"APPLY": "high", "MAYBE": "default"}


def push_job(job: dict) -> None:
    """Send a push notification for a single APPLY or MAYBE job."""
    if not NTFY_TOPIC:
        log.warning("NTFY_TOPIC not set — skipping push for job %s", job.get("id"))
        return

    tier = job.get("tier", "MAYBE")
    emoji = _TIER_EMOJI.get(tier, "🟡")
    title = f"{emoji} {job.get('company', 'Unknown')} — {job.get('title', 'Unknown')}"

    body_lines = [f"{emoji} {tier}", job.get("location", "")]
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
                "Title": f"{job.get('company', '')} - {job.get('title', '')}",
                "Priority": _TIER_PRIORITY.get(tier, "default"),
                "Tags": _TIER_TAGS.get(tier, "yellow_circle"),
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
