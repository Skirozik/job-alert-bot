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


def _option_variants(label) -> set:
    """The names an option legitimately answers to: its full text, and its
    leading sentence when it is phrased "Label. Long explanation...".

    IBM's skill dropdowns render whole paragraphs —
        "Extensive Experience. I am proficient in performing this skill across
         a variety of situations & settings. I need help with this skill only
         in unusually complex situations."
    Requiring that verbatim in the profile would be miserable to maintain and
    would break on any wording tweak, so "Extensive Experience" is accepted too.

    Still not substring matching. The variants are whole units, so "United
    States" cannot match "United States Minor Outlying Islands" — the failure
    this module exists to prevent. And the caller refuses to choose at all if
    two options answer to the same name.
    """
    n = norm(label)
    variants = {n}
    head = n.split(".")[0].strip()
    if head and head != n:
        variants.add(head)
    # Also the leading COMMA segment, so a profile value of "Yes" answers an
    # option worded "Yes, I am willing to relocate." IBM phrases the same
    # yes/no question as a bare Yes/No on one requisition and as a full
    # sentence on another.
    lead = n.split(",")[0].split(".")[0].strip()
    if lead and lead != n:
        variants.add(lead)
    return variants


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

  // 2a. Select2's rendered control, by its deterministic id. This is THE
  //     source on IBM — confirmed from the live DOM — so it is checked before
  //     the heuristic sweep below. An unfilled Select2 renders a
  //     .select2-selection__placeholder child ("Select an option"); its
  //     presence means nothing is chosen, which is far more reliable than
  //     pattern-matching the placeholder's text.
  let chosen = '';
  const s2 = document.getElementById('select2-' + fid + '-container');
  if (s2) {
    if (s2.querySelector('.select2-selection__placeholder')) {
      chosen = '';
    } else {
      chosen = norm(s2.getAttribute('title') || s2.textContent);
    }
  }

  // 2b. Fallback sweep, for any dynamic widget that is not Select2.
  if (!chosen && box) {
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
# THE WIDGET IS SELECT2. Confirmed from a live click failure, which named it
# outright:
#     <select id="10478" aria-hidden="true"
#             class="... select2-hidden-accessible">
#     <span id="select2-10478-container" role="textbox" aria-readonly="true"
#           class="select2-selection__rendered WizardFieldInput">
# So the control is at the deterministic id `select2-<fieldId>-container`, and
# finding it is a lookup rather than the four-way guess this used to be.
#
# The select is NOT display:none — Select2 leaves it in the layout at roughly
# 1x1px as an accessibility shim. A naive width>0 && height>0 test therefore
# calls it visible and clicks it, which is what happened: Playwright reported
# "element is visible, enabled and stable", clicked, and the select2 span
# "intercepts pointer events" for thirty seconds of retries. Require a real
# size, not merely a nonzero one.
_TRIGGER_JS = """
(fid) => {
  const el = document.getElementById(fid);
  if (!el) return '';

  // Select2's rendered control, by its deterministic id.
  const s2 = document.getElementById('select2-' + fid + '-container');
  if (s2) {
    s2.setAttribute('data-autofill-trigger', fid);
    return `[data-autofill-trigger="${fid}"]`;
  }

  const clickable = n => {
    if (!n) return false;
    const st = getComputedStyle(n);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    const r = n.getBoundingClientRect();
    // A 1x1 accessibility shim is not a control a human can click.
    return r.width > 8 && r.height > 8;
  };
  if (clickable(el)) return '';          // ordinary select — click it directly

  let box = el.parentElement;
  for (let hop = 0; box && hop < 4; hop++, box = box.parentElement) {
    const cands = box.querySelectorAll(
      '[role="combobox"], [role="textbox"], [class*="select"], [class*="chosen"],' +
      '[class*="dropdown"], button, input[type="text"], a[href="#"],' +
      'span[tabindex], div[tabindex]');
    for (const c of cands) {
      if (c === el || !clickable(c)) continue;
      c.setAttribute('data-autofill-trigger', fid);
      return `[data-autofill-trigger="${fid}"]`;
    }
  }
  return '';
}
"""

# IBM renders a sticky header that covers whatever the browser scrolls to the
# top of the viewport, so Playwright's own scroll-into-view lands the target
# underneath it and the click is intercepted — "<div class='header__wrapper'>
# ... intercepts pointer events", retried until timeout. Centring the element
# puts it clear of both the header and any sticky footer.
_SCROLL_CENTRE_JS = """
(sel) => {
  const n = document.querySelector(sel);
  if (n) n.scrollIntoView({block: 'center', inline: 'nearest'});
}
"""

# Select2 opens on mousedown, not click. Used only when a real click cannot get
# through — a genuine event sequence on the exact element, not a force-click
# that would ignore whatever is actually covering it.
_MOUSEDOWN_JS = """
(sel) => {
  const n = document.querySelector(sel);
  if (!n) return false;
  for (const type of ['mousedown', 'mouseup', 'click']) {
    n.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
  }
  return true;
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
                 '.select2-results__option, [role="option"], li.ui-menu-item,'
                 + '.ui-autocomplete li, [class*="option"]'
               ).length > 0""",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


# Select the option directly, without opening anything or typing a character.
#
# Select2 is a jQuery plugin: it does not watch the <select> for native events,
# it listens for jQuery's "change". Setting .value and dispatching a native
# CustomEvent is exactly why "setting .value does not work" was recorded during
# the original DOM survey — the value DID change, Select2 just never heard
# about it and went on rendering its placeholder.
#
# This only works when the option is already in the DOM. IBM's university,
# country and degree fields carry the AutoCompleteField class and fetch their
# options from the server on search, so their <select> is empty at load and
# there is nothing to pick — that is what the typing path is for. Fixed lists
# like "How did you hear about this opportunity" (10 options) are all present
# and need none of it.
#
# Returns 'ok' | 'no-option' (must search) | 'no-select' | 'not-applied'.
_DIRECT_SELECT_JS = """
(args) => {
  const el = document.getElementById(args.fid);
  if (!el || el.tagName !== 'SELECT') return 'no-select';
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const want = norm(args.want);

  // Same variant rule as the interactive path: an option answers to its full
  // text OR its leading sentence. Without this the skill dropdowns never
  // matched here — the profile holds "Extensive Experience" while the option's
  // DOM text is the whole paragraph — so every one of them fell through to the
  // slow open/paste/wait/click route despite having all five options already
  // in the DOM. Ambiguity refuses, exactly as it does there.
  const variants = o => {
    const full = norm(o.textContent);
    const head = full.split('.')[0].trim();
    return head && head !== full ? [full, head] : [full];
  };

  // ANY means the candidate explicitly delegated the choice for this field —
  // take the first real option. Used where a dropdown's rendered list is
  // broken (IBM's CI/CD widget paints nothing) yet the field is required, so
  // the alternative is an unsubmittable form. Still not the tool deciding
  // anything on its own: only a literal "ANY" in the profile reaches here.
  let match = null;
  if (want === 'any') {
    // Select on VALUE, not on text. IBM's CI/CD dropdown renders option rows
    // with EMPTY labels — visible in the widget as blank highlighted lines —
    // and an earlier version of this skipped any option whose text was empty,
    // which skipped every real option and left the field unanswered. What
    // makes an option real is that it submits something: a non-empty value.
    // Only the placeholder is excluded.
    for (const o of el.options) {
      const v = (o.value || '').trim();
      if (!v) continue;                                  // the placeholder row
      const t = norm(o.textContent);
      if (t && (/^(-+|select|choose|none)\\b/.test(t) || t.indexOf('select an option') === 0)) continue;
      match = o;
      break;
    }
    if (!match) return 'no-option';
  } else {
    const hits = [];
    for (const o of el.options) {
      if (variants(o).indexOf(want) !== -1) hits.push(o);
    }
    if (hits.length === 0) return 'no-option';
    if (hits.length > 1) return 'ambiguous';
    match = hits[0];
  }

  el.value = match.value;

  // Select2 is a jQuery plugin and listens for jQuery's "change", not the
  // native one. Try every place jQuery hides: the globals, and the copy
  // Select2 itself keeps when a page has called jQuery.noConflict().
  let jq = window.jQuery || window.$;
  if (!jq && el.closest) {
    const holder = document.querySelector('.select2-container');
    if (holder && holder.ownerDocument && holder.ownerDocument.defaultView) {
      const w = holder.ownerDocument.defaultView;
      jq = w.jQuery || w.$;
    }
  }
  if (jq && typeof jq === 'function') {
    try {
      jq(el).trigger('change');
      jq(el).trigger('change.select2');
    } catch (e) { /* fall through to the native events below */ }
  }
  el.dispatchEvent(new Event('input',  {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));

  // Deliberately NOT verified here. Select2 re-renders its container
  // asynchronously in response to the change event, so reading it in this same
  // synchronous call sees the OLD text and reports failure — which is why every
  // dropdown fell through to the slow open/type/click path despite the value
  // having been set correctly. The caller polls for the update instead.
  //
  // The VALUE is returned alongside the text, because a blank-labelled option
  // has no text to confirm against and the value is the only proof available.
  // Pipe-separated: an option VALUE is an Avature numeric id and never
  // contains one. (A NUL separator here put a raw 0x00 byte in the Python
  // source and made the module unimportable — same family of mistake as the
  // \b -> 0x08 corruption above.)
  return 'set:' + match.value + '|' + norm(match.textContent);
}
"""


def list_select_options(page: Page, field_id: str) -> list:
    """Every option in the field's underlying <select>, as [(value, text), ...].

    Diagnostic. A Select2 that renders an empty list can mean two very
    different things: the options genuinely do not exist, or they exist in the
    <select> and the widget is failing to paint them. The second is fixable
    from here — _try_direct_select can set the value without the UI's help —
    and the first is IBM's problem. Reading the select is how to tell.
    """
    try:
        return page.evaluate(
            """(fid) => {
                const el = document.getElementById(fid);
                if (!el || !el.options) return [];
                return Array.from(el.options).map(
                    o => [o.value, (o.textContent || '').replace(/\\s+/g, ' ').trim()]);
            }""",
            field_id,
        ) or []
    except Exception:
        return []


def _try_direct_select(page: Page, field_id: str, text: str) -> str:
    try:
        return page.evaluate(_DIRECT_SELECT_JS, {"fid": field_id, "want": text})
    except Exception as exc:
        log.debug("Dropdown %s: direct select failed: %s", field_id, exc)
        return "error"


def _direct_select_and_confirm(page: Page, field_id: str, text: str, want: str) -> bool:
    """Set the underlying <select> and confirm it stuck. Widget-agnostic.

    Works for anything backed by a <select>, which is every dynamic dropdown on
    this portal, so it is tried before any widget-specific handling. Confirmation
    is by the select's VALUE, not by the rendered label: Select2 repaints
    asynchronously, and IBM's CI/CD options carry no label at all.
    """
    outcome = _try_direct_select(page, field_id, text)

    if outcome == "ambiguous":
        log.warning("Dropdown %s: %r matches more than one option — not choosing.",
                    field_id, text)
        return False

    if not (isinstance(outcome, str) and outcome.startswith("set:")):
        log.debug("Dropdown %s: no direct selection possible (%s).", field_id, outcome)
        return False

    chosen_value, _, chosen_text = outcome[4:].partition("|")
    for _ in range(20):
        if _select_value_is(page, field_id, chosen_value):
            shown, _src = read_dropdown_choice(page, field_id)
            label = shown or chosen_text or f"(blank label, value {chosen_value})"
            if want == "any":
                log.warning("Dropdown %s: profile says ANY, so it selected %r. "
                            "Change ibm.skill_levels if that is not what you want.",
                            field_id, label)
            else:
                log.info("Dropdown %s = %r (selected directly, no typing)", field_id, label)
            return True
        page.wait_for_timeout(25)

    log.debug("Dropdown %s: value did not stick — falling back to the widget.", field_id)
    return False


def _select_value_is(page: Page, field_id: str, value: str) -> bool:
    """Whether the underlying <select> currently holds this value.

    The proof of a successful direct select, and the only proof available for
    IBM's CI/CD dropdown, whose options render with EMPTY labels — there is no
    text to read back, so the submitted value is the sole evidence.
    """
    try:
        return bool(page.evaluate(
            "(a) => { const e = document.getElementById(a.fid);"
            " return !!e && String(e.value) === String(a.v); }",
            {"fid": field_id, "v": value},
        ))
    except Exception:
        return False


def _fill_select2(page: Page, field_id: str, text: str) -> bool:
    """Drive a Select2 dropdown by its own contract rather than by keystrokes
    aimed at whatever happens to have focus.

    Select2 renders its search box and results list in a `.select2-dropdown`
    appended to <body>, NOT inside the field's container. Typing through
    page.keyboard therefore only lands if focus happened to move there, which
    is why every dropdown reported `reads back '' (source: none)`: the control
    opened, the keystrokes went nowhere, and Enter selected nothing.

    Selection is by clicking the option whose text matches EXACTLY. Enter takes
    whatever Select2 has highlighted, which is how "United States" became
    "United States Minor Outlying Islands" during the original DOM survey.
    """
    want = norm(text)
    container = f'[id="select2-{field_id}-container"]'
    if page.locator(container).count() == 0:
        return False

    # The fast path already ran in fill_avature_dropdown, for every widget
    # rather than only this one. Reaching here means it could not select — the
    # options are not in the DOM yet, which is the AutoCompleteField case this
    # interactive path exists for.

    try:
        page.evaluate(_SCROLL_CENTRE_JS, container)
    except Exception:
        pass

    opened = False
    try:
        page.locator(container).click(timeout=4000)
        opened = True
    except Exception:
        try:
            opened = bool(page.evaluate(_MOUSEDOWN_JS, container))
        except Exception:
            opened = False
    if not opened:
        log.warning("Dropdown %s: could not open the Select2 control.", field_id)
        return False

    try:
        page.wait_for_selector(".select2-dropdown, .select2-container--open", timeout=3000)
    except Exception:
        log.warning("Dropdown %s: clicked, but Select2 never opened.", field_id)
        return False

    # Type into Select2's own search box when it has one. Some instances are
    # configured without it (minimumResultsForSearch), in which case the list
    # is already complete and filtering is unnecessary.
    #
    # Pasted, not typed. browser.human_type's slow randomised cadence exists so
    # a form field an ATS is watching sees real keystrokes; this is a
    # client-side filter box inside a widget and nothing watches it, so paying
    # ~75ms/char here bought nothing and cost ~2s on "Georgia State University".
    #
    # fill() rather than assigning .value: Select2 filters on the input event,
    # so a raw assignment changes the text and triggers no search at all —
    # the same mistake as setting the <select>'s value and expecting the widget
    # to notice. keyup is dispatched too, because older Select2 builds bind
    # their search handler to that instead.
    search = page.locator(".select2-search__field")
    typed = False
    if search.count() > 0 and search.first.is_visible():
        try:
            search.first.fill(text)
            page.evaluate(
                """() => {
                    const f = document.querySelector('.select2-search__field');
                    if (!f) return;
                    f.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'a'}));
                }"""
            )
            typed = True
        except Exception as exc:
            log.debug("Dropdown %s: fill() on the search box failed: %s", field_id, exc)

        # If pasting produced nothing, the widget wants real keystrokes after
        # all. Retry by typing before giving up on the field.
        if typed and not _wait_for_results(page, timeout_ms=4000):
            log.debug("Dropdown %s: paste produced no results — retrying with keystrokes",
                      field_id)
            try:
                search.first.fill("")
                search.first.type(text, delay=random.uniform(12, 30))
            except Exception:
                pass

    # Wait for REAL options rather than a fixed pause — the AJAX-backed fields
    # answer in their own time, and a sleep is either too short (reads
    # "Searching…") or wastefully long.
    if not _wait_for_results(page):
        log.warning("Dropdown %s: options never finished loading for %r.", field_id, text)
        _close_select2(page)
        return False

    options = page.locator(".select2-results__option")
    count = options.count()
    labels, matches = [], []
    for i in range(min(count, 300)):
        try:
            label = options.nth(i).inner_text()
        except Exception:
            continue
        labels.append(label)
        if want in _option_variants(label):
            matches.append(i)

    if not matches:
        log.warning("Dropdown %s: no option matching %r. Saw: %s",
                    field_id, text, " | ".join(l.strip()[:60] for l in labels[:6]) or "(none)")
        _close_select2(page)
        return False

    if len(matches) > 1:
        # Two options answering to the same name. Picking either would be a
        # coin flip on a real application, so pick neither.
        log.warning("Dropdown %s: %r matches %d options ambiguously — not choosing. %s",
                    field_id, text, len(matches),
                    " | ".join(labels[i].strip()[:50] for i in matches[:3]))
        _close_select2(page)
        return False

    exact_idx = matches[0]

    try:
        options.nth(exact_idx).click(timeout=3000)
    except Exception as exc:
        log.warning("Dropdown %s: could not click the matching option (%s).", field_id, exc)
        _close_select2(page)
        return False

    # Poll for the container to update rather than sleeping a fixed interval —
    # Select2 re-renders in a few milliseconds, so this usually returns on the
    # first check instead of burning half a second per dropdown.
    for _ in range(20):
        after, _src = read_dropdown_choice(page, field_id)
        if norm(after) == want:
            return True
        page.wait_for_timeout(50)
    log.warning("Dropdown %s: clicked %r but the widget still reads %r.",
                field_id, text, after)
    return False


# Are the results REAL, or is Select2 still fetching them?
#
# The AutoCompleteField dropdowns (country, university, degree) load their
# options over AJAX, and Select2 renders a "Searching…" placeholder as a result
# row while the request is in flight. Reading the list at that moment sees one
# option called "Searching…" and nothing else — which is exactly what the run
# reported: `no option matching 'United States'. Saw: Searching…`. The options
# were fine; they simply had not arrived yet.
_RESULTS_READY_JS = """
() => {
  const opts = document.querySelectorAll('.select2-results__option');
  if (!opts.length) return false;
  for (const o of opts) {
    if (o.classList.contains('loading-results')) return false;
    const t = (o.textContent || '').trim().toLowerCase().replace(/[.…]+$/, '');
    if (t === 'searching' || t === 'loading' || t === 'please wait') return false;
  }
  return true;
}
"""


def _wait_for_results(page: Page, timeout_ms: int = 6000) -> bool:
    """Waits for real options, not the loading placeholder."""
    try:
        page.wait_for_function(_RESULTS_READY_JS, timeout=timeout_ms)
        return True
    except Exception:
        return False


def _close_select2(page: Page) -> None:
    """Leave no dropdown hanging open over the next field."""
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


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

    # Direct selection FIRST, for every widget — not just Select2.
    #
    # This lives here rather than inside _fill_select2 because it depends only
    # on there being a <select> behind the widget, which is true of all of
    # them. Nesting it in the Select2 branch meant a dropdown with no
    # select2-<id>-container never got the fast path OR the ANY handling, and
    # "ANY" was typed into a search box as if it were a country name —
    # exactly what "wanted 'ANY', reads back ''" was reporting for 10542-10.
    if _direct_select_and_confirm(page, field_id, text, want):
        return True

    # Select2, driven by its own contract.
    if page.locator(f'[id="select2-{field_id}-container"]').count() > 0:
        ok = _fill_select2(page, field_id, text)
        if ok:
            after, src = read_dropdown_choice(page, field_id)
            log.info("Dropdown %s = %r (confirmed via '%s')", field_id, after, src)
        return ok

    # A dropdown with no rendered options and no Select2 wrapper cannot be
    # searched, so ANY has nowhere left to go. Say so precisely rather than
    # typing the literal word into it.
    if want == "any":
        log.warning("Dropdown %s: profile says ANY, but its <select> holds no "
                    "selectable option. Nothing to choose.", field_id)
        return False

    # Click the VISIBLE widget, not the element carrying the id. For a dynamic
    # dropdown that element is the hidden <select>, and clicking a hidden
    # element does nothing — silently, which is the whole failure mode here.
    try:
        trigger_sel = page.evaluate(_TRIGGER_JS, field_id)
    except Exception:
        trigger_sel = ""

    if trigger_sel:
        # Centre it first — Playwright's own scroll parks the element under
        # IBM's sticky header, where every click is intercepted.
        try:
            page.evaluate(_SCROLL_CENTRE_JS, trigger_sel)
            human_pause(0.2, 0.4)
        except Exception:
            pass

    target = page.locator(trigger_sel) if trigger_sel else locator
    opened = False
    try:
        target.click(timeout=4000)
        opened = True
    except Exception as exc:
        log.debug("Dropdown %s: real click blocked (%s) — dispatching mousedown",
                  field_id, str(exc).splitlines()[0][:90])

    if not opened and trigger_sel:
        # Select2 opens on mousedown. Dispatching the real event sequence on
        # the exact element beats a force-click, which would punch through
        # whatever is covering it without knowing what that is.
        try:
            opened = bool(page.evaluate(_MOUSEDOWN_JS, trigger_sel))
        except Exception:
            opened = False

    if not opened:
        log.warning("Dropdown %s: could not open its control.", field_id)
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
