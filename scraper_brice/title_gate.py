"""Title pre-filter for the Brice pipeline: which new listings may cost a description fetch and a Claude call.

Returns None (pass to the classifier) or the short name of the rule that dropped the title. A LinkedIn drop is
stored as INELIGIBLE with reason "Pre-filtered: <rule>", so a false drop is invisible on the dashboard while a
false pass costs one classification (~$0.003) -- when in doubt, pass.

Adapted from the field study's proposed gate (measured on real LinkedIn and ATS titles in these families):
  * an engineer-level floor: help desk / service desk / desktop support never pass; a technician title passes
    only if it also names an engineer role;
  * no cohort-year rule: the rubric judges graduation windows;
  * 'mid' is a level only as mid-level / mid-career / mid-senior / "Mid to ..." or a trailing "Mid"
    ("SE Desk - Mid West" and "Mid-Market" are regions and segments);
  * a range that starts at an entry level ("I/II", "Junior/Mid", "Entry to Mid") passes;
  * a strong new-grad or program marker outranks a level suffix or an architect noun, never a seniority word;
  * pure-sales titles drop only while config.DROP_PURE_SALES holds.
"""
import re
from typing import Optional

from config import DROP_PURE_SALES   # module attribute; tests patch title_gate.DROP_PURE_SALES
import families as F

INTERN_RE = re.compile(
    r"\bintern(?:ship)?s?\b|\bco[\s-]?ops?\b|\bsummer\s+(?:analyst|associate|intern|program)\b|\bextern(?:ship)?s?\b",
    re.I)

PURE_SALES_RE = re.compile(
    r"\baccount\s+executive\b|\b(?:sales|business|account)\s+development\b|\b(?:sdr|bdr|adr|ae)\b|"
    r"\baccount\s+manager\b|\bsales\s+rep(?:resentative)?\b|\bterritory\s+(?:sales\s+)?manager\b|\bsales\s+manager\b|"
    r"\bsales\s+associate\b|\bsales\s+executive\b|\baccount\s+director\b|\brelationship\s+(?:banker|manager)\b|"
    r"\binside\s+sales\b(?!\s+engineer)|\bsales\s+specialist\b|\bpartner\s+manager\b|\bchannel\s+sales\b",
    re.I)
TECH_MARKER_RE = re.compile(
    r"\bengineer(?:ing)?\b|\btechnical\b|\bsolutions?\s+(?:consultant|architect)\b|\bpre[\s-]?sales\b|\bsystems\b",
    re.I)

FLOOR_ALWAYS_RE = re.compile(
    r"\bhelp[\s-]*desk\b|\bservice[\s-]*desk\b|\bdesktop\s+support\b|\bdeskside\b"
    r"|\b(?:it|computer|pc|end[\s-]?user|desktop)\s+support\s+(?:specialist|technician|tech|representative|rep|agent)\b",
    re.I)
FLOOR_TECHNICIAN_RE = re.compile(
    r"\btechnicians?\b"
    r"|\b(?:it|noc|field|network|desktop|computer|pc|bench|repair|service|installation|install|cable|cabling"
    r"|low[\s-]?voltage|telecom|fiber|data\s*cent(?:er|re)|datacent(?:er|re))\s+techs?\b",
    re.I)
ENGINEER_RE = re.compile(r"\bengineer(?:ing)?\b", re.I)

STRONG_ENTRY_RE = re.compile(
    r"\bnew\s+(?:college\s+)?grad(?:uate)?s?\b|\brecent\s+(?:college\s+)?grad(?:uate)?s?\b"
    r"|\b(?:university|college)\s+grad(?:uate)?s?\b|\bncg\b|\bearly[\s-]+(?:in[\s-]+)?career\b|\bemerging\s+talent\b"
    r"|\bcampus\b|\bclass\s+of\s+20\d\d\b|\b20\d\d\s+(?:start|grad(?:uate)?s?|new\s+grads?)\b"
    r"|\b(?:graduate|university|rotational|rotation|associate)\s+program\b|\bacademy\b",
    re.I)
