"""IBM (Avature) application filler.

Measured live (2026-08-16) against careers.ibm.com jobIds 127789 and 129258.
Most of what is encoded here cannot be rediscovered without a logged-in IBM
session, so it is written down rather than derived.

WHY THIS IS WORTH HAVING AT ALL: Avature prefills name, address, education and
employment from the saved candidate profile, so only ~15 screening answers are
genuinely new per application — and they are IDENTICAL across every IBM
posting, because the portal's field ids are global rather than per-requisition
(8989/8991 recurred byte-identical across two unrelated jobIds). So the value
is in the `ibm:` block of application_profile.yaml, not in this DOM walking.

FIELD ID CONVENTIONS
  Radio groups   name is a numeric field id, element id is <name>_<value>,
                 and value 37 == Yes, 38 == No consistently portal-wide.
  Repeat groups  <groupId>-<colId>-<rowIndex>, plus a HIDDEN
                 <groupId>-<colId>-sample template row that must never be
                 filled — writing to it corrupts the submission.

WHY A FILL/RESCAN LOOP RATHER THAN ONE PASS: answering a question spawns new
required questions. Two chains confirmed on the 60% step:
    10480 authorized to work = Yes
        -> reveals 10774 will you require sponsorship
        -> reveals 10481 "I certify" checkbox
    10498 attended university = Yes
        -> reveals 10499 country -> 10500 university
        -> reveals 18199 degree -> specialization
platforms/greenhouse.py iterates the form once, which would leave every
revealed field blank. This module rescans until a pass fills nothing new.

WHAT THIS MODULE WILL NOT DO
  - click Submit/Apply/Finish (advance.py owns that guard)
  - answer a question the profile has no data for
  - touch the EEO / self-identification block
  - touch any -sample template row
"""

import logging
import re
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from autofill.browser import human_pause, human_type
from autofill.field_matcher import match_field
from autofill.profile_loader import resolve_resume_path
from autofill.widgets import (
    fill_avature_dropdown, fill_plain_select, is_placeholder_label, norm,
    read_dropdown_choice, read_select_choice,
)

log = logging.getLogger(__name__)

_MAX_PASSES = 6

# Radio option ids. Portal-global, but asserted against the rendered label
# before every click — see _radio_labels_agree.
_YES_OPTION = "37"
_NO_OPTION = "38"

_SOURCE_OPTIONS = {
    "Agency", "Campus", "Events", "IBM Careers website", "Job Board",
    "LimeConnect", "Other", "Recruiter", "Referral", "Social",
}


class Field(NamedTuple):
    kind: str                 # yesno|radio_label|check|select|dynamic|text|textarea|file
    key: Optional[str]        # dotted profile path, e.g. "ibm.work_authorized"
    label: str                # human-readable, for report lines
    required: bool = False
    resolver: Optional[Callable] = None   # for derived values
    option_label: Optional[str] = None    # radio_label: regex for the option to pick


def _join_locations(profile: dict) -> str:
    ibm = profile.get("ibm", {})
    parts = [ibm.get(f"location_{i}") for i in (1, 2, 3)]
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


