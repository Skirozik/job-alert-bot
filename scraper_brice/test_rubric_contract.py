"""The rubric's structural contract with classifier.py. Locally, and in CI before every run.

The rubric is gitignored (it describes a real person and this repo is public),
so this runs only where the file exists: CANDIDATE_PROFILE_PATH (env) or the
repo-root Brice_Candidate_Profile_and_Filters.md. Without it: SKIP, exit 0.
scrape_brice.yml runs it on the rubric it just wrote from the secret; a
failure stops the run before main.py, and its output is the public log.

It prints check labels and counts, NEVER rubric text.

What classifier.py relies on:
  * the tool's tier text points to a heading containing
    "INELIGIBLE — the complete list" and to labels I-1, I-2, ... -- they must
    exist, numbered contiguously, closed by the exhaustive sentence;
  * the user prompt's missing-description placeholder is quoted in the
    rubric's title-only section;
  * the cached prefix must clear the prompt-cache minimum (~5,100 tokens
    measured) and fit a GitHub secret (48 KB).

Run:  cd scraper_brice && python -X utf8 test_rubric_contract.py
      (CANDIDATE_PROFILE_PATH=/path/outside/the/repo/... to check a local draft)
"""

import testkit

testkit.block_network()

import re  # noqa: E402
import sys  # noqa: E402

import config  # noqa: E402
from testkit import check, section  # noqa: E402

path = config.CANDIDATE_PROFILE_PATH
if not path.is_file():
    print("SKIP rubric not present")
    sys.exit(0)

raw = path.read_bytes()
text = raw.decode("utf-8")
lines = text.splitlines()
headings = [(i, line.rstrip()) for i, line in enumerate(lines) if line.startswith("#")]

section("where the rubric lives")
inside_repo = testkit.REPO.resolve() in path.resolve().parents
check("a rubric inside the repo has the gitignored name",
      not inside_repo or path.name == "Brice_Candidate_Profile_and_Filters.md")

section("headings, in order")
REQUIRED = [
    "# Candidate Profile and Filters — Brice",
    "## 1. Who he is",
    "## 2. Skills and experience",
    "## 3. What he is looking for",
    "### How to tell an entry-level role from an experienced hire",
    "## 4. Target roles (priority order)",
    "## 5. Location: anywhere in the United States",
    "## 6. Work authorization and security clearance",
    "## 7. Graduation, start date and experience",
    "## 8. Employment type, staffing agencies, travel and certifications",
    "## 9. TRIAGE RUBRIC",
    "### 9.1 APPLY — clean fit",
    "### 9.2 APPLY_CAVEAT — worth applying, one reservation",
    "### 9.3 INELIGIBLE — the complete list",
    "## 10. GROUNDING RULE",
    "## 11. When the description is missing",
    "## 12. Worked examples",
]
last = -1
for want in REQUIRED:
    at = next((i for i, h in headings if h.startswith(want) and i > last), None)
    check(f"heading present, after the previous one: {want}", at is not None)
    if at is not None:
        last = at
check("no heading makes PENDING or 'Pre-filtered' a label (code states, not verdicts)",
      not any("PENDING" in h or "Pre-filtered" in h for _, h in headings))

section("the complete INELIGIBLE list")
start = next((i for i, h in headings if "INELIGIBLE — the complete list" in h), None)
check("a heading contains 'INELIGIBLE — the complete list' (em dash, as the tool text says)", start is not None)
if start is not None:
    end = next((i for i, h in headings if i > start and re.match(r"#{1,3} ", h)), len(lines))
    body = lines[start + 1:end]
    labels = [int(m.group(1)) for line in body
              if (m := re.match(r"\s*(?:[-*+]\s+|\d+\.\s+)?(?:\*\*)?I-(\d+)\b", line))]
    n = len(labels)
    check(f"conditions are labelled I-1..I-{n} contiguously, in order", labels == list(range(1, n + 1)), str(labels))
    check("...at least ten of them", n >= 10, str(n))
    check("the list is closed by the exhaustive sentence",
          "This list is exhaustive. Anything not listed is APPLY or APPLY_CAVEAT." in "\n".join(body))

section("rules the classifier depends on")
check("the classifier's missing-description placeholder is quoted, so the model can map it",
      "(not available — classify on title/company/location only)" in text)
low = text.lower()
check("no clause makes 'not an internship' a block", "not actually an internship" not in low)
check("no 'new grad or full-time' block", "new grad or full-time" not in low)
check("exactly three verdict labels are defined (APPLY / APPLY_CAVEAT / INELIGIBLE sections)",
      sum(1 for _, h in headings if re.match(r"### 9\.[123] ", h)) == 3)

section("size")
check(f"size {len(raw):,} bytes is within 20,000-48,000 (cache floor .. secret limit)", 20_000 <= len(raw) <= 48_000)

sys.exit(testkit.finish())
