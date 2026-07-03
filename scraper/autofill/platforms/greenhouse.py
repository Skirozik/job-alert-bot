"""Greenhouse application form filler.

Confirmed live (2026-07-03) against a real Greenhouse job-boards posting:
the application form sits directly in the main frame (not an iframe) with
clean semantic <label for="X"> pairing throughout, standard fields at
predictable ids (first_name/last_name/preferred_name/email/phone/country/
resume/cover_letter), and dynamic per-posting custom questions at
id="question_<number>" with the same label pairing. An invisible reCAPTCHA
Enterprise widget is present — it runs automatically in the background and
needs no action from this code, since we stop before ever clicking Submit
(the human solves it themselves if it ever surfaces a visible challenge at
that point, which is out of scope for this tool).

Order matters: resume is uploaded FIRST so Greenhouse's own resume parser
can prefill whatever it recognizes, mirroring how a human actually fills
these out — only fields still empty afterward get filled from the profile.
"""

import logging
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from autofill.browser import human_type, human_pause
from autofill.field_matcher import match_field
from autofill.profile_loader import resolve_resume_path

log = logging.getLogger(__name__)

_SIMPLE_FIELD_IDS = {
    "first_name": lambda p: p["personal"].get("preferred_first_name") or p["personal"]["legal_first_name"],
    "last_name": lambda p: p["personal"]["last_name"],
    "preferred_name": lambda p: p["personal"].get("preferred_first_name", ""),
    "email": lambda p: p["personal"]["email"],
}

_SUBMIT_BUTTON_PATTERN = "Submit Application|Submit|Apply|Finish"


def _fill_combobox(page, frame, input_locator, search_text: str) -> bool:
    """Fills a react-select-style searchable dropdown (country, and several
    of Greenhouse's yes/no custom questions all use this same widget) by
    opening it, typing the search text, and clicking the resulting option.

    Typing alone (human_type) is NOT enough for these — it only populates
    the widget's internal search box, it never actually selects anything,
    so the field is left effectively blank even though text was typed into
    it. Confirmed live: this was silently leaving 4 required questions
    unanswered on a real posting before this fix. Must target role="option"
    specifically — a plain text search also matches the widget's ARIA
    live-region announcement ("N results available for search term ..."),
    which sits earlier in the DOM and intercepts the click instead of the
    real option.
    """
    input_locator.click()
    human_pause(0.2, 0.5)
    page.keyboard.type(search_text, delay=random.uniform(40, 120))
    human_pause(0.6, 1.2)
    option = frame.get_by_role("option", name=search_text, exact=False).first
    if option.count() > 0:
        option.click()
        return True
    return False


def fill(page, job: dict, profile: dict) -> dict:
    """Returns a report dict: {"filled": [...], "unmapped": [...], "submit_button_text": str|None}."""
    report = {"filled": [], "unmapped": [], "submit_button_text": None}

    page.goto(job["apply_url"], wait_until="domcontentloaded", timeout=30000)
    human_pause(1.5, 3.0)

    frame = page.main_frame

    # 1. Resume upload first — let Greenhouse's own parser prefill what it can.
    resume_input = frame.locator("#resume")
    if resume_input.count() > 0:
        resume_path = resolve_resume_path(profile, job.get("suggested_resume", "General"))
        resume_input.set_input_files(str(resume_path))
        report["filled"].append(f"resume ({resume_path.name})")
        human_pause(1.5, 3.0)  # give the parser time to populate fields

    # 2. Simple fields — only fill what's still empty after the resume parse.
    for field_id, value_fn in _SIMPLE_FIELD_IDS.items():
        loc = frame.locator(f"#{field_id}")
        if loc.count() == 0:
            continue
        current = loc.input_value()
        if current:
            continue
        value = value_fn(profile)
        if value:
            human_type(loc, value)
            report["filled"].append(field_id)
            human_pause()

    # 3. Phone — plain text input inside the intl-tel-input widget.
    phone_loc = frame.locator("#phone")
    if phone_loc.count() > 0 and not phone_loc.input_value():
        phone = profile["personal"].get("phone")
        if phone:
            human_type(phone_loc, phone)
            report["filled"].append("phone")
            human_pause()

    # 4. Country — react-select searchable dropdown, not a plain input.
    country_loc = frame.locator("#country")
    if country_loc.count() > 0 and not (country_loc.get_attribute("value") or ""):
        country = profile["personal"]["address"].get("country")
        if country and _fill_combobox(page, frame, country_loc, country):
            report["filled"].append("country")
        human_pause()

    # 5. Dynamic per-posting custom questions — label-matched, never guessed.
    # These come in two widget types on Greenhouse: plain text/textarea
    # inputs, and the same react-select combobox as the country field above
    # (used for most yes/no screening questions) — role="combobox" is how
    # to tell them apart, and each needs its own fill strategy.
    question_labels = frame.locator("label[id$='-label'][for^='question_']")
    for i in range(question_labels.count()):
        label_el = question_labels.nth(i)
        label_text = label_el.inner_text().replace("*", "").strip()
        input_id = label_el.get_attribute("for")
        if not input_id:
            continue
        target = frame.locator(f"#{input_id}")
        if target.count() == 0:
            continue

        is_combobox = (target.get_attribute("role") or "") == "combobox"
        try:
            if not is_combobox and target.input_value():
                continue  # already has a value (e.g. resume parser filled it)
        except Exception:
            pass  # not a fillable input (e.g. a file input) — skip

        match = match_field(label_text, profile)
        if not match:
            report["unmapped"].append(label_text)
            continue

        field_key, value = match
        if is_combobox:
            if _fill_combobox(page, frame, target, value):
                report["filled"].append(f"{label_text} -> {field_key}")
            else:
                report["unmapped"].append(f"{label_text} (no matching option for '{value}')")
        else:
            human_type(target, value)
            report["filled"].append(f"{label_text} -> {field_key}")
        human_pause()

    # 6. Locate (but never click) the submit button.
    submit_btn = frame.get_by_role("button", name=_SUBMIT_BUTTON_PATTERN, exact=False)
    if submit_btn.count() > 0:
        report["submit_button_text"] = submit_btn.first.inner_text()

    return report
