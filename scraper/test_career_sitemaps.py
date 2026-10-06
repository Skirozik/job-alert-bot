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

print()
if _fails:
    print(f"{_fails} FAILED"); sys.exit(1)
print("all career sitemap checks passed")
