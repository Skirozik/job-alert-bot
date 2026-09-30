"""ntfy.sh push notifications for the Brice pipeline.

Rewrite of scraper_hassan/notifier.py. Two destinations that never cross:

  push_job          -> config.NTFY_TOPIC (Brice's phone): APPLY at high priority
                       (it sounds), APPLY_CAVEAT at low priority (silent).
  push_owner_alert  -> config.OWNER_NTFY_TOPIC (the owner's phone): every
                       infrastructure alert -- classifier down, zero results,
                       quota, crashes. It never falls back to NTFY_TOPIC: an
                       outage notice is not something to send the candidate.

The topic name IS the password on ntfy, and Actions logs are public, so
nothing here logs a topic, a request URL or an exception message (a
raise_for_status() message contains the URL). Failures are logged by exception
type and HTTP status only. The classifier's reason goes to the phone, never to
the log -- and a delivered ping is not logged at all: beside the job's
"Processing" line it would show which titles were actionable.
"""

import logging
import time

import requests

import config

log = logging.getLogger(__name__)

NTFY_BASE = "https://ntfy.sh"

_TIER_PRIORITY = {"APPLY": "high", "APPLY_CAVEAT": "low"}      # owner: APPLY sounds, APPLY_CAVEAT is silent
_TIER_TAGS = {"APPLY": "green_circle", "APPLY_CAVEAT": "warning"}
_TIER_EMOJI = {"APPLY": "🟢", "APPLY_CAVEAT": "🟡"}

# ntfy allows a burst of 60 messages, then 1 per 5 s per IP. One retry after a
# 429 covers a first run that pings everything it finds.
_RETRY_AFTER_429_S = 6


def _ascii(text: str) -> str:
    """HTTP header values are latin-1; emoji and other non-ASCII stay in the body."""
    return (text or "").encode("ascii", "replace").decode("ascii")


def _post(topic: str, body: str, headers: dict) -> bool:
    """POST one message. True on 2xx. One retry after HTTP 429; never raises."""
    for attempt in (1, 2):
        status = None
        try:
            resp = requests.post(f"{NTFY_BASE}/{topic}", data=body.encode("utf-8"), headers=headers, timeout=10)
            status = resp.status_code
            if status == 429 and attempt == 1:
                log.warning("ntfy rate limited (status=429) — retrying once in %ds", _RETRY_AFTER_429_S)
                time.sleep(_RETRY_AFTER_429_S)
                continue
            if 200 <= status < 300:
                return True
            log.error("ntfy post failed (%s, status=%s)", "HTTP", status)
            return False
        except Exception as exc:  # noqa: BLE001 -- a push must never take the run down
            log.error("ntfy post failed (%s, status=%s)", type(exc).__name__, status)
            return False
    return False


def push_job(job: dict) -> bool:
    """Ping Brice about one APPLY or APPLY_CAVEAT job. True if ntfy accepted it."""
    topic = config.NTFY_TOPIC
    if not topic:
        log.warning("NTFY_TOPIC not set - skipping push for job %s", job.get("id"))
        return False

    tier = job.get("tier", "APPLY_CAVEAT")
    emoji = _TIER_EMOJI.get(tier, "🟡")
    body_lines = [f"{emoji} {tier}", job.get("location", "")]
    if job.get("reason"):
        body_lines.append(f"Why: {job['reason']}")
    body = "\n".join(line for line in body_lines if line)
    headers = {
        "Title": _ascii(f"{job.get('company', '')} - {job.get('title', '')}"),
        "Priority": _TIER_PRIORITY.get(tier, "default"),
        "Tags": _TIER_TAGS.get(tier, "yellow_circle"),
        "Click": job.get("url", ""),
    }
    if _post(topic, body, headers):
        log.debug("Push sent for job %s", job.get("id"))
        return True
    return False


def push_owner_alert(message: str, *, title: str = "Brice pipeline alert",
                     priority: str = "urgent", tags: str = "warning,robot") -> bool:
    """Send an infrastructure alert to the OWNER's topic only. True if ntfy accepted it."""
    topic = config.OWNER_NTFY_TOPIC
    if not topic:
        log.warning("OWNER_NTFY_TOPIC not set — owner alert not sent: %s", title)
        return False
    headers = {"Title": _ascii(title), "Priority": priority, "Tags": tags}
    if _post(topic, message, headers):
        log.warning("Owner alert sent: %s", title)
        return True
    return False
