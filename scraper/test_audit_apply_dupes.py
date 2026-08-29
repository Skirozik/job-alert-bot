"""audit_apply_dupes classification, offline.

The audit only reports, so a wrong answer here cannot corrupt data -- but it
CAN send someone hunting a problem that does not exist, or hide one that does.
The tiers are what the whole report means, so they are asserted directly.

Run: cd scraper && python test_audit_apply_dupes.py
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import audit_apply_dupes as aud

_fails = 0


def check(label, cond, why=""):
    global _fails
    if cond:
        print(f"  PASS  {label}")
    else:
        _fails += 1
        print(f"  FAIL  {label}" + (f"\n        {why}" if why else ""))


def row(id, title, company="Notion", location="", status="new",
        tier="APPLY", norm_key=None, target_key=None):
    return {
        "id": id, "title": title, "company": company, "location": location,
        "status": status, "tier": tier,
        "norm_key": norm_key if norm_key is not None else f"{company.lower()}|{title.lower()}",
        "target_key": target_key, "url": "", "apply_url": "", "is_easy_apply": False,
        "found_at": "2026-08-20T00:00:00Z",
    }


def counts(to_apply, actioned):
    """classify() is pure, so assert on it directly rather than scraping the
    printed report -- the parsing was the fragile part, not the logic."""
    t = aud.classify(to_apply, actioned)
    return {k: len(v) for k, v in t.items()}


print("-- location mirror of dupes.ts --")

check("city, ST is parsed", aud._normalized_locations("New York, NY") == {"new york ny"})
check("full state name falls back to the split rule",
      "san francisco california" in aud._normalized_locations("San Francisco, California"),
      "the cityState regex needs a 2-letter uppercase code; California is not one")
check("SF and NY do not intersect",
      not (aud._normalized_locations("San Francisco, California")
           & aud._normalized_locations("New York, NY")))
check("NYC aliases to new york ny", aud._normalized_locations("NYC") == {"new york ny"})
check("a blank location yields nothing", aud._normalized_locations("") == set())
check("multi-city cells split on the middot",
      aud._normalized_locations("Atlanta, GA · Austin, TX") >= {"atlanta ga", "austin tx"})

print("\n-- '' and '|' are not identities --")

check("empty norm_key is not a key", aud._key_or_none("") is None)
check("bare pipe is not a key", aud._key_or_none("|") is None,
      "make_norm_key('','') is literally '|' -- treating it as an identity would "
      "match every blank-company row against every other")
check("a real key survives", aud._key_or_none("notion|swe") == "notion|swe")

print("\n-- tier classification --")

c = counts(
    [row("new-1", "Software Engineer Intern", location="New York, NY")],
    [row("old-1", "Software Engineer Intern", location="New York, NY", status="applied")],
)
check("same role + same city -> Tier A", c["A"] == 1 and c["B"] == 0, f"got {c}")

c = counts(
    [row("new-2", "Software Engineer Intern", location="San Francisco, California")],
    [row("old-2", "Software Engineer Intern", location="New York, NY", status="applied")],
)
check("same role + different city -> Tier B", c["B"] == 1 and c["A"] == 0,
      f"got {c} — this is the Notion case the user said is NOT a duplicate")

c = counts(
    [row("new-3", "Software Engineer Intern", location="")],
    [row("old-3", "Software Engineer Intern", location="Austin, TX", status="applied")],
)
check("a BLANK location counts as Tier A, not B", c["A"] == 1,
      "a blank location is precisely what stops dupes.ts pairing rows, so it is "
      "the finding rather than a reason to dismiss the pair")

c = counts(
    [row("new-4", "Data Intern", target_key="ashby:notion:abc")],
    [row("old-4", "Something Else", status="applied", target_key="ashby:notion:abc")],
)
check("same requisition -> Tier C", c["C"] == 1,
      "Tier C should be empty against real data; this proves the detector works")

c = counts(
    [row("new-5", "Totally Unrelated Role", company="Acme")],
    [row("old-5", "Software Engineer Intern", status="applied")],
)
check("an unmatched row is reported in no tier",
      c["A"] == 0 and c["B"] == 0 and c["C"] == 0, f"got {c}")

c = counts(
    [row("new-6", "x", norm_key="|")],
    [row("old-6", "y", status="applied", norm_key="|")],
)
check("two blank-company rows do not match each other",
      c["A"] == 0 and c["B"] == 0, f"got {c}")

print("\n-- the script cannot write --")

src = (Path(__file__).parent / "audit_apply_dupes.py").read_text()
for verb in (".update(", ".delete(", ".insert(", ".upsert(", ".rpc("):
    check(f"no {verb} anywhere in the audit", verb not in src,
          "this is the entire safety argument for running it unattended")
# Checking for the literal "--apply" would fail on the docstring that explains
# no such flag exists. The property that actually matters is that the script
# never branches on argv at all, so no flag can gate anything.
check("the script never reads sys.argv, so no flag can enable a write",
      "sys.argv" not in src,
      "a read-only tool that inspects argv is one edit away from not being one")

print(f"\n{'ALL PASS' if not _fails else str(_fails) + ' FAILED'}")
sys.exit(1 if _fails else 0)
