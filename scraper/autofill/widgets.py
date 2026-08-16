"""Dropdown widgets, and the verification that proves one actually took.

Avature (IBM's careers portal) renders TWO kinds of dropdown that look
identical to a user and need completely different handling. Getting it wrong
fails SILENTLY — the field looks touched and stays empty — which is the same
failure class as the react-select bug documented in platforms/greenhouse.py,
but with a different fix.

Measured live (2026-08-16) against careers.ibm.com jobIds 127789 and 129258:

1. Plain <select>. Options are present in the DOM at page load. Setting the
   value works. Confirmed on 32764, 37299-7-N, 35979_month, 35979_day,
   10542-1, 20400.

2. Dynamic dropdown — a custom widget whose underlying <select> holds 0 or 1
   options at load and populates only on interaction. Three approaches were
   tried and only the third works:
     - setting .value directly       -> no effect
     - clicking the [role=option]    -> no effect
     - arrow keys + Enter            -> selects the WRONG item. It picked
       "United States Minor Outlying Islands" for "United States".
     - click -> type the search text -> Enter   <- the only reliable one
   Confirmed on 10478, 10499, 10500, 18199, study/specialization, 12706, 12710.

THE VERIFICATION IS THE POINT OF THIS MODULE. Because a failed fill is
invisible, every function here returns a bool that reflects what the DOM
actually holds afterward, not whether the interaction was dispatched without
raising. Callers report False as unmapped rather than claiming success.

That verification compares EXACT equality against a single extracted label,
never a substring of a text blob. "United States" is a substring of "United
States Minor Outlying Islands", so the precise failure this module exists to
prevent would sail through a substring check.
"""

import logging
import random
import re

from playwright.sync_api import Locator, Page

from autofill.browser import human_pause

log = logging.getLogger(__name__)

# Labels that mean "nothing chosen yet".
#
# A PATTERN, not a fixed set. The set version missed Avature's actual wording,
# "Select an option", and the cost of that miss was the entire feature: a
# dropdown showing its placeholder read as already-answered, so every dynamic
# field was skipped, never entered the report at all, and the form rejected the
# step for four required fields the tool had silently declined to fill.
#
# Matches "Select an option", "Select...", "-- Select --", "Please choose a
# value", "None", bare dashes. Does NOT match a real option that merely begins
# with those letters — \b after `select` means "Selected Employer" is safe.
# Matches the WHOLE string, not just its start. A prefix match would swallow
# real options: "Choose Financial Group" is a real company and "Selected
# Employer" a real answer, and treating either as "nothing chosen" would make
# the tool overwrite a correct value or re-fill forever.
_PLACEHOLDER_RE = re.compile(
    r"^[-–—\s]*(?:please\s+)?(?:select|choose|pick)"
    r"(?:\s+(?:an?|one|your|the))?"
    r"(?:\s+(?:option|value|item|choice|one))?"
    r"[-–—\s.]*$"
    r"|^[-–—\s]*$"
    r"|^none$"
    r"|^n/?a$",
    re.I,
)


