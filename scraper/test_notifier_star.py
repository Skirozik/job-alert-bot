"""The gold star as it actually reaches the phone.

The subtle failure this guards: notifier.py builds the Title header with
.encode("ascii", "replace"), so any emoji placed there arrives as a literal "?".
Emoji belong in the body; the title gets the WORD "GOLD".

Run: cd scraper && python test_notifier_star.py
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import notifier

_fails = 0


def check(label, cond, why=""):
    global _fails
    if cond:
        print(f"  PASS  {label}")
    else:
        _fails += 1
        print(f"  FAIL  {label}" + (f"\n        {why}" if why else ""))


sent = {}


def _fake_post(url, data=None, headers=None, timeout=None):
    sent.clear()
    sent.update(url=url, body=data.decode("utf-8"), headers=headers)
    return types.SimpleNamespace(raise_for_status=lambda: None)


notifier.requests = types.SimpleNamespace(post=_fake_post)
notifier.NTFY_TOPIC = "test-topic"

STARRED = {
    "id": "ats:1", "company": "Microsoft", "title": "Software Engineer Intern",
    "location": "Redmond, WA", "tier": "APPLY", "reason": "Clean fit",
    "suggested_resume": "General", "salary": "$60.00 per hour",
    "is_easy_apply": False, "url": "https://example.com",
}
PLAIN = {**STARRED, "company": "Obscure Widgets", "salary": None}
EASY = {**STARRED, "is_easy_apply": True}

print("-- a starred job --")

notifier.push_job(STARRED)
check("body carries the star emoji", "⭐" in sent["body"], sent["body"])
check("body says GOLD", "GOLD" in sent["body"])
check("body explains WHY it is starred", "top-tier company" in sent["body"],
      "a badge with no reason is one the user learns to ignore")
check("Tags header carries ntfy's star shortcode",
      sent["headers"]["Tags"].startswith("star,"), sent["headers"]["Tags"])
check("Title says GOLD as a word", "GOLD" in sent["headers"]["Title"])

title = sent["headers"]["Title"]
check("Title survives ascii encoding with NO '?' substitution",
      "?" not in title,
      f"an emoji in the Title becomes '?' after .encode('ascii','replace'): {title!r}")
check("Title is pure ascii", all(ord(ch) < 128 for ch in title), title)
check("Priority is still high, not urgent",
      sent["headers"]["Priority"] == "high",
      "urgent is reserved for the scraper-down canary; diluting it is worse")
check("the tier line is still present under the star",
      "APPLY" in sent["body"] and "Redmond" in sent["body"])

print("\n-- an unstarred job is completely unchanged --")

notifier.push_job(PLAIN)
check("no star emoji", "⭐" not in sent["body"])
check("no GOLD in the body", "GOLD" not in sent["body"])
check("no GOLD in the title", "GOLD" not in sent["headers"]["Title"])
check("Tags has no star prefix", not sent["headers"]["Tags"].startswith("star,"),
      sent["headers"]["Tags"])
check("still carries the tier and location",
      "APPLY" in sent["body"] and "Redmond" in sent["body"])

print("\n-- Easy Apply is never starred, even at a listed company --")

notifier.push_job(EASY)
check("no star emoji on an Easy Apply push", "⭐" not in sent["body"])
check("no GOLD in the title", "GOLD" not in sent["headers"]["Title"])
check("but the push still goes out", "APPLY" in sent["body"],
      "the gate suppresses the STAR, never the notification itself")

print(f"\n{17 - _fails} passed, {_fails} failed")
sys.exit(1 if _fails else 0)
