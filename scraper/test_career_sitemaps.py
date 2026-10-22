"""career_sitemaps.py, offline: what it reads, what it opens, what it returns.

Run: cd scraper && python test_career_sitemaps.py
"""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import career_sitemaps as cs

_fails = 0


def check(label, cond, why=""):
    global _fails
    if cond:
        print(f"  PASS  {label}")
    else:
        _fails += 1
        print(f"  FAIL  {label}" + (f"\n        {why}" if why else ""))


META_SITEMAP = """<urlset>
<url><loc>https://www.metacareers.com/profile/job_details/111/</loc><lastmod>x</lastmod></url>
<url><loc>https://www.metacareers.com/profile/job_details/222/</loc><lastmod>x</lastmod></url>
<url><loc>https://www.metacareers.com/profile/job_details/333/</loc><lastmod>x</lastmod></url>
<url><loc>https://www.metacareers.com/teams/</loc></url>
</urlset>"""
APPLE_SITEMAP = """<urlset>
<url><loc>https://jobs.apple.com/en-us/details/200606145-3810/software-engineering-internships</loc></url>
<url><loc>https://jobs.apple.com/en-us/search</loc></url>
</urlset>"""
DESHAW_SITEMAP = """<urlset>
<url><loc>https://www.deshaw.com/careers/software-developer-intern-new-york-summer-2027-5894</loc></url>
<url><loc>https://www.deshaw.com/careers/choose-your-path</loc></url>
</urlset>"""
META_PAGE = (b'<html><head><script type="application/ld+json">{"@type":"JobPosting",'
             b'"title":"Software Engineering Intern","datePosted":"2026-10-01T00:00:00-07:00",'
             b'"description":"<p>Build things &amp; ship them.</p>",'
             b'"jobLocation":[{"@type":"Place","name":"Bellevue, WA"},{"@type":"Place","name":"Menlo Park, CA"}]}'
             b'</script></head><body>' + b"x" * 1000 + b"</body></html>")


