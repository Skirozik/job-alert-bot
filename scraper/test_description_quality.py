"""Description-quality guards: chrome stripping and the title-only override.

WHY THIS EXISTS: an audit of the 124 live APPLY rows on 2026-08-20 found six
whose stored "description" opened with a cookie banner and a department menu.
The job text was in there, but 1,725-5,096 characters down — on a 12,000-char
budget that truncates from the END, where eligibility rules live, that is a real
risk of a hard disqualifier never reaching the classifier.

The same audit found five rows with no description at all, labelled APPLY on the
title alone and indistinguishable in the queue from a posting read in full.

Runs offline — no network, no API key, no DB.
"""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from bs4 import BeautifulSoup

from external_descriptions import _extract_job_content, _strip_chrome

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


# The shape SAP SuccessFactors actually serves (Grainger, WEC Energy): the
# posting body sits in bare <div>s with no id or class, and the chrome that
# precedes it is in identifiable widgets.
SUCCESSFACTORS = """
<html><body>
  <div id="cookie-info">We use cookies to offer you the best possible website
    experience. Your cookie preferences will be stored in your browser's local
    storage. This includes cookies necessary for the website's operation.</div>
  <div class="menu desktop upper">About Us Why work here Benefits Locations
    Students and grads Search by Keyword Search by Location Clear</div>
  <div class="job-alert-signup">Select how often (in days) to receive an alert:
    Create Alert</div>
  <div class="col-xs-12 fontalign-left">
    <div>Job Summary The Technology Group internship is a 10-week paid program
      based in Chicago. Interns are matched to a team and own a project that
      ships. You will write code, review pull requests, and present at the end
      of the term to senior leadership and your peers.</div>
    <div>Job Responsibilities Build and maintain internal web applications using
      the team's existing stack. Participate in daily standups and sprint
      planning. Document what you build so the next intern can pick it up.</div>
  </div>
  <div class="footerRowBottom">2026 All Rights Reserved Privacy Policy</div>
</body></html>
"""

# A site that DOES mark its job container semantically.
SEMANTIC = """
<html><body>
  <div class="nav">Home Jobs Login</div>
  <div class="job-description">
    Responsibilities include building features in Python and React, writing
    tests, and shipping to production. Qualifications: currently enrolled in a
    Bachelor's program in Computer Science or a related field. Experience with
    SQL is preferred but not required for this internship position.
  </div>
  <div class="footer">Copyright 2026</div>
</body></html>
"""

CHROME_WORDS = re.compile(r"cookie|create alert|search by keyword|privacy policy", re.I)


# ---- _strip_chrome -------------------------------------------------------

soup = BeautifulSoup(SUCCESSFACTORS, "lxml")
before = soup.get_text(separator=" ", strip=True)
_strip_chrome(soup)
after = soup.get_text(separator=" ", strip=True)

check("strip_chrome shrinks the page", len(after) < len(before),
      f"before={len(before)} after={len(after)}")
check("strip_chrome drops the cookie banner", "cookie preferences" not in after)
check("strip_chrome drops the alert widget", "Create Alert" not in after)
check("strip_chrome drops the footer", "All Rights Reserved" not in after)
check("strip_chrome KEEPS the job body", "Job Summary" in after and "Job Responsibilities" in after)
check("strip_chrome keeps the whole job body", "next intern can pick it up" in after)


# ---- _extract_job_content ------------------------------------------------

soup = BeautifulSoup(SEMANTIC, "lxml")
_strip_chrome(soup)
scoped = _extract_job_content(soup)
check("extract finds a semantic container", scoped is not None)
if scoped:
    check("extracted text is the job body", "Responsibilities include" in scoped)
    check("extracted text has no chrome", not CHROME_WORDS.search(scoped), repr(scoped[:80]))

# No semantic container -> None, so the caller falls back rather than guessing.
soup = BeautifulSoup(SUCCESSFACTORS, "lxml")
_strip_chrome(soup)
check("extract returns None when nothing matches", _extract_job_content(soup) is None)

# A container that matches a selector but holds almost nothing must not win.
soup = BeautifulSoup('<html><body><main>Too short</main></body></html>', "lxml")
check("extract ignores a container under 200 chars", _extract_job_content(soup) is None)


# ---- _apply_title_only_override -----------------------------------------
# Imported late: classifier.py reads config at import time.

from classifier import _apply_title_only_override  # noqa: E402

r = _apply_title_only_override({"id": "ats:x", "description": None}, {"tier": "APPLY", "reason": "great fit"})
check("no description downgrades APPLY", r["tier"] == "APPLY_CAVEAT", r["tier"])
check("downgrade rewrites the reason", "title only" in r["reason"].lower(), r["reason"])

r = _apply_title_only_override({"id": "ats:x", "description": "   "}, {"tier": "APPLY", "reason": "x"})
check("whitespace-only description downgrades", r["tier"] == "APPLY_CAVEAT")

r = _apply_title_only_override({"id": "ats:x", "description": "short"}, {"tier": "APPLY", "reason": "x"})
check("sub-200-char description downgrades", r["tier"] == "APPLY_CAVEAT")

real = "x" * 250
r = _apply_title_only_override({"id": "ats:x", "description": real}, {"tier": "APPLY", "reason": "kept"})
check("real description leaves APPLY alone", r["tier"] == "APPLY")
check("real description leaves the reason alone", r["reason"] == "kept")

r = _apply_title_only_override({"id": "gh:x", "description": None}, {"tier": "INELIGIBLE", "reason": "grad only"})
check("INELIGIBLE is never resurrected", r["tier"] == "INELIGIBLE", r["tier"])
check("INELIGIBLE reason is untouched", r["reason"] == "grad only")

r = _apply_title_only_override({"id": "gh:x", "description": None}, {"tier": "APPLY_CAVEAT", "reason": "caveat"})
check("APPLY_CAVEAT is left as-is", r["tier"] == "APPLY_CAVEAT" and r["reason"] == "caveat")


# ---- byte hygiene --------------------------------------------------------
# This repo has shipped \b -> 0x08 BACKSPACE four times and a raw NUL once,
# both from patch scripts writing non-raw strings.

for name in ("external_descriptions.py", "classifier.py", "test_description_quality.py"):
    raw = (pathlib.Path(__file__).parent / name).read_bytes()
    bad = [b for b in (0x00, 0x08, 0x0B, 0x0C, 0x1B) if bytes([b]) in raw]
    check(f"{name} has no control characters", not bad, f"found {bad}")


print()
if failures:
    print(f"{len(failures)} FAILED: {', '.join(failures)}")
    sys.exit(1)
print("all description-quality checks passed")