# Key convention: for radios the key is the group NAME (that is what "is this
# answered" is asked of, since the group has several element ids). For every
# other kind it is the element id.
_FIELD_MAP = {
    # ── Step 60%: screening ──────────────────────────────────────────────
    "10478": Field("dynamic", "ibm.source", "How did you hear about IBM"),
    "10480": Field("yesno", "ibm.work_authorized", "Legally authorized to work in the US", True),
    "10774": Field("yesno", "ibm.requires_sponsorship", "Will require sponsorship", True),
    "10481": Field("check", "ibm.certify_information_accurate", "Certification of accuracy", True),
    "10498": Field("yesno", "ibm.attended_university", "Attended university"),
    "10499": Field("dynamic", "ibm.university_country", "University country"),
    "10500": Field("dynamic", "ibm.university", "University"),
    "18199": Field("dynamic", "ibm.degree", "Degree"),
    "10519": Field("text", "ibm.location_1", "Location — first choice"),
    "10520": Field("text", "ibm.location_2", "Location — second choice"),
    "10521": Field("text", "ibm.location_3", "Location — third choice"),
    "10522": Field("yesno", "ibm.consider_other_locations", "Consider other locations"),
    "10523": Field("yesno", "ibm.residence_differs_from_application", "Residential address differs"),
    "10530": Field("yesno", "ibm.worked_at_ibm_before", "Worked at IBM before"),
    "35979_month": Field("select", "ibm.birth_month", "Month of birth", True),
    "35979_day": Field("select", "ibm.birth_day", "Day of birth", True),
    "10542-1": Field("select", "ibm.willing_to_relocate", "Willing to relocate"),
    "10542-2": Field("textarea", None, "Top 3 location preferences", resolver=_join_locations),
    "10542-3": Field("text", "ibm.available_start_month_year", "Available start date"),
    "20400": Field("select", "ibm.hybrid_requirement", "Can meet hybrid requirement"),
    "21272": Field("file", None, "Resume / CV", True,
                   resolver=lambda p: None),   # handled specially, needs the job dict

    # ── Step 40%: privacy + personal ─────────────────────────────────────
    # 20527's element id was observed as 20527_1043734. Under the <name>_<value>
    # convention that is group 20527, option 1043734 — a portal-instance option
    # id, far less trustworthy than the 37/38 pair, so the option is resolved by
    # its LABEL instead of hardcoding that number.
    "20527": Field("radio_label", "ibm.privacy_agree", "Privacy statement", True,
                   option_label=r"\bagree\b"),
    "32764": Field("select", "ibm.resident_of_china_or_south_korea",
                   "Resident of China or South Korea"),
    # 32766 (IBM-processing consent) is deliberately absent. It is hidden unless
    # 32764 == Yes, and the visibility gate in the snapshot keeps it out with no
    # special-casing needed. Listing it here would be an invitation to fill a
    # consent question that the form itself is not asking.
}

# The EEO / self-identification block. Skipped SILENTLY — not reported as
# unmapped either, because a line in the "needs your input" list implies the
# tool wanted to fill it and could not. It is the human's, entirely.
_EEO_RE = re.compile(r"^(1270[4-9]|1271[01])(_|$)|^13602(_|$)")


def _is_eeo_id(field_id: str) -> bool:
    return bool(_EEO_RE.match(field_id or ""))


def _is_sample_id(field_id: str) -> bool:
    """Hidden Avature template rows for 'Add Another' repeat groups. Filling
    one corrupts the submission, so this is checked on every field."""
    return "sample" in (field_id or "").split("-")


def _is_repeat_row(field_id: str):
    """Returns (group, col, row) for a repeat-group cell, else None.

    THE TRAP: '10542-1' is a perfectly normal two-segment field id, while
    '37299-7-2' is a repeat row. A naive `"-" in field_id` check treats
    10542-1/-2/-3 as repeat rows and silently skips three real questions —
    relocation, location preferences and start date.
    """
    parts = (field_id or "").split("-")
    if len(parts) != 3:
        return None
    group, col, row = parts
    if not row.isdigit():
        return None
    return (group, col, row)


def _radio_option_id(group: str, answer: bool) -> str:
    return f"{group}_{_YES_OPTION if answer else _NO_OPTION}"


def _lookup(profile: dict, dotted: str):
    node = profile
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


# ── Profile validation, before the browser ever launches ─────────────────

_RADIO_KEYS = [
    "work_authorized", "requires_sponsorship", "attended_university",
    "consider_other_locations", "residence_differs_from_application",
    "worked_at_ibm_before", "privacy_agree", "certify_information_accurate",
]
_TEXT_KEYS = [
    "source", "university_country", "university", "degree", "specialization",
    "willing_to_relocate", "hybrid_requirement", "resident_of_china_or_south_korea",
    "birth_month", "birth_day", "available_start_month_year",
    "location_1", "location_2", "location_3",
]


