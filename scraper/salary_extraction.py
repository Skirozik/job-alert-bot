"""Regex-based salary extraction from free-text job descriptions.

Shared by classifier.py (as a deterministic fallback when the LLM's own
salary extraction comes back empty — the model doesn't reliably notice
every stated compensation figure, especially in long descriptions) and
backfill_salary_from_desc.py (for retroactively filling in already-stored
jobs).
"""

import re

# Separator between range bounds: a dash or the word "to" — NOT a character
# class, since [-–—to]+ matches the individual letters 't' and 'o' too (so it
# would wrongly match e.g. "$20oo$30").
_SEP = r'(?:[-–—]+|\s*to\s*)'

# A dollar amount — tolerates a space after the "$" (e.g. postings that
# render "$ 25.00" instead of "$25.00").
_DOLLAR = r'\$\s*[\d,]+(?:\.\d+)?'

# Salary patterns — ordered most-specific first
_PATTERNS = [
    # Range with /hr or /hour on BOTH sides: $35/hr - $45/hr
    rf'{_DOLLAR}\s*/\s*h(?:ou)?r\s*{_SEP}\s*{_DOLLAR}\s*/\s*h(?:ou)?r',
    # Range with $ and /hr or /hour (unit on the second value only):
    # $20 - $30/hr, $20.50-$35/hour
    rf'{_DOLLAR}\s*{_SEP}\s*{_DOLLAR}\s*/\s*h(?:ou)?r',
    # Range with $ and per hour: $20 - $30 per hour
    rf'{_DOLLAR}\s*{_SEP}\s*{_DOLLAR}\s*per\s*hour',
    # Range with $ and /yr or annually: $80,000 - $120,000/yr
    rf'{_DOLLAR}\s*{_SEP}\s*{_DOLLAR}\s*(?:/\s*yr|per\s*year|annually)',
    # Plain dollar range (likely annual): $80,000 - $120,000
    rf'\$\s*[\d,]{{5,}}\s*{_SEP}\s*\$\s*[\d,]{{5,}}',
    # K range: $80K - $120K, $80k–$120k
    rf'\$\d+[kK]\s*{_SEP}\s*\$\d+[kK]',
    # Single value /hr: $25/hr, $25.50 / hour
    rf'{_DOLLAR}\s*/\s*h(?:ou)?r',
    # Single value per hour
    rf'{_DOLLAR}\s*per\s*hour',
    # Single K value: $120K, $120k
    r'\$\d+[kK]',
    # Single large dollar value (annual): $120,000
    r'\$\s*[\d,]{5,}',
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]

# The last two patterns (K value, bare large dollar figure) have no hourly/
# annual anchor, so they also catch tuition reimbursement caps, signing
# bonuses, and relocation stipends. Skip a match if one of these phrases
# appears just before it.
_FALSE_POSITIVE_CONTEXT = re.compile(
    r'(tuition|reimburs|bonus|sign[- ]?on|relocation|stipend)', re.IGNORECASE
)
_UNANCHORED_PATTERN_COUNT = 2


def extract_salary(text: str) -> str | None:
    if not text:
        return None
    for idx, pattern in enumerate(_COMPILED):
        m = pattern.search(text)
        if not m:
            continue
        if idx >= len(_COMPILED) - _UNANCHORED_PATTERN_COUNT:
            context = text[max(0, m.start() - 40):m.start()]
            if _FALSE_POSITIVE_CONTEXT.search(context):
                continue
        return m.group(0).strip()
    return None
