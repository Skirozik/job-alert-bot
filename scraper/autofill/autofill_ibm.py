"""IBM (Avature) autofill — fills the multi-step application wizard and stops
before Submit, same contract as autofill.py and autofill_simplify.py.

Usage (from the scraper directory):
    python -m autofill.autofill_ibm <jobId>
    python -m autofill.autofill_ibm <jobId> --dry-run
    python -m autofill.autofill_ibm <jobId> --no-advance
    python -m autofill.autofill_ibm <jobId> --resume AI

Takes the raw IBM jobId straight off the posting URL rather than a database
id, because there is no IBM source in ats_config.py and therefore no IBM row
to look up. Consequence, accepted deliberately: IBM applications are not
tracked on the dashboard, so there is no "mark applied" step at the end the
way autofill.py has one.

Kept separate from autofill.py rather than folded into it:
  - autofill.py resolves a job through the database and hands off to ONE
    filler call. IBM needs a step loop, because the wizard spans 5 screens.
  - The step loop belongs in the orchestrator, not the filler, which is the
    division of labour autofill_simplify.py already established: the filler
    fills what is on screen, the orchestrator decides whether to advance.

Requires a logged-in IBM session in the persistent Chrome profile browser.py
manages. Direct navigation to JobApplication only works when authenticated —
if it lands on a login wall this stops immediately and says so, rather than
burning eight steps against a login page.
"""

import logging
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AUTOFILL_BROWSER_PROFILE_DIR
from autofill.browser import (
    close_browser, launch_browser, has_visible_captcha_challenge,
    human_pause, profile_in_use,
)
from autofill.profile_loader import load_profile, ProfileError, resolve_resume_path
from autofill.advance import click_next_and_verify, find_next_button
from autofill.platforms import ibm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

_APPLY_URL = "https://careers.ibm.com/en_US/careers/JobApplication?jobId={job_id}"
_MAX_STEPS = 8  # hard ceiling so a verification bug can't loop forever


def _is_terminal_page(page) -> bool:
    """The review page, detected by URL.

    Deliberately NOT by button text. The terminal page's buttons are
    Back / Submit Application / Cancel Application — and "Cancel Application"
    contains "Application" too, so a text match there is the same fragility
    class as the get_by_role(name=...) bug that had advance.py matching
    nothing for months. The URL is unambiguous.
    """
    try:
        return "jobapplicationsummary" in (page.url or "").lower()
    except Exception:
        return False


def _read_progress(page) -> str:
    """IBM's wizard shows a percentage. Used for LOGGING and as a fallback
    advance signal — never as a step counter.

    Observed 20% -> 40% -> 60% -> 100% on jobId 129258: there is no 80% step,
    and the number of steps varies by requisition. Nothing here may index off
    the percentage or assume how many steps remain.
    """
    try:
        text = page.evaluate(
            """() => {
                const bar = document.querySelector(
                  '[role="progressbar"], [class*="progress"], [id*="progress"]');
                if (!bar) return '';
                const v = bar.getAttribute('aria-valuenow');
                if (v) return v + '%';
                const m = (bar.innerText || '').match(/(\\d{1,3})\\s*%/);
                return m ? m[1] + '%' : '';
            }"""
        )
        return text or ""
    except Exception:
        return ""


def _looks_like_application_form(page) -> bool:
    """Are we actually on the wizard, or on a login wall?"""
    if _read_progress(page):
        return True
    try:
        if find_next_button(page) is not None:
            return True
        hits = page.evaluate(
            "(ids) => ids.filter(i => document.getElementById(i)).length",
            list(ibm._FIELD_MAP.keys()),
        )
        if hits:
            return True
        # Radio groups are addressed by name, not id, so check those too.
        named = page.evaluate(
            "(ns) => ns.filter(n => document.getElementsByName(n).length).length",
            list(ibm._FIELD_MAP.keys()),
        )
        return bool(named)
    except Exception:
        return False