def validate_ibm_profile(profile: dict) -> list:
    """Returns a list of problems with the `ibm:` block. Run BEFORE launching
    the browser so a YAML mistake costs nothing.

    The error worth catching here is the YAML boolean trap. An unquoted `Yes`
    parses as boolean True, so `willing_to_relocate: Yes` silently becomes a
    bool and the filler would go looking for a <select> option labelled
    "True". Quoting it is the fix, and this says so by name.
    """
    problems = []
    ibm = profile.get("ibm")
    if not isinstance(ibm, dict):
        return ["No `ibm:` block in application_profile.yaml — "
                "IBM applications need one. See the plan for the schema."]

    for key in _RADIO_KEYS:
        if key not in ibm:
            problems.append(f"ibm.{key} is missing (needs a true/false value)")
        elif not isinstance(ibm[key], bool):
            problems.append(
                f"ibm.{key} must be a real YAML bool (true/false), got "
                f"{ibm[key]!r} — this one drives a radio button, not a dropdown"
            )

    for key in _TEXT_KEYS:
        if key not in ibm:
            problems.append(f"ibm.{key} is missing")
        elif isinstance(ibm[key], bool):
            problems.append(
                f"ibm.{key} is the boolean {ibm[key]!r}, but it must be the "
                f"dropdown's option TEXT. This is the YAML gotcha: an unquoted "
                f"Yes/No parses as a bool. Quote it — {key}: \"Yes\""
            )
        elif not str(ibm[key]).strip():
            problems.append(f"ibm.{key} is empty")

    source = ibm.get("source")
    if isinstance(source, str) and source not in _SOURCE_OPTIONS:
        problems.append(
            f"ibm.source is {source!r}, which is not one of IBM's options. "
            f"It is typed verbatim into a closed-list dropdown, so it must match "
            f"exactly (including case). Valid: {', '.join(sorted(_SOURCE_OPTIONS))}"
        )

    return problems


# ── DOM snapshot ─────────────────────────────────────────────────────────

# One evaluate() per pass rather than N Playwright round-trips. The exclusion
# gates run in JS so excluded fields never cross the bridge at all.
_SNAPSHOT_JS = """
() => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim();

  const labelFor = el => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return norm(l.innerText);
    }
    const wrap = el.closest('label');
    if (wrap) return norm(wrap.innerText);
    const box = el.closest('div, td, li, fieldset');
    if (box) {
      const l = box.querySelector('label, legend');
      if (l) return norm(l.innerText);
    }
    return '';
  };

  const visible = el => {
    if (el.offsetParent === null && getComputedStyle(el).position !== 'fixed') return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 || r.height > 0;
  };

  const out = [];
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const type = (el.type || '').toLowerCase();
    if (type === 'hidden' || type === 'submit' || type === 'button' || type === 'reset') continue;
    if (!el.id && !el.name) continue;

    // -sample rows: check BOTH the id and the ancestry. The fields inside a
    // template row do not always carry "sample" in their own ids, and filling
    // one corrupts the submission, so this is belt and braces on purpose.
    const fid = el.id || '';
    if (fid.split('-').includes('sample')) continue;
    if (el.closest('[id*="sample"], [class*="sample"]')) continue;

    if (el.disabled) continue;
    // Invisible fields are skipped, which is also what keeps the hidden
    // dependent 32766 out without naming it.
    if (!visible(el)) continue;

    const label = labelFor(el);
    let optionCount = 0, selectedText = '';
    if (el.tagName === 'SELECT') {
      optionCount = el.options.length;
      if (el.selectedIndex >= 0) selectedText = norm(el.options[el.selectedIndex].textContent);
    }

    out.push({
      id: fid,
      name: el.name || '',
      tag: el.tagName.toLowerCase(),
      type: type,
      role: el.getAttribute('role') || '',
      value: el.value || '',
      checked: !!el.checked,
      option_count: optionCount,
      selected_text: selectedText,
      label: label,
      required: !!el.required || el.getAttribute('aria-required') === 'true'
                || /\\*/.test(label),
      file_count: el.files ? el.files.length : 0,
    });
  }
  return out;
}
"""


def _snapshot(page) -> list:
    try:
        return page.evaluate(_SNAPSHOT_JS)
    except Exception as exc:
        log.error("Could not read the form: %s", exc)
        return []


def _logical_fields(raw: list) -> list:
    """Collapses radio groups into one logical field keyed by group name, drops
    EEO fields, and returns (key, kind_hint, rows) tuples."""
    groups, singles = {}, []
    for el in raw:
        if el["type"] == "radio":
            groups.setdefault(el["name"], []).append(el)
        else:
            singles.append(el)

    out = []
    for name, rows in groups.items():
        if _is_eeo_id(name):
            continue
        out.append((name, "radio", rows))
    for el in singles:
        key = el["id"] or el["name"]
        if _is_eeo_id(key):
            continue
        out.append((key, None, [el]))
    return out


