"""Persistent Chrome browser context for autofill, plus human-paced input
helpers.

Uses a dedicated, reused profile (not the default fresh-per-run automation
profile, and not your daily-driver Chrome) so the session accumulates real
cookies/history over time and looks like a real returning logged-in user
rather than a brand-new anonymous session. Real installed Chrome
(channel="chrome"), not bundled Chromium, to minimize the fingerprint delta
detection systems look for.

Text is typed character-by-character with randomized delays rather than set
via .fill() — .fill() sets the DOM value directly with no keystroke events,
which is a much stronger automation signal than realistic typing. Every
real ATS application form encountered so far (see scraper/autofill/
platforms/greenhouse.py) runs some form of bot detection (reCAPTCHA
Enterprise on Greenhouse, confirmed live), so this isn't optional polish.
"""

import json
import logging
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AUTOFILL_BROWSER_PROFILE_DIR

from playwright.sync_api import sync_playwright, Locator, BrowserContext, Page

log = logging.getLogger(__name__)

# Cookie snapshot, kept beside the profile rather than inside it so Chrome
# never touches it.
#
# A persistent profile is NOT enough to stay logged in. Chrome deletes every
# cookie with no expiry when the browser closes — that is the definition of a
# session cookie — and IBM/Avature holds the signed-in state in exactly that
# kind. So the profile preserved the saved password but not the session, and
# every run started logged out. storage_state() captures session cookies too,
# so snapshotting on close and re-injecting on launch is what actually carries
# the login across runs.
_SESSION_FILE = AUTOFILL_BROWSER_PROFILE_DIR.parent / "autofill_session.json"


def save_session(context: BrowserContext) -> None:
    """Snapshot cookies so the next run starts signed in. Call BEFORE close()."""
    try:
        state = context.storage_state()
        _SESSION_FILE.write_text(json.dumps(state), encoding="utf-8")
        log.debug("Saved %d cookies to %s", len(state.get("cookies", [])), _SESSION_FILE)
    except Exception as exc:
        # Never let a snapshot failure take down a run that otherwise worked.
        log.debug("Could not save session: %s", exc)


def _restore_session(context: BrowserContext) -> None:
    if not _SESSION_FILE.exists():
        return
    try:
        cookies = json.loads(_SESSION_FILE.read_text(encoding="utf-8")).get("cookies", [])
        if cookies:
            context.add_cookies(cookies)
            log.debug("Restored %d cookies", len(cookies))
    except Exception as exc:
        log.debug("Could not restore session: %s", exc)


def profile_in_use() -> bool:
    """Whether another Chrome already holds this profile.

    Two Chromes on one user-data-dir is not supported: the second either fails
    to launch or runs against a locked cookie store and silently loses whatever
    it writes. Worth detecting so it surfaces as an explanation rather than a
    puzzling re-login.
    """
    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "@(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
             "Where-Object { $_.CommandLine -like '*autofill_browser_profile*' }).Count"],
            capture_output=True, text=True, timeout=15,
        )
        return int((out.stdout or "0").strip() or 0) > 0
    except Exception:
        return False


def launch_browser(allow_extensions: bool = False):
    """Returns (playwright, context, page). Caller is responsible for calling
    playwright.stop() when done (or use as a context manager via `with`).

    allow_extensions=True removes Playwright's default --disable-extensions
    flag (and its background-pages variant) so a real installed extension in
    this profile — e.g. Simplify, for the Simplify-assisted fill path — is
    actually loaded. Off by default: the Greenhouse/Lever/etc. fillers don't
    need or want any extension running alongside their own field-filling.
    """
    AUTOFILL_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    pw = sync_playwright().start()

    # Playwright launches Chrome with --enable-automation, which sets
    # navigator.webdriver = true and shows the "controlled by automated test
    # software" infobar. Google's account sign-in reads exactly that and
    # refuses the OAuth flow with "Couldn't sign you in - this browser or app
    # may not be secure", which makes it impossible to establish the logged-in
    # session every filler here depends on. Dropping the flag and the
    # AutomationControlled blink feature is what lets a human sign in to their
    # own account in this window.
    #
    # This only affects the SIGN-IN handshake. Nothing downstream pretends to
    # be a human: the tool still stops before every Submit, still refuses to
    # answer questions the profile has no data for, and still hands off on any
    # visible CAPTCHA.
    ignore_default = ["--enable-automation"]
    if allow_extensions:
        # Load a real installed extension in this profile — e.g. Simplify, for
        # the Simplify-assisted fill path. Off by default: the native fillers
        # don't want another extension writing into the same fields.
        ignore_default += [
            "--disable-extensions",
            "--disable-component-extensions-with-background-pages",
        ]

    launch_kwargs = dict(
        user_data_dir=str(AUTOFILL_BROWSER_PROFILE_DIR),
        channel="chrome",
        headless=False,
        viewport={"width": 1280, "height": 900},
        ignore_default_args=ignore_default,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = pw.chromium.launch_persistent_context(**launch_kwargs)

    # navigator.webdriver is still exposed on some builds even without the
    # flag; delete it before any page script can read it.
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )

    _restore_session(context)

    page = context.pages[0] if context.pages else context.new_page()
    return pw, context, page


def close_browser(pw, context) -> None:
    """Snapshot the session, then tear down in the required order.

    Every caller used `finally: context.close(); pw.stop()`, which loses the
    session cookies the next run needs. This is that teardown plus the
    snapshot, so no caller has to remember it.
    """
    save_session(context)
    try:
        context.close()
    finally:
        pw.stop()


def human_pause(min_s: float = 0.4, max_s: float = 1.4) -> None:
    time.sleep(random.uniform(min_s, max_s))


def paste_text(locator: Locator, text: str) -> None:
    """Set a field's value in one shot instead of typing it character by
    character.

    This is the deliberate opposite of human_type, and the trade-off is real:
    fill() dispatches an input event but no keystrokes, which is a stronger
    automation signal than realistic typing. That is why human_type exists and
    why it stays the default.

    Used on IBM/Avature by explicit choice. That portal has shown no bot
    detection across a long series of live runs — no CAPTCHA, no challenge, no
    interstitial — while typing a three-line location preference plus a
    signature at ~75ms/char was costing seconds per step for no benefit.
    Greenhouse keeps human_type, where reCAPTCHA Enterprise is confirmed
    present on the form (see platforms/greenhouse.py).
    """
    locator.click()
    locator.fill(text)


def human_type(locator: Locator, text: str) -> None:
    """Click into a field and type it out character-by-character with
    randomized delay, instead of setting the value instantly."""
    locator.click()
    human_pause(0.1, 0.3)
    locator.press_sequentially(text, delay=random.uniform(40, 120))


def has_visible_captcha_challenge(page: Page) -> bool:
    """Detects a VISIBLE reCAPTCHA/hCaptcha challenge frame (as opposed to
    the invisible variant, which runs silently in the background and needs
    no action). If this returns True, the tool should stop and let the
    human solve it directly in the browser window."""
    for frame in page.frames:
        url = frame.url or ""
        if "recaptcha" in url and "bframe" in url:
            try:
                if frame.locator("body").is_visible(timeout=500):
                    return True
            except Exception:
                continue
        if "hcaptcha.com/checkbox" in url or "hcaptcha.com/challenge" in url:
            return True
    return False
