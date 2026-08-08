"""Advances a multi-step application form by clicking Next/Continue — never
Submit/Apply/Finish — and self-verifies the click actually moved the form
forward before it's safe to continue automatically.

Platform-agnostic on purpose: shared by both the native platform fillers
(platforms/greenhouse.py etc.) and the Simplify-assisted path
(simplify_assist.py), since advancing a step is the same problem regardless
of which filler populated the fields on it.
"""

import logging
from playwright.sync_api import Page

from autofill.browser import human_pause

log = logging.getLogger(__name__)

_NEXT_BUTTON_PATTERN = "Next|Continue|Save and Continue|Save & Continue"
_SUBMIT_BUTTON_PATTERN = "Submit Application|Submit|Apply Now|^Apply$|Finish"


def find_next_button(page: Page):
    """Returns a Locator for a Next/Continue-style button, or None. Never
    matches Submit/Apply/Finish-style text — a button whose text matches
    both patterns (e.g. "Continue to Submit") is treated as a submit
    button, not a safe Next, full stop."""
    candidates = page.get_by_role("button", name=_NEXT_BUTTON_PATTERN, exact=False)
    submit_like = page.get_by_role("button", name=_SUBMIT_BUTTON_PATTERN, exact=False)
    submit_texts = set()
    for i in range(submit_like.count()):
        try:
            submit_texts.add(submit_like.nth(i).inner_text().strip().lower())
        except Exception:
            continue

    for i in range(candidates.count()):
        btn = candidates.nth(i)
        try:
            text = btn.inner_text().strip().lower()
        except Exception:
            continue
        if text in submit_texts:
            continue
        if btn.is_visible():
            return btn
    return None


def click_next_and_verify(page: Page) -> bool:
    """Clicks a Next/Continue button and verifies the form actually
    advanced (URL changed, or the exact button just clicked is no longer
    attached in the same spot — i.e. this step plausibly re-rendered
    forward). Returns False if there's no Next button, or the click didn't
    visibly do anything — the caller must stop and let the human take over
    in either case. This never retries or guesses at success."""
    btn = find_next_button(page)
    if btn is None:
        return False

    before_url = page.url
    try:
        btn_html = btn.evaluate("el => el.outerHTML")
    except Exception:
        btn_html = None

    btn.click()
    human_pause(1.5, 2.5)

    if page.url != before_url:
        log.info("Advanced: URL changed (%s -> %s).", before_url, page.url)
        return True

    if btn_html is not None:
        try:
            still_there = page.evaluate(
                "html => Array.from(document.querySelectorAll('button')).some(b => b.outerHTML === html)",
                btn_html,
            )
            if not still_there:
                log.info("Advanced: previous step's Next button is no longer present.")
                return True
        except Exception:
            pass

    log.warning("Clicked Next but couldn't confirm the form advanced — stopping for review.")
    return False