def _is_answered(page, key: str, kind: Optional[str], rows: list) -> bool:
    """Whether this field already holds a value. This is what makes Avature's
    prefilled identity/education/employment free — already-answered fields
    never enter the to-do list, so they are never overwritten."""
    spec = _FIELD_MAP.get(key)
    kind = (spec.kind if spec else None) or kind
    el = rows[0]

    if kind in ("yesno", "radio_label") or el["type"] == "radio":
        return any(r["checked"] for r in rows)
    if kind == "check" or el["type"] == "checkbox":
        return el["checked"]
    if el["type"] == "file":
        # .value is the "C:\\fakepath\\..." sham and is truthy even when the
        # upload did not take; files.length is the real signal.
        return el["file_count"] > 0
    if kind == "dynamic":
        # NEVER the underlying <select> — a dynamic widget's select is empty at
        # load, so this would report every one of them unanswered forever and
        # the loop would refill them on every pass.
        return read_dropdown_choice(page, key)[0] != ""
    if el["tag"] == "select":
        return bool(el["value"].strip()) and not is_placeholder_label(el["selected_text"])
    return bool(el["value"].strip())


def _radio_labels_agree(page, group: str) -> bool:
    """Confirms #<group>_37 really is the Yes option before anything is clicked.

    '37 == Yes, 38 == No portal-wide' is an empirical claim from two postings.
    If it is ever flipped, this would answer "No" to "are you legally
    authorized to work in the US" on a real application — the worst available
    failure in this whole feature, and one nothing downstream would catch.
    """
    try:
        labels = page.evaluate(
            """(g) => {
                const read = v => {
                  const el = document.getElementById(`${g}_${v}`);
                  if (!el) return null;
                  const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
                         || el.closest('label');
                  return l ? l.innerText.replace(/\\s+/g,' ').trim() : '';
                };
                return {yes: read('37'), no: read('38')};
            }""",
            group,
        )
    except Exception:
        return False
    yes, no = norm(labels.get("yes")), norm(labels.get("no"))
    if not yes or not no:
        return False
    return yes.startswith("yes") and no.startswith("no")


# ── Filling one field ────────────────────────────────────────────────────

def _fill_one(page, key: str, spec: Field, value, rows: list) -> tuple:
    """Returns (ok, note). Never raises past the caller."""
    el = rows[0]

    if spec.kind in ("yesno",):
        if not isinstance(value, bool):
            return (False, "profile value is not true/false")
        if not _radio_labels_agree(page, key):
            return (False, "Yes/No option ids did not match their labels — not clicking. "
                           "Answer this one yourself.")
        loc = page.locator(f"#{_radio_option_id(key, value)}")
        if loc.count() == 0:
            return (False, "option element not found")
        loc.check()
        return (loc.is_checked(), "")

    if spec.kind == "radio_label":
        if not value:
            return (False, "profile value is false — not agreeing on your behalf")
        pattern = re.compile(spec.option_label or r".", re.I)
        for row in rows:
            if pattern.search(row["label"] or ""):
                loc = page.locator(f"#{row['id']}")
                loc.check()
                return (loc.is_checked(), "")
        return (False, f"no option matching /{spec.option_label}/ in group {key}")

    if spec.kind == "check":
        if not isinstance(value, bool):
            return (False, "profile value is not true/false")
        if not value:
            return (False, "profile value is false — leaving this attestation unticked")
        loc = page.locator(f"#{key}")
        loc.check()
        return (loc.is_checked(), "")

    if spec.kind == "select":
        loc = page.locator(f"#{key}")
        ok = fill_plain_select(loc, str(value))
        return (ok, "" if ok else f"no option matching '{value}'")

    if spec.kind == "dynamic":
        loc = page.locator(f"#{key}")
        ok = fill_avature_dropdown(page, loc, str(value), key)
        if not ok:
            return (False, f"dropdown did not accept '{value}' — check it yourself")
        return (True, "unverified — only the search box confirmed it"
                if read_dropdown_choice(page, key)[1] == "combobox" else "")

    if spec.kind in ("text", "textarea"):
        loc = page.locator(f"#{key}")
        human_type(loc, str(value))
        return (bool(loc.input_value().strip()), "")

    if spec.kind == "file":
        loc = page.locator(f"#{key}")
        loc.set_input_files(str(value))
        human_pause(1.0, 2.0)
        count = page.evaluate(
            "(fid) => { const e = document.getElementById(fid); return e && e.files ? e.files.length : 0; }",
            key,
        )
        return (count > 0, "" if count > 0 else "upload did not attach")

    return (False, f"unknown field kind '{spec.kind}'")