def _advanced(page, before_progress: str, before_url: str, clicked_ok: bool) -> bool:
    """Did the form actually move forward?

    advance.click_next_and_verify decides this by URL-change or by the clicked
    button's exact outerHTML disappearing. Neither reliably holds on IBM: the
    URL stays JobApplication?jobId=N across the middle steps, and "Continue" is
    re-rendered identically on each one. So a successful advance can report
    False there.

    Three signals, cheapest first. The click has already happened by the time
    this runs, so consulting them is a better success check, not a retry — and
    advance.py's Submit guard already did its job inside click_next_and_verify
    before anything was clicked.
    """
    if clicked_ok:
        return True
    try:
        if (page.url or "") != before_url:
            log.info("Advanced: URL changed to %s.", page.url)
            return True
    except Exception:
        pass
    after = _read_progress(page)
    if after and before_progress and after != before_progress:
        log.info("Advanced: progress %s -> %s.", before_progress, after)
        return True
    return False


_MAX_ERROR_RETRIES = 2


def _advance_step(page, job, profile, progress: str, step_url: str) -> bool:
    """Clicks Continue and, if the form rejects it, reads the errors IBM
    renders, fills what they name, and tries again. Returns whether the wizard
    actually moved on.

    This replaces predicting required-ness, which cannot work here: gender
    reports element.required === false and carries no asterisk, yet the form
    refuses to advance without it. The form's own complaint is the only honest
    source of truth, and it names the field
    ("Gender: This field is required"), so a rejection is actionable rather
    than just a dead end.
    """
    for attempt in range(1, _MAX_ERROR_RETRIES + 2):
        clicked = click_next_and_verify(page)

        errors = ibm.read_validation_errors(page)
        if not errors and not ibm.has_validation_errors(page):
            if _advanced(page, progress, step_url, clicked):
                return True
            log.warning("Could not confirm the form advanced, and it reported no "
                        "errors either — stopping for you to check.")
            return False

        log.warning("=== The form rejected this step (attempt %d) ===", attempt)
        for err in errors:
            log.warning("  %s: %s", err.get("field") or "?", err.get("message") or "")

        if attempt > _MAX_ERROR_RETRIES:
            break

        # Try to satisfy what it complained about. fill_current_step only
        # touches fields that are still empty, so this is safe to re-run.
        log.info("Re-filling what the form named, then retrying.")
        retry = ibm.fill_current_step(page, job, profile)
        _print_report(retry)
        if not retry["filled"]:
            log.warning("Nothing further could be filled from your profile.")
            break
        human_pause(0.5, 1.0)

    log.warning("Stopping on this step. Fix the fields listed above in the open "
                "browser window — nothing was submitted.")
    return False


def _print_report(report: dict) -> None:
    if report["filled"]:
        log.info("=== Filled ===")
        for item in report["filled"]:
            log.info("  %s", item)
    if report["unmapped"]:
        log.warning("=== NOT filled — needs your input in the browser ===")
        for item in report["unmapped"]:
            log.warning("  %s", item)
    elif report["filled"]:
        log.info("Nothing unmapped on this step.")


def _dry_run_report(page, job: dict, profile: dict) -> None:
    """Prints what WOULD be filled without touching a single field. This is how
    _FIELD_MAP gets validated against a live posting at zero risk of writing a
    wrong answer into a real application."""
    raw = ibm._snapshot(page)
    log.info("=== DRY RUN — %d field(s) on this step, nothing will be typed ===", len(raw))
    for key, kind, rows in ibm._logical_fields(raw, profile):
        spec = ibm._FIELD_MAP.get(key)
        answered = "answered" if ibm._is_answered(page, key, kind, rows) else "EMPTY"
        if spec is None:
            log.info("  %-14s %-9s [unknown]  %s", key, answered, rows[0]["label"] or "(no label)")
            continue
        if spec.kind == "file":
            try:
                value = resolve_resume_path(profile, job.get("suggested_resume", "General")).name
            except Exception as exc:
                value = f"<{exc}>"
        elif spec.resolver is not None:
            value = spec.resolver(profile)
        else:
            value = ibm._lookup(profile, spec.key)
        log.info("  %-14s %-9s %-9s %s = %r", key, answered, spec.kind, spec.label, value)


