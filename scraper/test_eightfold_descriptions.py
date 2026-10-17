"""Eightfold career sites: the posting comes from the site's API, never its theme JSON.

WHY THIS EXISTS: on 2026-10-17 every open row from an Eightfold site (Microsoft,
Qualcomm, Boston Scientific, Eaton, John Deere, Netflix; 46 rows) had the site's
theme settings stored as its "description" -- the page renders the posting in
the browser, so _fetch_generic read '{"themeOptions": ...}' instead. The same
Microsoft rows carried speedyapply's locations ("Washington, DC", "Virginia, MN")
for jobs Microsoft lists in Redmond, WA.

The responses below copy the shape of the live ones (2026-10-17): the page names
the API's domain as "domain=microsoft.com"; position_details returns
jobDescription HTML and standardizedLocations like "Redmond, WA, US"; the older
/api/apply/v2/jobs/<id> returns job_description and a raw location string.

Runs offline -- no network, no API key, no DB.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import external_descriptions as ed
import main

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


BODY = "<p>Overview</p><p>" + "Research interns build efficient AI systems with world-class scientists. " * 8 + "</p>"
PAGE = ('<html><head><link href="https://static.vscdn.net/fonts/css/eightfold-font-base.css"></head><body>'
        # As served on 2026-10-17: the theme JSON HTML-escaped inside a hidden element.
        '<div id="branding-data" style="display: none;">{&#34;themeOptions&#34;: {&#34;customTheme&#34;: '
        '{&#34;varTheme&#34;: {&#34;accordion-body-text-color&#34;: &#34;#646464&#34;}}}}</div>'
        '<a href="/careers?domain=microsoft.com&hl=en">Jobs</a><img src="/logo?domain=microsoft.com"></body></html>')
GENERIC = "<html><body><main>" + "A normal server-rendered posting about the internship. " * 10 + "</main></body></html>"


class Resp:
    def __init__(self, status, text="", data=None):
        self.status_code, self.ok, self.text, self._data = status, 200 <= status < 300, text, data

    def json(self):
        return self._data


class Net:
    """requests.get stand-in: routes by URL, records every call."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        for prefix, resp in self.routes:
            if url.startswith(prefix):
                return resp
        return Resp(404)


def run(routes, url):
    net = Net(routes)
    saved = ed.requests.get
    ed.requests.get = net
    try:
        return ed.fetch_external_posting(url), net
    finally:
        ed.requests.get = saved


MS = "https://apply.careers.microsoft.com"
MS_URL = f"{MS}/careers/job/1970393557041731?utm_source=Simplify&ref=Simplify"


def details(locs):
    return Resp(200, data={"status": 200, "data": {"name": "Research Intern", "jobDescription": BODY,
                                                     "standardizedLocations": locs}})


print("-- position_details: description and the employer's U.S. location --")
(desc, loc), net = run([(f"{MS}/careers/job/", Resp(200, PAGE)),
                        (f"{MS}/api/pcsx/position_details", details(["Redmond, WA, US"]))], MS_URL)
check("the description is the posting, as text", desc and desc.startswith("Overview") and "<p>" not in desc, str(desc)[:80])
check("...never the theme JSON", desc is not None and "themeOptions" not in desc)
check("the location is the employer's: Redmond, WA", loc == "Redmond, WA", str(loc))
api = [c for c in net.calls if "position_details" in c[0]]
check("the API gets the position id from /careers/job/<id> (the utm query ignored)",
      api and api[0][1].get("position_id") == "1970393557041731", str(api))
check("...and the domain the page names", api and api[0][1].get("domain") == "microsoft.com", str(api))

(desc, loc), _ = run([(f"{MS}/careers/job/", Resp(200, PAGE)),
                      (f"{MS}/api/pcsx/position_details", details(["Redmond, WA, US", "Mountain View, CA, US",
                                                                   "Redmond, WA, US"]))], MS_URL)
check("several U.S. sites join with '; ' (the dashboard splits on it), duplicates once",
      loc == "Redmond, WA; Mountain View, CA", str(loc))

(desc, loc), _ = run([(f"{MS}/careers/job/", Resp(200, PAGE)),
                      (f"{MS}/api/pcsx/position_details", details(["Markham, ON, CA"]))], MS_URL)
check("a non-U.S. site keeps the tracker's location ('ON, CA' would read as California)", loc is None, str(loc))
check("...and still returns the description", desc is not None and desc.startswith("Overview"))

(desc, loc), _ = run([(f"{MS}/careers/job/", Resp(200, PAGE)),
                      (f"{MS}/api/pcsx/position_details", details(["Redmond, WA, US", "Dublin, D, IE"]))], MS_URL)
check("a mixed U.S. / foreign list keeps the tracker's location", loc is None, str(loc))

print("\n-- the older endpoint, where position_details 404s (Boston Scientific, Eaton, John Deere) --")
BS = "https://bostonscientific.eightfold.ai"
BS_URL = f"{BS}/careers/job/563602813542900?utm_source=Simplify&ref=Simplify"
bs_page = PAGE.replace("microsoft.com", "bostonscientific.com")
(desc, loc), net = run([(f"{BS}/careers/job/", Resp(200, bs_page)),
                        (f"{BS}/api/apply/v2/jobs/563602813542900",
                         Resp(200, data={"name": "R&D Software Engineer Intern", "job_description": BODY,
                                         "location": "Arden Hills US-MN, United States |  N/A"}))], BS_URL)
