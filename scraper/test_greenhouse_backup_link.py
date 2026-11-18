#!/usr/bin/env python3
"""The Greenhouse backup link on alerts (notifier.greenhouse_form_url and push_job).

What it guards: Airbnb's Software Engineering Intern was pushed at 05:01 UTC on 2026-11-18, a
minute after Greenhouse listed it, and the link (careers.airbnb.com/positions/8360478?gh_jid=...)
was a 404 because Airbnb's own site had not built the page yet. Greenhouse's embedded form for the
same posting was live. Offline: the ntfy post and the page check are both stubbed.

Run: cd scraper && python test_greenhouse_backup_link.py
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import notifier

_fails = 0
_ran = 0


def check(label, cond, why=""):
    global _fails, _ran
    _ran += 1
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"\n        {why}" if not cond and why else ""))
    if not cond:
        _fails += 1


sent = {}
page = {"status": 200, "raise": None}
checked = []


def _fake_post(url, data=None, headers=None, timeout=None):
    sent.clear()
    sent.update(url=url, body=data.decode("utf-8"), headers=headers)
    return types.SimpleNamespace(raise_for_status=lambda: None)


def _fake_get(url, **kw):
    checked.append(url)
    if page["raise"]:
        raise page["raise"]
    return types.SimpleNamespace(status_code=page["status"], close=lambda: None)


notifier.requests = types.SimpleNamespace(post=_fake_post, get=_fake_get)
notifier.NTFY_TOPIC = "test-topic"

AIRBNB_PAGE = "https://careers.airbnb.com/positions/8360478?gh_jid=8360478"
AIRBNB_FORM = "https://job-boards.greenhouse.io/embed/job_app?for=airbnb&token=8360478"
AIRBNB = {
    "id": "ats:4f88d5cba74c4690", "company": "Airbnb", "title": "Software Engineering Intern (BS/MS)",
    "location": "San Francisco, California", "tier": "APPLY", "reason": "Fit", "suggested_resume": "General",
    "is_easy_apply": False, "url": AIRBNB_PAGE, "apply_url": AIRBNB_PAGE,
}

print("-- which postings get a backup link --")
check("Airbnb's company-site link gives Greenhouse's form for the same posting",
      notifier.greenhouse_form_url(AIRBNB) == AIRBNB_FORM, notifier.greenhouse_form_url(AIRBNB))
hosted = {**AIRBNB, "url": "https://job-boards.greenhouse.io/airbnb/jobs/123?gh_jid=123",
          "apply_url": "https://job-boards.greenhouse.io/airbnb/jobs/123?gh_jid=123"}
check("a link already on Greenhouse needs no backup", notifier.greenhouse_form_url(hosted) is None)
check("a company not on a Greenhouse board gets none, even with gh_jid in the link",
      notifier.greenhouse_form_url({**AIRBNB, "company": "Some Startup"}) is None)
non_gh = next(c for c, v in notifier.ATS_COMPANIES.items() if v.get("platform") != "greenhouse")
check(f"a company on another ATS ({non_gh}) gets none",
      notifier.greenhouse_form_url({**AIRBNB, "company": non_gh}) is None)
check("a company-site link without gh_jid gets none",
      notifier.greenhouse_form_url({**AIRBNB, "url": "https://careers.airbnb.com/positions/8360478",
                                    "apply_url": None}) is None)
linkedin = {**AIRBNB, "id": "li:1", "url": "https://www.linkedin.com/jobs/view/4450000001/"}
check("a LinkedIn row whose apply link is Airbnb's page still gets the form (from apply_url)",
      notifier.greenhouse_form_url(linkedin) == AIRBNB_FORM, notifier.greenhouse_form_url(linkedin))

print("\n-- the company page is up: tap opens it, the form is a button --")
page.update({"status": 200, "raise": None})
checked.clear()
notifier.push_job(AIRBNB)
h = sent["headers"]
check("the page was checked once", checked == [AIRBNB_PAGE], str(checked))
check("tap still opens Airbnb's page", h["Click"] == AIRBNB_PAGE, h["Click"])
check("a 'Greenhouse form' button opens the form", h.get("Actions") == f"view, Greenhouse form, {AIRBNB_FORM}",
      h.get("Actions"))
check("the body carries the form link too", AIRBNB_FORM in sent["body"], sent["body"])

print("\n-- the company page 404s (the 2026-11-18 case): tap opens the form --")
page.update(status=404)
notifier.push_job(AIRBNB)
h = sent["headers"]
check("tap opens Greenhouse's form", h["Click"] == AIRBNB_FORM, h["Click"])
check("the company page is kept as a button", h.get("Actions") == f"view, Company page, {AIRBNB_PAGE}",
      h.get("Actions"))
check("the body says why", "isn't up yet" in sent["body"], sent["body"])
page.update(status=410)
notifier.push_job(AIRBNB)
check("410 Gone counts as missing too", sent["headers"]["Click"] == AIRBNB_FORM)

print("\n-- a page that cannot be checked is treated as up --")
for status in (403, 429, 500):
    page.update(status=status)
    notifier.push_job(AIRBNB)
    check(f"HTTP {status}: tap keeps the company page, form stays a button",
          sent["headers"]["Click"] == AIRBNB_PAGE and "Greenhouse form" in sent["headers"].get("Actions", ""))
page["status"] = 200
page["raise"] = TimeoutError("timed out")
notifier.push_job(AIRBNB)
check("a timeout: tap keeps the company page", sent["headers"]["Click"] == AIRBNB_PAGE)
page["raise"] = None

print("\n-- everything else is unchanged --")
checked.clear()
notifier.push_job(linkedin)
check("a LinkedIn link is never fetched", checked == [], str(checked))
check("…tap opens LinkedIn, with the form as a button",
      sent["headers"]["Click"] == linkedin["url"] and AIRBNB_FORM in sent["headers"].get("Actions", ""))
checked.clear()
plain = {**AIRBNB, "company": "Some Startup", "url": "https://example.com/job/1", "apply_url": None}
notifier.push_job(plain)
check("a non-Greenhouse alert has no Actions header and no page check",
      "Actions" not in sent["headers"] and checked == [], str(sent["headers"]))
check("…and no backup line", "404" not in sent["body"] and "Greenhouse" not in sent["body"], sent["body"])
notifier.push_job(AIRBNB)
check("every header is plain ascii (ntfy reads headers as latin-1)",
      all(all(ord(ch) < 128 for ch in v) for v in sent["headers"].values()), str(sent["headers"]))

print(f"\n{_ran - _fails} passed, {_fails} failed")
sys.exit(1 if _fails else 0)