def _resolve_target(job_id: Optional[str], url: Optional[str]):
    """Returns (apply_url, label) from either a jobId or a full URL.

    Both forms exist because a jobId is what you have when looking at a posting,
    while --url covers the cases a constructed URL cannot reach: an
    ApplicationMethods link, a session-specific URL, or any IBM/Avature host
    whose path differs from the JobApplication pattern.
    """
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            log.error("--url needs a full http(s) URL, got %r.", url)
            return (None, None)
        qs = parse_qs(parsed.query)
        found = (qs.get("jobId") or qs.get("jobid") or [None])[0]
        return (url, f"ibm-{found}" if found else "ibm-url")

    if not job_id or not job_id.isdigit():
        log.error("Expected a numeric IBM jobId (the number in the posting URL), "
                  "got %r. Or pass --url with the full application URL.", job_id)
        return (None, None)
    return (_APPLY_URL.format(job_id=job_id), f"ibm-{job_id}")


def _warn_if_profile_busy() -> None:
    """Two Chromes on one user-data-dir is unsupported — the second runs against
    a locked cookie store and loses whatever it writes, so the sign-in appears
    to work and is gone by the next run. Cheap to detect, baffling if not."""
    if profile_in_use():
        log.warning(
            "Another Chrome is already using the autofill profile. Close that "
            "window first — two of them share one cookie store and the second "
            "one's sign-in will not be saved.")
        input("Press Enter once it's closed (or Ctrl-C to abort)... ")


def login(url: Optional[str] = None) -> None:
    """Opens the automation browser at IBM and waits, so you can sign in once.

    Deliberately skips profile loading and validation. The session is what makes
    everything else testable, and requiring a complete application_profile.yaml
    before you can even reach the login page is a chicken-and-egg this tool
    should not impose. Nothing is filled and nothing is clicked here.
    """
    target = url or "https://careers.ibm.com/"
    _warn_if_profile_busy()
    pw, context, page = launch_browser()
    try:
        page.goto(target, wait_until="domcontentloaded", timeout=30000)
        log.info("Sign in to IBM in this window. The session is saved to the "
                 "persistent profile at %s, so you only do this once.",
                 AUTOFILL_BROWSER_PROFILE_DIR)
        input("\nPress Enter once you're signed in... ")
        log.info("Now at: %s", page.url)
    finally:
        close_browser(pw, context)


