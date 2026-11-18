"""Push notifications (ntfy.sh) and canary alerts."""

import logging
import re
from urllib.parse import urlparse

import requests
from ats_config import ATS_COMPANIES
from config import NTFY_TOPIC
from gold_star import is_starred, reason_summary

log = logging.getLogger(__name__)

NTFY_BASE = "https://ntfy.sh"

_GH_JID = re.compile(r"[?&]gh_jid=(\d+)")
_PAGE_CHECK_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
}


def greenhouse_form_url(job: dict) -> str | None:
    """Greenhouse's own page for a posting whose link goes to the company's careers site, else None.

    39 of the 84 Greenhouse boards (Airbnb, Stripe, Databricks, Jane Street, ...) hand out a link
    on the company's own site, carrying ?gh_jid=<Greenhouse id>. The company's site builds those
    pages on its own schedule, so a posting the watcher finds within a minute can still 404 there:
    Airbnb's Software Engineering Intern was listed by Greenhouse at 05:00 UTC on 2026-11-18 and
    its careers page was still "Oops!" minutes later. Greenhouse's embedded form is live as soon as
    the board lists the posting, with the full description and the apply button.
    """
    cfg = ATS_COMPANIES.get(job.get("company") or "")
    if not cfg or cfg.get("platform") != "greenhouse":
        return None
    for link in (job.get("url"), job.get("apply_url")):
        m = _GH_JID.search(link or "")
        if m and not urlparse(link).netloc.endswith("greenhouse.io"):
            return f"https://job-boards.greenhouse.io/embed/job_app?for={cfg['token']}&token={m.group(1)}"
    return None


def _page_missing(url: str) -> bool:
    """True only when the page answers 404 or 410. Anything else, a block or a timeout included,
    counts as there: the alert then keeps the company's link and offers the form as a button."""
    try:
        resp = requests.get(url, headers=_PAGE_CHECK_HEADERS, timeout=6, allow_redirects=True, stream=True)
        resp.close()
        return resp.status_code in (404, 410)
    except Exception:
        return False

_TIER_EMOJI = {"APPLY": "🟢", "APPLY_CAVEAT": "🟢"}
_TIER_TAGS = {"APPLY": "green_circle", "APPLY_CAVEAT": "warning"}
# APPLY_CAVEAT is high priority too. It is on the same list as APPLY and is
# meant to reach him the same way — the caveat is context, not a downgrade.
# Before this it fell through to "default", which ntfy delivers silently:
# the push was recorded as sent and never alerted the phone.
_TIER_PRIORITY = {"APPLY": "high", "APPLY_CAVEAT": "high"}


def push_job(job: dict, title_prefix: str = "") -> bool:
    """Send a push notification for a single APPLY or APPLY_CAVEAT job. True when ntfy took it.
    title_prefix marks a test push ("DRILL - ", from apple_watch.drill)."""
    if not NTFY_TOPIC:
        log.warning("NTFY_TOPIC not set — skipping push for job %s", job.get("id"))
        return False

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

    # Tapping the alert opens the posting. When that posting is on a company site that has not
    # built its page yet, open Greenhouse's form instead and keep the company page as a button.
    click = job.get("url", "")
    actions = None
    form = greenhouse_form_url(job)
    if form:
        # Only the company's own page is checked (it carries gh_jid); a LinkedIn link is left alone.
        if _GH_JID.search(click) and _page_missing(click):
            body_lines.append("Opens Greenhouse's application page (the company's page isn't up yet)")
            actions = f"view, Company page, {click}"
            click = form
        else:
            body_lines.append(f"If the page 404s, apply here: {form}")
            actions = f"view, Greenhouse form, {form}"
    body = "\n".join(line for line in body_lines if line)

    try:
        # Post to topic URL; emoji stays in the body (UTF-8), not the Title header (latin-1)
        resp = requests.post(
            f"{NTFY_BASE}/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={
                # "GOLD" as a WORD, never the emoji: this is ascii-encoded
                # with errors="replace", so a star here would arrive as "?".
                "Title": (title_prefix + ("GOLD - " if starred else "")
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
                "Click": click,
                **({"Actions": actions} if actions else {}),
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Push sent: %s", title)
        return True
    except Exception as exc:
        log.error("ntfy push failed for job %s: %s", job.get("id"), exc)
        return False


def push_apple(title: str, body: str, url: str, drill: bool = False) -> str | None:
    """The Apple push: "🍎 APPLE - <title>", at ntfy's top priority, opening the posting.
    Returns ntfy's message id when ntfy took it (apple_watch.drill reads it back), else None.

    Published as JSON rather than headers, so the title can carry the 🍎 itself: the Title header in
    push_job is ascii-encoded, which is why emoji there arrive as "?". Top priority is shared with
    push_canary on purpose -- a new Apple role is as rare as a broken scraper, and as urgent.
    """
    return _push_top("🍎", "Apple", title, body, url, drill)


def push_google(title: str, body: str, url: str, drill: bool = False) -> str | None:
    """The Google push (google_watch): "🔎 GOOGLE - <title>", sent the same way as push_apple."""
    return _push_top("🔎", "Google", title, body, url, drill)


def _push_top(emoji: str, company: str, title: str, body: str, url: str, drill: bool) -> str | None:
    if not NTFY_TOPIC:
        log.warning("NTFY_TOPIC not set — skipping %s push", company)
        return None
    name = company.upper()
    payload = {
        "topic": NTFY_TOPIC,
        "title": (f"{emoji} DRILL - {name} - " if drill else f"{emoji} {name} - ") + title,
        "message": body,
        "priority": 5,
        "tags": ["drill"] if drill else [],
        "click": url,
    }
    try:
        resp = requests.post(NTFY_BASE, json=payload, timeout=10)
        resp.raise_for_status()
        msg_id = (resp.json() or {}).get("id") or "sent"
        log.info("%s push sent%s: %s", company, " (drill)" if drill else "", title)
        return msg_id
    except Exception as exc:
        log.error("%s push failed (%s): %s", company, title, exc)
        return None


def push_canary(message: str) -> bool:
    """Send a canary/warning alert — used when the scraper looks broken.
    True only when ntfy took it: apple_watch marks an alarm sent only then, so
    a lost alarm is tried again on the next pass instead of silencing the outage."""
    if not NTFY_TOPIC:
        return False
    try:
        resp = requests.post(
            f"{NTFY_BASE}/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": "Job scraper alert",
                "Priority": "urgent",
                "Tags": "warning,robot",
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.warning("Canary sent: %s", message)
        return True
    except Exception as exc:
        log.error("Canary push failed: %s", exc)
        return False