class _Resp:
    def __init__(self, text=None, content=None, status=200):
        self.status_code = status
        self.text = text if text is not None else (content or b"").decode()
        self._content = content if content is not None else (text or "").encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, n):
        for i in range(0, len(self._content), n):
            yield self._content[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


opened_pages: list[str] = []


def fake_get(url, headers=None, timeout=None, stream=False):
    if "metacareers.com/jobsearch/sitemap" in url:
        return _Resp(META_SITEMAP)
    if "jobs.apple.com/sitemap" in url:
        return _Resp(APPLE_SITEMAP)
    if "deshaw.com/sitemap" in url:
        return _Resp(DESHAW_SITEMAP)
    if "/profile/job_details/" in url:
        opened_pages.append(url)
        return _Resp(content=META_PAGE)
    raise AssertionError(f"unexpected fetch: {url}")


calls: list[dict] = []


def fake_unknown(jobs, batch_size=5000):
    jobs = list(jobs)
    calls.append({"batch_size": batch_size, "keys": {j["norm_key"] for j in jobs}, "n": len(jobs)})
    return {j["id"] for j in jobs} - KNOWN


KNOWN: set[str] = set()
state: dict = {}
cs.requests.get = fake_get
cs.find_unknown_candidates = fake_unknown
cs.get_state = lambda k: state.get(k)
cs.set_state = lambda k, v: state.__setitem__(k, v)
cs.DETAIL_GAP_S = 0

print("\n-- titles read from URLs --")
check("Apple slug", cs.title_from_slug(
    "https://jobs.apple.com/en-us/details/200606145-3810/software-engineering-internships")
    == "Software Engineering Internships")
check("D. E. Shaw slug drops the requisition number", cs.title_from_slug(
    "https://www.deshaw.com/careers/software-developer-intern-new-york-summer-2027-5894")
    == "Software Developer Intern New York Summer 2027",
    cs.title_from_slug("https://www.deshaw.com/careers/software-developer-intern-new-york-summer-2027-5894"))

print("\n-- a first pass --")
out = cs.collect_listings()
by_company = {}
for j in out:
    by_company.setdefault(j["company"], []).append(j)
check("only job pages are taken from each sitemap (non-job URLs ignored)",
      len(by_company.get("Meta", [])) == 3 and len(by_company.get("Apple", [])) == 1
      and len(by_company.get("D. E. Shaw", [])) == 1, str({k: len(v) for k, v in by_company.items()}))
meta = by_company["Meta"][0]
check("Meta title comes from the page's JobPosting block", meta["title"] == "Software Engineering Intern")
check("Meta locations are summarised", meta["location"] == "Bellevue, WA +1 more", meta["location"])
check("Meta description is plain text", meta["description"] == "Build things & ship them.", repr(meta["description"]))
check("Meta posted date is kept", meta["posted_at"].startswith("2026-10-01"))
url = "https://www.metacareers.com/profile/job_details/111/"
check("ids use the ATS scheme", meta["id"] == "ats:" + hashlib.sha1(url.encode()).hexdigest()[:16])
check("rows are tagged as career-sitemap", all(j["search_term"] == "career-sitemap" for j in out))
apple = by_company["Apple"][0]
check("Apple row needs no page fetch and has no description", apple["description"] is None
      and not any("apple" in u for u in opened_pages))
check("Apple rows are U.S. (en-us sitemap)", apple["location"] == "United States")
check("no internal _source field leaks out", not any("_source" in j for j in out))
check("the new-id question goes in batches of 500", all(c["batch_size"] == 500 for c in calls), str(calls))
check("the new-id question is id-only (key '|')", all(c["keys"] == {"|"} for c in calls), str(calls))
check("the pass is stamped", cs.STATE_KEY in state)

print("\n-- the hourly gate --")
opened_pages.clear()
check("a second call inside the hour does nothing", cs.collect_listings() == [] and not opened_pages)

print("\n-- known ids are not reopened; the page cap holds --")
state.clear(); opened_pages.clear()
KNOWN.add(meta["id"])
cs.DETAIL_CAP = 1
out = cs.collect_listings()
metas = [j for j in out if j["company"] == "Meta"]
check("a known Meta id is never reopened", url not in opened_pages, str(opened_pages))
check("no more pages than DETAIL_CAP are opened", len(opened_pages) == 1, str(opened_pages))
check("capped leftovers are simply not returned yet", len(metas) == 1, str([j["url"] for j in metas]))

print("\n-- Apple students page (the sitemap does not reliably list student postings) --")
import json as _json


def apple_page(loader_data: dict) -> str:
    """A page in Apple's own embedding: the data as a JSON document inside a JS string literal."""
    return ('<html><body><div id="root"></div><script>window.__staticRouterHydrationData = JSON.parse('
            + _json.dumps(_json.dumps({"loaderData": loader_data})) + ');</script></body></html>')


def search_result(rid, slug, title, locs=("United States",)):
    return {"id": rid, "positionId": rid.split("-")[0], "postingTitle": title, "transformedPostingTitle": slug,
            "postDateInGMT": "2026-05-21T18:00:00.000Z", "locations": [{"name": n} for n in locs],
            "team": {"teamCode": "STDNT"}}


RESULTS = [search_result("200664780-3810", "machine-learning-and-artificial-intelligence-undergrad-internships",
                         "Machine Learning and Artificial Intelligence Undergrad Internships"),
           search_result("200673612-0157", "applied-data-solutions-program-internships-summer-2027",
                         "Applied Data Solutions Program, Internships \u2013 Summer 2027", ("Austin",)),
           search_result("200673612-0836", "applied-data-solutions-program-internships-summer-2027",
                         "Applied Data Solutions Program, Internships \u2013 Summer 2027", ("Cupertino",))]
SEARCH_P1 = apple_page({"search": {"searchResults": RESULTS[:2], "totalRecords": 3}})
SEARCH_P2 = apple_page({"search": {"searchResults": RESULTS[2:], "totalRecords": 3}})
DETAIL = apple_page({"jobDetails": {"jobsData": {
    "jobSummary": "<p>Join a team building ML into products.</p>", "description": "Work on real models &amp; data.",
    "minimumQualifications": "<ul><li>Pursuing a BS in Computer Science</li><li>At the end of the internship, "
                             "you must return to school</li></ul>",
    "preferredQualifications": "<ul><li>Python</li></ul>"}}})

posts, total = cs.parse_apple_search(SEARCH_P1)
check("search page: postings and the total are read from the embedded data", len(posts) == 2 and total == 3)
check("the URL is built from the posting id, in the sitemap's form (so stored rows keep their id)",
      posts[0]["url"] == "https://jobs.apple.com/en-us/details/200664780-3810/"
                         "machine-learning-and-artificial-intelligence-undergrad-internships", posts[0]["url"])
check("title and location come from the data", posts[1]["title"].startswith("Applied Data Solutions")
      and posts[1]["location"] == "Austin")
check("a page without the embedded data parses to nothing", cs.parse_apple_search("<html></html>") == ([], 0))
desc = cs.parse_apple_detail(DETAIL)
check("detail: summary, description and both qualification lists, as plain text",
      desc and desc.startswith("Join a team") and "Work on real models & data." in desc
      and "Minimum qualifications: Pursuing a BS in Computer Science" in desc
      and "Preferred qualifications: Python" in desc and "<" not in desc, repr(desc))
check("a detail page without the data gives no description", cs.parse_apple_detail("<html></html>") is None)

fetched: list[str] = []


def apple_get(url, headers=None, timeout=None, stream=False):
    fetched.append(url)
    if url == cs.APPLE_STUDENTS_URL:
        return _Resp(SEARCH_P1)
    if url == cs.APPLE_STUDENTS_URL + "&page=2":
        return _Resp(SEARCH_P2)
    if "/en-us/details/" in url:
        return _Resp(DETAIL)
    raise AssertionError(f"unexpected fetch: {url}")


cs.requests.get = apple_get
state.clear(); KNOWN.clear(); calls.clear()
KNOWN.add(cs._make_job("Apple", "x", "", "https://jobs.apple.com/en-us/details/200664780-3810/"
                       "machine-learning-and-artificial-intelligence-undergrad-internships", None, None)["id"])
out = cs.collect_apple_students()
check("both pages are read, and no more", fetched[:2] == [cs.APPLE_STUDENTS_URL, cs.APPLE_STUDENTS_URL + "&page=2"]
      and sum("search" in u for u in fetched) == 2, str(fetched))
check("a stored posting is not reopened or returned",
      not any("200664780" in u for u in fetched[2:]) and not any("200664780" in j["url"] for j in out), str(fetched))
check("each new posting's page is opened once, and it carries the description",
      len(out) == 2 and all(j["description"] and "Computer Science" in j["description"] for j in out), str(out))
check("rows use the ATS id scheme and are tagged apple-students",
      all(j["id"].startswith("ats:") and j["search_term"] == "apple-students" and j["company"] == "Apple" for j in out))
check("the dedup question carries real norm_keys (titles are known here)",
      calls and all(k != "|" for k in calls[-1]["keys"]), str(calls[-1:]))
check("the pass is stamped", cs.APPLE_STUDENTS_STATE_KEY in state)
fetched.clear()
check("a second call inside 30 minutes fetches nothing", cs.collect_apple_students() == [] and not fetched)
check("the interval is 30 minutes", cs.APPLE_STUDENTS_INTERVAL_S == 1800)


def refused(url, headers=None, timeout=None, stream=False):
    return _Resp("Forbidden", status=403)


cs.requests.get = refused
state.clear()
check("a refused page returns nothing and is still stamped (retried next interval, never around)",
      cs.collect_apple_students() == [] and cs.APPLE_STUDENTS_STATE_KEY in state)

print()
if _fails:
    print(f"{_fails} FAILED"); sys.exit(1)
print("all career sitemap checks passed")
