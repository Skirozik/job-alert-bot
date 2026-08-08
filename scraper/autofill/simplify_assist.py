"""Orchestrates the Simplify Copilot Chrome extension to fill an
application form, instead of our own platform-specific field-by-field
fillers in platforms/.

We do NOT reuse, copy, or reverse-engineer any of Simplify's code — this
only drives the extension's own UI exactly as a human would: find its
"Autofill this page" button and click it. That button lives inside
Simplify's own open shadow DOM (confirmed live: class
"simplify-jobs-shadow-root"); Playwright's locators pierce open shadow
roots automatically, so no special handling is needed to reach it.

Requires the browser to have been launched with allow_extensions=True (see
browser.py) and Simplify to already be installed + logged into the profile
being driven — this module does not install or configure Simplify itself.
"""

import logging
from playwright.sync_api import Page

from autofill.browser import human_pause

log = logging.getLogger(__name__)

_AUTOFILL_BUTTON_TEXT = "Autofill this page"


def is_simplify_active(page: Page, timeout_ms: int = 8000) -> bool:
    """True if Simplify's panel has injected into the page and its autofill
    button is present. False if Simplify isn't installed, isn't logged in,
    or doesn't support this particular page/platform — caller should fall
    back to a native filler or hand off to the human."""
    try:
        page.get_by_text(_AUTOFILL_BUTTON_TEXT, exact=True).first.wait_for(
            state="attached", timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


def trigger_autofill(page: Page) -> bool:
    """Clicks Simplify's own 'Autofill this page' button. Returns True if
    the button was found and clicked, False if it wasn't there."""
    btn = page.get_by_text(_AUTOFILL_BUTTON_TEXT, exact=True).first
    if btn.count() == 0:
        return False
    btn.scroll_into_view_if_needed()
    human_pause(0.3, 0.6)
    btn.click()
    log.info("Clicked Simplify's '%s' button.", _AUTOFILL_BUTTON_TEXT)
    return True


def wait_for_fill_to_settle() -> None:
    """Simplify fills fields client-side with no single 'done' signal to
    hook into (setting a React-controlled input's value doesn't reliably
    fire DOM mutations a MutationObserver would catch), so this is a fixed,
    generous pause rather than an event-based wait — the same pragmatic
    approach platforms/greenhouse.py already uses after resume upload."""
    human_pause(3.0, 4.5)
