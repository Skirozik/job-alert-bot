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

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AUTOFILL_BROWSER_PROFILE_DIR

from playwright.sync_api import sync_playwright, Locator, BrowserContext, Page


def launch_browser():
    """Returns (playwright, context, page). Caller is responsible for calling
    playwright.stop() when done (or use as a context manager via `with`)."""
    AUTOFILL_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(AUTOFILL_BROWSER_PROFILE_DIR),
        channel="chrome",
        headless=False,
        viewport={"width": 1280, "height": 900},
    )
    page = context.pages[0] if context.pages else context.new_page()
    return pw, context, page


def human_pause(min_s: float = 0.4, max_s: float = 1.4) -> None:
    time.sleep(random.uniform(min_s, max_s))


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