ENTRY_RE = re.compile(
    r"\b(?:associate|junior|jr|entry[\s-]?level|graduate|university|campus|college|apprentice|trainee|rotational"
    r"|academy|tier\s*(?:1|i)|level\s*(?:1|i)|l1)\b|\b20\d\d\b"
    r"|(?<![/&\w])\b(?:i|1)\b(?![/&])",
    re.I)
MULTI_LEVEL_RE = re.compile(
    r"\b(?:i|1|jr|junior|entry(?:[\s-]level)?)\s*(?:/|-|–|&|\bor\b|\bto\b|\bthrough\b)\s*"
    r"(?:ii|iii|2|3|mid|intermediate|senior|sr|experienced)\b"
    r"|\bassociate\s*(?:/|&|\bor\b|\bto\b|\bthrough\b)\s*(?:mid|intermediate|senior|sr|experienced)\b",
    re.I)

SENIOR_RE = re.compile(
    r"\b(?:senior|sr|principal|lead|director|head\s+of|vp|vice\s+president|chief|president|distinguished|fellow"
    r"|leader|supervisor|manager|intermediate|sme|expert)\b"
    r"|\bmid[\s-]*(?:level|career|senior)\b|\bmid\s+to\b|\bmid\b(?=\s*(?:$|[(),|/]))"
    r"|\bstaff\s+(?:\w+\s+){0,2}(?:engineer|architect|consultant|analyst|administrator)\b|^\s*staff\b|\bstaff\s*[-–:,]",
    re.I)
TAM_RE = re.compile(r"\btechnical\s+account\s+manager\b", re.I)
LEVEL_RE = re.compile(
    r"\b(?:ii|iii|iv|v)\b|\b(?:level|lvl|tier)\s*(?:[2-5]|ii|iii|iv)\b|\bl[2-5]\b"
    r"|\b(?:engineer|analyst|administrator|admin|specialist|technician|tech|consultant|architect|associate)\s*[-,]?\s*[2-5]\b",
    re.I)
ARCHITECT_RE = re.compile(r"\barchitect\b", re.I)
SOL_ARCH_RE = re.compile(r"\bsolutions?\s+architect\b", re.I)


def is_entry_marked(title: str) -> bool:
    t = title or ""
    return bool(ENTRY_RE.search(t) or STRONG_ENTRY_RE.search(t))


def gate(title: str) -> Optional[str]:
    t = " ".join((title or "").split())
    if INTERN_RE.search(t):
        return "internship/co-op title"
    if DROP_PURE_SALES and PURE_SALES_RE.search(t) and not TECH_MARKER_RE.search(t):
        return "pure-sales title (AE/SDR/BDR/account manager)"
    if FLOOR_ALWAYS_RE.search(t):
        return "below the engineer floor (help desk/service desk/desktop support)"
    if FLOOR_TECHNICIAN_RE.search(t) and not ENGINEER_RE.search(t):
        return "below the engineer floor (technician)"
    if TAM_RE.search(t):
        return None if is_entry_marked(t) else "technical account manager without an entry marker"
    if MULTI_LEVEL_RE.search(t):
        return None
    if SENIOR_RE.search(t):
        return "seniority/leadership title"
    if STRONG_ENTRY_RE.search(t):
        return None            # a new-grad/program marker outranks a level suffix or an architect noun
    if LEVEL_RE.search(t):
        return "level II+/2+ title"
    if ARCHITECT_RE.search(t) and not SOL_ARCH_RE.search(t) and not is_entry_marked(t):
        return "architect title other than solutions architect"
    return None


ALLOWED_FAMILIES = {"SALES_SOLUTIONS", "NETWORK_INFRA", "DATA_CENTER", "SECURITY", "SUPPORT_ENG", "SYSTEMS_IT",
                    "ENDPOINT_ITSUP"}
EXCLUDED_SUBFAMILIES = {("ENDPOINT_ITSUP", "it_support_helpdesk")}     # owner floor
FAMILY_RANK = {"SALES_SOLUTIONS": 0, "NETWORK_INFRA": 1, "PROGRAM": 2, "DATA_CENTER": 3, "SYSTEMS_IT": 4,
               "SUPPORT_ENG": 5, "ENDPOINT_ITSUP": 6, "SECURITY": 7}

