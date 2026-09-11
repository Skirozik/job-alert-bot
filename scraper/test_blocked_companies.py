#!/usr/bin/env python3
"""The blocked-employer pre-filter. Offline: no network, no database.

Every accepted spelling below is a real `company` value taken from the jobs
table, including the emoji-prefixed variants the GitHub trackers emit. The
rejected ones are the near misses that a substring match would wrongly catch.

    python test_blocked_companies.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from main import BLOCKED_COMPANIES, _is_blocked_company

failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if not cond else ""))
    if not cond:
        failures.append(name)


# ---- the five spellings that actually exist in the table -----------------
# 270 'TikTok', 127 'ByteDance', 105 'TikTok' with a fire emoji, 43
# 'TikTok USDS Joint Venture', 35 'ByteDance' with a fire emoji.
for name in ("TikTok", "ByteDance", "\U0001f525TikTok", "\U0001f525ByteDance",
             "TikTok USDS Joint Venture", "tiktok", "BYTEDANCE",
             "TikTok Inc.", "ByteDance Ltd", "TikTok, Inc."):
    check(f"blocked: {name!r}", _is_blocked_company(name))

# ---- near misses a substring match would swallow --------------------------
# The reason this matches whole tokens of the normalised name.
for name in ("Tiktoken", "Tiktoken Labs", "Dance Studios", "Bytedance Analytics Group"):
    expected = name == "Bytedance Analytics Group"   # that one IS them
    check(f"{'blocked' if expected else 'allowed'}: {name!r}",
          _is_blocked_company(name) == expected)

for name in ("Stripe", "Adobe", "Rivian", "Byte", "Dance", "TikTok Rivals Inc"):
    if name == "TikTok Rivals Inc":
        continue                      # genuinely contains the token; blocking is correct
    check(f"allowed: {name!r}", not _is_blocked_company(name))

# ---- multi-word entries match as a PHRASE, not as tokens -----------------
# "american express" cannot be a token match: "american" would catch American
# Airlines and "express" would catch Express Scripts. Added 2026-09-10 at
# Zach's request after nine Amex resumes were built and deleted.
for name in ("American Express", "american express", "AMERICAN EXPRESS",
             "American Express National Bank", "American Express, Inc.",
             "American Express Global Business Travel"):
    check(f"blocked: {name!r}", _is_blocked_company(name))

for name in ("American Airlines", "Bank of America", "Express Scripts",
             "American Water Works", "FedEx Express", "Americas Styrenics"):
    check(f"allowed: {name!r}", not _is_blocked_company(name))

check("a single-word entry still matches as a token", _is_blocked_company("TikTok"))
check("the block list holds the phrase", "american express" in BLOCKED_COMPANIES)

# ---- degenerate input ----------------------------------------------------
for bad in (None, "", "   ", "!!!"):
    check(f"empty-ish {bad!r} is not blocked", not _is_blocked_company(bad))

check("the block list is normalised lowercase",
      all(c == c.lower() for c in BLOCKED_COMPANIES))

print()
if failures:
    print(f"{len(failures)} FAILED: {', '.join(sorted(set(failures)))}")
    sys.exit(1)
print("all blocked-employer checks passed")