check("the v2 description is used", desc is not None and desc.startswith("Overview"), str(desc)[:60])
check("...but not a location in a shape it cannot read", loc is None, str(loc))
v2 = [c for c in net.calls if "/api/apply/v2/" in c[0]]
check("...asked with the page's domain", v2 and v2[0][1].get("domain") == "bostonscientific.com", str(v2))

v2loc = ed._eightfold_v2_us_location
check("v2 location, Microsoft's order: 'United States, Washington, Redmond' -> Redmond, WA",
      v2loc(["United States, Washington, Redmond"]) == "Redmond, WA")
check("v2 location, Netflix's order: 'Los Gatos,California,United States of America' -> Los Gatos, CA",
      v2loc(["Los Gatos,California,United States of America"]) == "Los Gatos, CA")
check("a city named like a state stays the city: 'United States, Minnesota, Virginia' -> Virginia, MN",
      v2loc(["United States, Minnesota, Virginia"]) == "Virginia, MN")
check("Microsoft's two formats for one place collapse to one: -> Redmond, WA",
      v2loc(["United States, Washington, Redmond", "Redmond, WA, USA"]) == "Redmond, WA")
check("several entries join with '; '", v2loc(["United States, Washington, Redmond", "United States, Virginia, Reston"])
      == "Redmond, WA; Reston, VA")
check("a foreign entry anywhere -> None", v2loc(["United States, Washington, Redmond", "India, Karnataka, Bangalore"]) is None)
check("an unreadable shape -> None", v2loc(["Arden Hills US-MN, United States |  N/A"]) is None and v2loc([]) is None)

MS_V2 = f"{MS}/careers/job/1970393556982897"
(desc, loc), _ = run([(f"{MS}/careers/job/", Resp(200, PAGE)),
                      (f"{MS}/api/apply/v2/jobs/1970393556982897",
                       Resp(200, data={"job_description": BODY, "locations": ["United States, Washington, Redmond"]}))],
                     MS_V2)
check("a Microsoft posting position_details 404s on still gets Redmond, WA from v2", loc == "Redmond, WA", str(loc))

print("\n-- both endpoints fail: nothing, rather than the theme JSON --")
(desc, loc), _ = run([(f"{MS}/careers/job/", Resp(200, PAGE))], MS_URL)
check("no description and no location", (desc, loc) == (None, None), str((desc, loc)))
(desc, loc), _ = run([(f"{MS}/careers/job/", Resp(200, PAGE)),
                      (f"{MS}/api/pcsx/position_details",
                       Resp(200, data={"data": {"jobDescription": "<p>short</p>", "standardizedLocations": ["Redmond, WA, US"]}}))],
                     MS_URL)
check("a near-empty API description counts as none (and brings no location)", (desc, loc) == (None, None), str((desc, loc)))

print("\n-- the page marker, escaped or not --")
check("the live, HTML-escaped theme JSON is recognised", bool(ed._EIGHTFOLD_PAGE.search(PAGE)))
check("...and plain JSON too", bool(ed._EIGHTFOLD_PAGE.search('{"themeOptions": {}}')))
check("...but not the word in prose", not ed._EIGHTFOLD_PAGE.search("<p>We offer themeOptions to customers</p>"))

print("\n-- other sites are untouched --")
(desc, loc), net = run([("https://jobs.example.com/", Resp(200, GENERIC))], "https://jobs.example.com/careers/job/12345678")
check("a server-rendered page still goes through the generic scrape", desc and "normal server-rendered posting" in desc)
check("...with no employer location, and no API call", loc is None and len(net.calls) == 1, str(net.calls))
desc = None
saved = ed.requests.get
ed.requests.get = Net([(f"{MS}/careers/job/", Resp(200, PAGE)),
                       (f"{MS}/api/pcsx/position_details", details(["Redmond, WA, US"]))])
try:
    desc = ed.fetch_external_description(MS_URL)
finally:
    ed.requests.get = saved
check("fetch_external_description (the backfill scripts' entry point) returns the description alone",
      isinstance(desc, str) and desc.startswith("Overview"))

print("\n-- process_job: the employer's location replaces the tracker's --")
seen = {}
saved = {k: getattr(main, k) for k in ("get_job_row", "fetch_external_posting", "classify", "insert_job")}
main.get_job_row = lambda job_id: None
main.fetch_external_posting = lambda url: (BODY, "Redmond, WA")
main.classify = lambda job: (seen.update(job), {"tier": "INELIGIBLE", "reason": "test", "suggested_resume": "General"})[1]
main.insert_job = lambda job: True
try:
    job = {"id": "gh:test1", "title": "Research Intern", "company": "Microsoft", "location": "Washington, DC",
           "url": MS_URL, "apply_url": MS_URL, "description": None, "is_easy_apply": False,
           "search_term": "github:speedyapply/2026-SWE-College-Jobs"}
    main.process_job(job)
    check("the classifier sees Redmond, WA, not the tracker's Washington, DC", seen.get("location") == "Redmond, WA",
          str(seen.get("location")))
    check("...and the stored row carries it", job["location"] == "Redmond, WA", job["location"])
    seen.clear()
    main.fetch_external_posting = lambda url: (BODY, None)
    job2 = dict(job, id="gh:test2", location="Markham, ON, Canada")
    main.process_job(job2)
    check("no employer location -> the tracker's location stands", seen.get("location") == "Markham, ON, Canada",
          str(seen.get("location")))
finally:
    for k, v in saved.items():
        setattr(main, k, v)

print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAILED'}")
sys.exit(1 if failures else 0)