# ── The fill/rescan loop ─────────────────────────────────────────────────

def fill_current_step(page, job: dict, profile: dict) -> dict:
    """Fills everything mappable on the CURRENT step. Does not navigate.

    Returns {"filled": [...], "unmapped": [...], "submit_button_text": None,
             "blocking": [...]} where `blocking` lists REQUIRED fields still
    unanswered — the orchestrator refuses to advance while it is non-empty.
    """
    report = {"filled": [], "unmapped": [], "submit_button_text": None, "blocking": []}
    seen_unmapped = set()
    known_found = 0

    for pass_no in range(1, _MAX_PASSES + 1):
        raw = _snapshot(page)
        if not raw:
            break

        todo = []
        for key, kind, rows in _logical_fields(raw):
            if key in _FIELD_MAP:
                known_found += 1 if pass_no == 1 else 0
            if not _is_answered(page, key, kind, rows):
                todo.append((key, kind, rows))

        if not todo:
            log.info("Pass %d: everything on this step is answered.", pass_no)
            break

        filled_this_pass = 0
        for key, kind, rows in todo:
            spec = _FIELD_MAP.get(key)

            # Re-check visibility: a cascade triggered earlier in THIS pass can
            # hide a field that was visible when the snapshot was taken.
            try:
                if rows[0]["type"] != "radio" and not page.locator(f"#{key}").is_visible():
                    continue
            except Exception:
                continue

            if spec is not None:
                if spec.kind == "file":
                    try:
                        value = resolve_resume_path(profile, job.get("suggested_resume", "General"))
                    except Exception as exc:
                        report["unmapped"].append(f"{spec.label} ({key}) — {exc}")
                        continue
                elif spec.resolver is not None:
                    value = spec.resolver(profile)
                else:
                    value = _lookup(profile, spec.key)

                if value is None or (isinstance(value, str) and not value.strip()):
                    if key not in seen_unmapped:
                        seen_unmapped.add(key)
                        report["unmapped"].append(
                            f"{spec.label} ({key}) — nothing in the profile for {spec.key}")
                    continue

                try:
                    ok, note = _fill_one(page, key, spec, value, rows)
                except Exception as exc:
                    ok, note = False, str(exc)

                if ok:
                    shown = Path(str(value)).name if spec.kind == "file" else value
                    line = f"{spec.label} ({key}) -> {shown}"
                    if note:
                        line += f"  [{note}]"
                    report["filled"].append(line)
                    filled_this_pass += 1
                elif key not in seen_unmapped:
                    seen_unmapped.add(key)
                    report["unmapped"].append(f"{spec.label} ({key}) — {note or 'fill failed'}")

            else:
                # Unknown id. Route by label — but ONLY for free-form kinds.
                # An unknown radio or checkbox is never LLM-routed: a wrong
                # yes/no on a legal or eligibility question is the highest-
                # consequence failure available and nothing catches it.
                el = rows[0]
                label = el["label"] or key
                if el["type"] in ("radio", "checkbox"):
                    if key not in seen_unmapped:
                        seen_unmapped.add(key)
                        report["unmapped"].append(f"{label} ({key}) — yes/no question, answer it yourself")
                    continue

                matched = match_field(label, profile)
                if not matched:
                    if key not in seen_unmapped:
                        seen_unmapped.add(key)
                        report["unmapped"].append(f"{label} ({key})")
                    continue

                field_key, value = matched
                try:
                    if el["tag"] == "select":
                        ok = fill_plain_select(page.locator(f"#{key}"), str(value))
                    elif (el["role"] or "") == "combobox":
                        ok = fill_avature_dropdown(page, page.locator(f"#{key}"), str(value), key)
                    else:
                        human_type(page.locator(f"#{key}"), str(value))
                        ok = True
                except Exception as exc:
                    ok = False
                    log.debug("Unknown field %s failed: %s", key, exc)

                if ok:
                    report["filled"].append(f"{label} ({key}) -> {field_key}")
                    filled_this_pass += 1
                elif key not in seen_unmapped:
                    seen_unmapped.add(key)
                    report["unmapped"].append(f"{label} ({key}) — could not fill '{value}'")

            human_pause(0.3, 0.7)

        log.info("Pass %d: filled %d field(s).", pass_no, filled_this_pass)
        if filled_this_pass == 0:
            # A pass that changed nothing will never change anything.
            break
    else:
        log.warning("Hit the %d-pass cap with fields still unanswered.", _MAX_PASSES)

    _check_repeat_groups(page, report)
    _verify_filled(page, report)

    # Anything still unanswered AND required blocks advancement.
    for key, kind, rows in _logical_fields(_snapshot(page)):
        if _is_answered(page, key, kind, rows):
            continue
        spec = _FIELD_MAP.get(key)
        required = spec.required if spec else rows[0]["required"]
        if required:
            label = spec.label if spec else (rows[0]["label"] or key)
            report["blocking"].append(f"{label} ({key})")

    if known_found:
        log.info("%d of %d known IBM field ids were present on this step.",
                 known_found, len(_FIELD_MAP))
    return report


