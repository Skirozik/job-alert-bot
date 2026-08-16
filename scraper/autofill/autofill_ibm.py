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

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))
from autofill.browser import launch_browser, has_visible_captcha_challenge, human_pause
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


def _read_progress(page) -> str:
    """IBM's wizard shows a percentage (20% / 40% / 60% ...). Used both for
    logging and — importantly — as the advance signal, see _advanced()."""
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


def _advanced(page, before_progress: str, clicked_ok: bool) -> bool:
    """Did the form actually move forward?

    advance.click_next_and_verify decides this by URL-change or by the clicked
    button's exact outerHTML disappearing. Neither holds on IBM: the URL stays
    JobApplication?jobId=N across every step, and "Continue" is re-rendered
    identically on each one. So a successful advance reports False there.

    The progress bar is the reliable signal on this portal. The click has
    already happened by the time this runs, so consulting it is a better
    success check, not a retry — and advance.py's Submit guard still did its
    job inside click_next_and_verify before anything was clicked.
    """
    if clicked_ok:
        return True
    after = _read_progress(page)
    if after and before_progress and after != before_progress:
        log.info("Advanced: progress %s -> %s.", before_progress, after)
        return True
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
    for key, kind, rows in ibm._logical_fields(raw):
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


def run(job_id: str, resume_variant: str = "General",
        dry_run: bool = False, advance: bool = True) -> None:
    if not job_id.isdigit():
        log.error("Expected a numeric IBM jobId (the number in the posting URL), got %r.", job_id)
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
        "id": f"ibm-{job_id}",          # label only — not a database id
        "title": f"IBM job {job_id}",
        "company": "IBM",
        "apply_url": _APPLY_URL.format(job_id=job_id),
        "suggested_resume": resume_variant,
    }
    log.info("Job: %s [ibm]", job["apply_url"])
    log.info("Resume: %s", resume_variant)

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
            log.info("--- Step %d%s ---", step, f" ({progress})" if progress else "")

            if dry_run:
                _dry_run_report(page, job, profile)
                break

            report = ibm.fill_current_step(page, job, profile)
            _print_report(report)

            if has_visible_captcha_challenge(page):
                log.warning("A CAPTCHA challenge is visible — solve it yourself, then continue.")
                break

            if report["blocking"]:
                log.warning("=== Cannot continue — required field(s) still unanswered ===")
                for item in report["blocking"]:
                    log.warning("  %s", item)
                log.warning("Stopping on this step. Fill these in the open browser window.")
                break

            if not advance:
                log.info("--no-advance: stopping after this step so you can inspect it.")
                break

            if find_next_button(page) is None:
                log.info("No Next/Continue button — this is the final review/submit step. "
                         "Stopping here; review and submit yourself, never automated.")
                break

            if not _advanced(page, progress, click_next_and_verify(page)):
                log.warning("Could not confirm the form advanced — stopping for you to check.")
                break
        else:
            log.warning("Hit the %d-step ceiling. Stopping.", _MAX_STEPS)

        log.info("Nothing was submitted. Review the form and submit it yourself.")
        input("\nPress Enter when you're done to close the browser... ")
    finally:
        context.close()
        pw.stop()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if not args:
        print("Usage: python -m autofill.autofill_ibm <jobId> [--dry-run] [--no-advance] [--resume VARIANT]")
        sys.exit(1)

    variant = "General"
    if "--resume" in args:
        i = args.index("--resume")
        if i + 1 >= len(args):
            print("--resume needs a variant name (General, Mobile, AI, Frontend)")
            sys.exit(1)
        variant = args[i + 1]
        del args[i:i + 2]

    flags = {a for a in args if a.startswith("--")}
    positional = [a for a in args if not a.startswith("--")]
    if len(positional) != 1:
        print("Usage: python -m autofill.autofill_ibm <jobId> [--dry-run] [--no-advance] [--resume VARIANT]")
        sys.exit(1)

    run(
        positional[0],
        resume_variant=variant,
        dry_run="--dry-run" in flags,
        advance="--no-advance" not in flags,
    )