# Early-career PROGRAM titles with no family noun (AT&T "Technology Development Program", Kyndryl "Early Career
# Consult Program - Network Support Associate", Palo Alto "Academy Systems Engineer", Anduril "Entry Level Systems
# Engineer, C2 Integration" -- whose "New Grad" twin already passed). ATS rows only.
PROGRAM_RE = re.compile(
    r"\b(?:development|rotational|rotation|graduate|university|campus|associate|academy|leadership\s+development"
    r"|early[\s-]+career(?:\s+\w+)?)\s+program\b"
    r"(?!\s+(?:manager|engineer|director|lead|coordinator|specialist|analyst|officer|administrator|associate)\b)"
    r"|\bacademy\b|\bnew\s+(?:college\s+)?grad(?:uate)?s?\b|\b(?:university|college|recent)\s+grad(?:uate)?s?\b"
    r"|\bncg\b|\bearly[\s-]+(?:in[\s-]+)?career\b|\bemerging\s+talent\b|\bentry[\s-]+level\b",
    re.I)
# A program title that names a role outside the families is not passed through.
PROGRAM_EXCLUDE_RE = re.compile(
    r"\b(?:software|firmware|hardware|process|test|product|industrial|quality|manufacturing|mechanical|electrical"
    r"|civil|chemical|materials|reliability|yield|device|packaging|equipment|facilities|layout|validation|optical"
    r"|rf|analog|photonics|thermal|power|controls?|robotics|aerospace|flight|space|orbital|propulsion|avionics"
    r"|structural|gnc|mission|eda|cad|etch|lithography|photomask|failure\s+analysis|sustaining|metrology|creative"
    r"|news|research|supplier|sourcing|category|finance|financial|accounting|audit\w*|tax|actuarial|marketing"
    r"|human\s+resources|hr|people|legal|supply\s+chain|procurement|design\w*|nursing|clinical|verification"
    r"|pharmacy|retail|store|data\s+scien\w*|machine\s+learning|ml|ai|developer|scientist)\b",
    re.I)
# Sales-program titles are pure sales while DROP_PURE_SALES holds.
PROGRAM_SALES_RE = re.compile(r"\bsales\b|\baccount\s+rep\w*|\bprosales\b", re.I)
# jobright's Support list: only support-ENGINEER titles (owner floor; the list is 5,000+ rows a day).
SUPPORT_ENGINEER_RE = re.compile(
    r"\b(?:support|escalation|technical\s+services|technical\s+consulting|customer\s+reliability)\s+engineer(?:ing)?\b",
    re.I)
# ...a rule about support titles, so it leaves the engineering families alone: Google's "Customer Solutions
# Engineer, Compute, Google Cloud" was posted only on the Support list. Support-type and unplaced titles still
# need a support-ENGINEER noun there, and so do SECURITY and DATA_CENTER: on that list they were retail and
# visitor-control security and data-center logistics (2026-09-30 README).
SUPPORT_LIST_OPEN_FAMILIES = {"SALES_SOLUTIONS", "NETWORK_INFRA", "SYSTEMS_IT"}


def source_gate(title, company, location, url="", *, support_list=False, program_passthrough=True):
    """(keep: bool, label). label = family / "PROGRAM" when kept, else the drop rule. Listing fields only."""
    t = " ".join((title or "").split())
    if F.is_us(location, url, t) is False:
        return False, "non-US location"
    fam, sub = F.classify_family(t, company)
    if support_list and fam not in SUPPORT_LIST_OPEN_FAMILIES and not SUPPORT_ENGINEER_RE.search(t):
        return False, "support list: not a support-engineer title"
    if fam in ALLOWED_FAMILIES and (fam, sub) not in EXCLUDED_SUBFAMILIES:
        label = fam
    elif (program_passthrough and fam is None and PROGRAM_RE.search(t) and not PROGRAM_EXCLUDE_RE.search(t)
          and not (DROP_PURE_SALES and PROGRAM_SALES_RE.search(t))):
        label = "PROGRAM"
    else:
        return False, "off-family"
    rule = gate(t)
    if rule:
        return False, rule
    return True, label