def _check_repeat_groups(page, report: dict) -> None:
    """Education (37299) and employment (9017) arrive prefilled from the saved
    Avature profile, which is the whole premise of this tool. If row 1 is
    empty, say so — but do NOT fill it.

    Detect-only on purpose: the sub-column semantics (-5 start month vs -4 end
    month) are inferred from a single observation, not measured, and the widget
    kinds are unconfirmed. Guessing writes wrong dates into a real submitted
    application, which is worse than asking the human to type them.
    """
    for group, what in (("37299", "Education history"), ("9017", "Employment history")):
        try:
            empty = page.evaluate(
                """(g) => {
                    const cells = Array.from(document.querySelectorAll(
                      `[id^="${g}-"]`)).filter(e => {
                        const p = e.id.split('-');
                        return p.length === 3 && /^\\d+$/.test(p[2]);
                      });
                    if (!cells.length) return null;
                    return cells.every(e => !(e.value || '').trim());
                }""",
                group,
            )
        except Exception:
            continue
        if empty:
            report["blocking"].append(
                f"{what} ({group}) is empty — fill it in yourself. This tool does "
                f"not write repeat-group rows.")


def _verify_filled(page, report: dict) -> None:
    """Re-reads every field claimed as filled and demotes any that reads back
    empty. Permanent, not test-only: a silent no-op fill is THE expected
    failure mode on this portal, and this is what converts it into a loud one
    on every run rather than something discovered after submitting."""
    raw = _snapshot(page)
    by_key = {k: (kind, rows) for k, kind, rows in _logical_fields(raw)}

    kept = []
    for line in report["filled"]:
        m = re.search(r"\(([^()]+)\)\s*->", line)
        key = m.group(1) if m else None
        if key and key in by_key:
            kind, rows = by_key[key]
            if not _is_answered(page, key, kind, rows):
                report["unmapped"].append(f"{line}  [reported filled but reads back EMPTY]")
                continue
        kept.append(line)
    report["filled"] = kept


def fill(page, job: dict, profile: dict) -> dict:
    """dispatch.get_filler contract — navigates, fills ONE step, and returns
    exactly the three documented keys.

    IBM is a multi-step wizard, so this is the degraded single-step path that
    exists so `python -m autofill.autofill <db_id>` on an IBM apply_url still
    does something sane. The real entry point is autofill_ibm.py.
    """
    page.goto(job["apply_url"], wait_until="domcontentloaded", timeout=30000)
    human_pause(2.0, 3.0)
    report = fill_current_step(page, job, profile)
    log.info("IBM is a multi-step application — this filled the current step only. "
             "Use `python -m autofill.autofill_ibm <jobId>` for the full flow.")
    return {k: report[k] for k in ("filled", "unmapped", "submit_button_text")}