def run(job_id: Optional[str] = None, resume_variant: str = "General",
        dry_run: bool = False, advance: bool = True,
        url: Optional[str] = None) -> None:
    apply_url, label = _resolve_target(job_id, url)
    if apply_url is None:
        return

    try:
        profile = load_profile()
    except ProfileError as exc:
        log.error(str(exc))
        return

    # Validate before launching a browser — a YAML mistake should cost nothing.
    problems = ibm.validate_ibm_profile(profile)
    if problems:
        log.error("The `ibm:` block in your application profile needs fixing:")
        for p in problems:
            log.error("  %s", p)
        return

    # Fail on a missing resume now rather than three steps into the wizard.
    if not dry_run:
        try:
            resolve_resume_path(profile, resume_variant)
        except ProfileError as exc:
            log.error(str(exc))
            return

    job = {
        "id": label,                    # label only — not a database id
        "title": f"IBM job {label.removeprefix('ibm-')}",
        "company": "IBM",
        "apply_url": apply_url,
        "suggested_resume": resume_variant,
    }
    log.info("Job: %s [ibm]", job["apply_url"])
    log.info("Resume: %s", resume_variant)

    _warn_if_profile_busy()
    pw, context, page = launch_browser()
    try:
        page.goto(job["apply_url"], wait_until="domcontentloaded", timeout=30000)
        human_pause(2.0, 3.0)

        if not _looks_like_application_form(page):
            log.error(
                "This does not look like the application form — most likely you are "
                "not logged into IBM. Log in in this browser window, then re-run. "
                "(The window stays open.)")
            input("\nPress Enter to close the browser... ")
            return

        for step in range(1, _MAX_STEPS + 1):
            progress = _read_progress(page)
            step_url = page.url
            log.info("--- Step %d%s ---", step, f" ({progress})" if progress else "")

            # Terminal page, detected by URL rather than by button text.
            if _is_terminal_page(page):
                log.info("Reached the review page (JobApplicationSummary). "
                         "Everything below this point is yours: review it and click "
                         "Submit Application yourself. This tool never does.")
                break

            if dry_run:
                _dry_run_report(page, job, profile)
                break

            report = ibm.fill_current_step(page, job, profile)
            _print_report(report)

            if has_visible_captcha_challenge(page):
                log.warning("A CAPTCHA challenge is visible — solve it yourself, then continue.")
                break

            if report["likely_required"]:
                log.info("Possibly still required (advisory — the form is the judge): %s",
                         ", ".join(report["likely_required"]))

            if not advance:
                log.info("--no-advance: stopping after this step so you can inspect it.")
                break

            if find_next_button(page) is None:
                log.info("No Next/Continue button — nothing left to advance through. "
                         "Review and submit yourself, never automated.")
                break

            if not _advance_step(page, job, profile, progress, step_url):
                break
        else:
            log.warning("Hit the %d-step ceiling. Stopping.", _MAX_STEPS)

        log.info("Nothing was submitted. Review the form and submit it yourself.")
        input("\nPress Enter when you're done to close the browser... ")
    finally:
        close_browser(pw, context)


_USAGE = (
    "Usage:\n"
    "  python -m autofill.autofill_ibm --login              sign in once, nothing else\n"
    "  python -m autofill.autofill_ibm <jobId> [options]\n"
    "  python -m autofill.autofill_ibm --url <applicationUrl> [options]\n"
    "\n"
    "Options:\n"
    "  --dry-run          print what would be filled; type nothing\n"
    "  --no-advance       fill the current step only, don't click Continue\n"
    "  --resume VARIANT   General (default), Mobile, AI, Frontend\n"
)

if __name__ == "__main__":
    args = list(sys.argv[1:])
    if not args:
        print(_USAGE)
        sys.exit(1)

    if "--login" in args:
        rest = [a for a in args if a != "--login"]
        login(rest[0] if rest else None)
        sys.exit(0)

    def take_value(flag, err):
        if flag not in args:
            return None
        i = args.index(flag)
        if i + 1 >= len(args) or args[i + 1].startswith("--"):
            print(err)
            sys.exit(1)
        value = args[i + 1]
        del args[i:i + 2]
        return value

    variant = take_value("--resume", "--resume needs a variant (General, Mobile, AI, Frontend)") or "General"
    target_url = take_value("--url", "--url needs the full application URL")

    flags = {a for a in args if a.startswith("--")}
    unknown = flags - {"--dry-run", "--no-advance"}
    if unknown:
        print(f"Unknown option(s): {', '.join(sorted(unknown))}\n\n{_USAGE}")
        sys.exit(1)

    positional = [a for a in args if not a.startswith("--")]
    if target_url and positional:
        print("Pass either a jobId or --url, not both.\n\n" + _USAGE)
        sys.exit(1)
    if not target_url and len(positional) != 1:
        print(_USAGE)
        sys.exit(1)

    run(
        positional[0] if positional else None,
        resume_variant=variant,
        dry_run="--dry-run" in flags,
        advance="--no-advance" not in flags,
        url=target_url,
    )