def norm(text) -> str:
    """Whitespace-collapsed, stripped, casefolded — the single normalization
    used for every label comparison in this module and platforms/ibm.py."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def is_placeholder_label(text) -> bool:
    t = norm(text)
    return not t or bool(_PLACEHOLDER_RE.search(t))


# ── Plain <select> ────────────────────────────────────────────────────────

def fill_plain_select(locator: Locator, text: str) -> bool:
    """Selects an option by its visible label. Returns whether the select
    actually ended up holding that label — never whether the attempt raised.

    Never picks a near match. An option list containing "Yes" and "Yes, with
    conditions" must not silently resolve "Yes" to the wrong one, so matching
    is exact (after whitespace/case normalization) at every tier.
    """
    want = norm(text)

    # Tier 1: Playwright's own API. It dispatches input+change itself, raises
    # loudly when no option matches, and is a smaller automation signal than
    # assigning .value from JS — the same reasoning browser.py's human_type
    # docstring gives for not using .fill().
    #
    # timeout is short on purpose. select_option WAITS for a matching option to
    # appear, defaulting to 30s — but a plain select is by definition one whose
    # options are already in the DOM at load, so a miss here is a real miss, not
    # a slow load. Without this, every unmatched value cost 30 dead seconds
    # before the two instant fallbacks below even ran, times up to six passes.
    try:
        locator.select_option(label=text, timeout=2000)
        if _selected_label(locator) == want:
            return True
    except Exception as exc:
        log.debug("select_option(label=%r) did not take: %s", text, exc)

    # Tier 2: the label may differ by whitespace or case only. Find the option
    # whose normalized text matches and select it by its value attribute.
    try:
        options = locator.evaluate(
            "el => Array.from(el.options).map(o => ({value: o.value, text: o.textContent}))"
        )
    except Exception:
        options = []
    for opt in options:
        if norm(opt.get("text")) == want:
            try:
                locator.select_option(value=opt["value"])
                if _selected_label(locator) == want:
                    return True
            except Exception as exc:
                log.debug("select_option(value=%r) failed: %s", opt.get("value"), exc)
            break

    # Tier 3: assign the value and dispatch the events by hand. Last resort —
    # some Avature selects are wired to listeners that Playwright's own
    # selection already satisfies, so this is only reached when both tiers
    # above have failed outright.
    for opt in options:
        if norm(opt.get("text")) == want:
            try:
                locator.evaluate(
                    """(el, v) => {
                        el.value = v;
                        el.dispatchEvent(new Event('input',  {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    }""",
                    opt["value"],
                )
            except Exception as exc:
                log.debug("manual value assignment failed: %s", exc)
            break

    return _selected_label(locator) == want


def _selected_label(locator: Locator) -> str:
    """Normalized text of the currently selected option, or '' for none or a
    placeholder ("Select...", "-", ...)."""
    try:
        label = locator.evaluate(
            "el => el.selectedIndex >= 0 ? el.options[el.selectedIndex].textContent : ''"
        )
    except Exception:
        return ""
    return "" if is_placeholder_label(label) else norm(label)


def read_select_choice(locator: Locator) -> str:
    """Public form of _selected_label, for the fill/rescan loop's
    'is this field answered' check."""
    return _selected_label(locator)


# ── Dynamic (Avature custom) dropdown ─────────────────────────────────────

# Ordered source chain for reading back what a dynamic dropdown is DISPLAYING.
# The exact markup could not be captured without a live session, so the chain
# is deliberately broad and meant to be TRIMMED once a probe run shows which
# source actually holds the chosen label. read_dropdown_choice returns which
# source hit so that trimming is a matter of reading the log, not guessing.
_CHOICE_READER_JS = """
(fid) => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
  const el = document.getElementById(fid);
  if (!el) return {select: '', chosen: '', combobox: '', container: ''};

  // 1. The underlying <select>. The form has to POST a value, so a genuine
  //    user pick very likely does sync here even though setting .value
  //    programmatically does not stick. If this works, nothing else matters.
  let select = '';
  if (el.tagName === 'SELECT' && el.selectedIndex >= 0) {
    select = norm(el.options[el.selectedIndex].textContent);
  }

  const box = el.closest('div, li, fieldset, td, tr') || el.parentElement;

  // 2. A rendered "chosen label" element inside the widget's container.
  let chosen = '';
  if (box) {
    const sel = '[class*="chosen"], [class*="selection"], [class*="selected"],'
              + '[class*="rendered"], [class*="display"], a[role="combobox"],'
              + 'span[role="combobox"], [class*="single"]';
    for (const node of box.querySelectorAll(sel)) {
      const t = norm(node.innerText || node.textContent);
      if (t) { chosen = t; break; }
    }
  }

  // 3. The combobox INPUT's value. Contaminated on purpose-built search
  //    boxes: it may hold what we TYPED rather than what got chosen, which
  //    would make verification vacuous. Reported separately so the caller
  //    can tag it as low-confidence rather than trusting it silently.
  let combobox = '';
  if (box) {
    const inp = box.querySelector('input[role="combobox"], input[type="text"]');
    if (inp) combobox = norm(inp.value);
  }

  // 4. Container text minus its <label>. Last resort, low confidence.
  let container = '';
  if (box) {
    let t = norm(box.innerText);
    for (const lab of box.querySelectorAll('label')) {
      t = t.replace(norm(lab.innerText), '').trim();
    }
    container = norm(t);
  }

  return {select, chosen, combobox, container};
}
"""


def read_dropdown_choice(page: Page, field_id: str, trusted_only: bool = False) -> tuple:
    """Returns (label, source) — what the dynamic dropdown is currently
    displaying, and which DOM source proved it. ('', '') when nothing is
    chosen.

    Public rather than private because the fill/rescan loop needs it for its
    'is this field answered' check: a dynamic widget's underlying <select> is
    empty at load, so inspecting the select would report every dynamic field
    as unanswered forever and the loop would refill them on every pass.
    """
    try:
        sources = page.evaluate(_CHOICE_READER_JS, field_id)
    except Exception as exc:
        log.debug("read_dropdown_choice(%s) failed: %s", field_id, exc)
        return ("", "")

    # trusted_only is for the "is this field already answered" check, where a
    # false positive means the field is silently never filled. The last two
    # sources cannot answer that question: `combobox` echoes whatever was
    # TYPED, so a failed fill looks like a successful one, and `container` is
    # raw innerText that can carry the question, the error text, or anything
    # else the widget renders.
    names = ("select", "chosen") if trusted_only else ("select", "chosen", "combobox", "container")
    for name in names:
        value = sources.get(name) or ""
        if value and not is_placeholder_label(value):
            return (value, name)
    return ("", "")


# Finds the thing a human would actually click for a dynamic dropdown.
#
# The element carrying the field id is the underlying <select>, which the
# widget hides — clicking it does nothing at all, silently. The visible control
# is a sibling or wrapper the widget renders. Returns a unique selector for it,
# or "" if the select itself is visible and can be clicked directly.
_TRIGGER_JS = """
(fid) => {
  const el = document.getElementById(fid);
  if (!el) return '';
  const onScreen = n => {
    if (!n) return false;
    const s = getComputedStyle(n);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    const r = n.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  if (onScreen(el)) return '';           // ordinary select — click it directly

  let box = el.parentElement;
  for (let hop = 0; box && hop < 4; hop++, box = box.parentElement) {
    const cands = box.querySelectorAll(
      '[role="combobox"], [class*="select"], [class*="chosen"], [class*="dropdown"],' +
      'button, input[type="text"], a[href="#"], span[tabindex], div[tabindex]');
    for (const c of cands) {
      if (c === el || !onScreen(c)) continue;
      // Tag it so Playwright can address it unambiguously — class names on
      // these widgets are neither unique nor stable.
      c.setAttribute('data-autofill-trigger', fid);
      return `[data-autofill-trigger="${fid}"]`;
    }
  }
  return '';
}
"""


def _wait_for_options(page: Page, timeout_ms: int = 5000) -> bool:
    """Waits for the widget's option list to populate after typing. Best
    effort: returns False on timeout and the caller falls back to a fixed
    pause, because the option markup varies and a hard failure here would be
    worse than a slightly-too-early Enter."""
    try:
        page.wait_for_function(
            """() => document.querySelectorAll(
                 '[role="option"], li.ui-menu-item, .ui-autocomplete li, [class*="option"]'
               ).length > 0""",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


def fill_avature_dropdown(page: Page, locator: Locator, text: str, field_id: str) -> bool:
    """Fills a dynamic Avature dropdown: click it, type the search text, press
    Enter. Returns whether the widget is DISPLAYING that exact label
    afterward — the caller reports False as unmapped rather than assuming the
    keystrokes landed.

    Enter, not a click on the rendered option, and not arrow keys. Both of
    those were tried live and neither works (see the module docstring); arrow
    keys are the dangerous one, because they silently select a neighbouring
    entry rather than doing nothing.

    page.keyboard.type rather than browser.human_type: the widget's search box
    is a different element from the combobox that was clicked, so typing into
    the located element would go to the wrong place. greenhouse._fill_combobox
    types through the keyboard for the same reason.
    """
    want = norm(text)

    before, before_src = read_dropdown_choice(page, field_id)
    if norm(before) == want:
        return True  # already answered — prefilled, or a re-scan pass

    # Click the VISIBLE widget, not the element carrying the id. For a dynamic
    # dropdown that element is the hidden <select>, and clicking a hidden
    # element does nothing — silently, which is the whole failure mode here.
    try:
        trigger_sel = page.evaluate(_TRIGGER_JS, field_id)
    except Exception:
        trigger_sel = ""
    target = page.locator(trigger_sel) if trigger_sel else locator
    if trigger_sel:
        log.debug("Dropdown %s: clicking rendered widget %s", field_id, trigger_sel)

    try:
        target.click(timeout=5000)
    except Exception as exc:
        log.warning("Dropdown %s: could not click its control (%s)", field_id, exc)
        return False
    human_pause(0.2, 0.5)
    page.keyboard.type(text, delay=random.uniform(40, 120))

    if not _wait_for_options(page):
        human_pause(0.6, 1.2)

    page.keyboard.press("Enter")
    human_pause(0.5, 1.0)

    after, after_src = read_dropdown_choice(page, field_id)
    ok = norm(after) == want

    if not ok:
        log.warning(
            "Dropdown %s did not take: wanted %r, reads back %r (source: %s)",
            field_id, text, after, after_src or "none",
        )
        return False

    if after_src == "combobox":
        # Only the search box confirmed it, and the search box may simply be
        # echoing what we typed. Say so rather than claim a verified fill.
        log.warning(
            "Dropdown %s verified only by its search box — the selection may not "
            "have committed. Check this field before submitting.", field_id,
        )

    # INFO, not debug. Which source confirms a dropdown could not be determined
    # without a live logged-in session, so the chain carries four candidates and
    # this line is how the winner gets identified from a real run's log — after
    # which the chain gets trimmed to the one that works.
    trusted, _ = read_dropdown_choice(page, field_id, trusted_only=True)
    log.info("Dropdown %s = %r (confirmed via '%s'%s)", field_id, after, after_src,
             "" if trusted else "; NOT visible to the trusted sources")
    return True


def dropdown_verification_source(page: Page, field_id: str) -> str:
    """Which source confirmed this dropdown, for tagging report lines."""
    return read_dropdown_choice(page, field_id)[1]
