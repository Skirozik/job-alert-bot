"""make_norm_key: an internship must never share a dedup key with a full-time job.

On 2026-09-25 the bot saw Meta's "Production Engineering Intern" 65 times and
dropped it as a duplicate every time, because norm_role deletes "intern" and
Meta's full-time "Production Engineering" row, stored since July, held the same
key. Dedup runs before any fetch or classification, so the internship was never
read, never classified and never pushed. These tests pin the fix and every
property the old key had that the fix must keep.

Run: cd scraper && python test_norm_key.py
"""

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db

_fails = 0


def check(label, cond, why=""):
    global _fails
    if cond:
        print(f"  PASS  {label}")
    else:
        _fails += 1
        print(f"  FAIL  {label}" + (f"\n        {why}" if why else ""))


key = db.make_norm_key


print("\n-- the bug: an internship and a full-time job no longer collide --")

check("Meta's internship and its full-time twin get different keys",
      key("Meta", "Production Engineering Intern") != key("Meta", "Production Engineering"),
      f"{key('Meta', 'Production Engineering Intern')!r} vs {key('Meta', 'Production Engineering')!r}")
check("Ramp's frontend internship and full-time role get different keys",
      key("Ramp", "Software Engineer Internship, Frontend") != key("Ramp", "Software Engineer, Frontend"))
check("a plain 'Software Engineer Intern' no longer matches a full-time 'Software Engineer'",
      key("Google", "Software Engineer Intern") != key("Google", "Software Engineer"))
check("the full-time key is the old key plus |ft",
      key("Meta", "Production Engineering") == "meta|production engineering|ft")


print("\n-- every internship key is byte-identical to what it was --")
# Internship keys are load-bearing: the notification ledger, the dashboard's
# status widening and the resume builder all match stored ones. The fix must
# not move a single one.

check("Meta's internship key is unchanged",
      key("Meta", "Production Engineering Intern") == "meta|production engineering")


def old_key(company, title):
    """The key as it was before the fix — norm_role itself is unchanged."""
    return f"{db.norm_company(company)}|{db.norm_role(title)}"


FIXTURES = [
    # internships, spelled every way the sources spell them
    ("Meta", "Production Engineering Intern"),
    ("Cloudflare", "Software Engineer Intern (Fall 2026) - Austin, TX"),
    ("Cloudflare", "Software Engineer Intern - Fall 2026 - Austin - TX"),
    ("Google", "Software Engineering Intern, BS, Summer 2027"),
    ("Google", "Software Engineering Intern - BS - Summer 2027"),
    ("Acme", "Intern - Software Engineer"),
    ("Acme", "Software Engineer Intern"),
    ("Acme", "Software Engineer Internship"),
    ("GE Appliances", "Software Engineering Co-op_Summer 2027"),
    ("Acme", "Software Engineering Coop"),
    ("Acme", "Software Engineering Co op"),
    ("Palantir Technologies", "Software Engineer, Internship - Infrastructure"),
    ("Notion", "Software Engineer Intern, Summer 2027"),
    ("Notion", "Software Engineer Intern, Winter 2027"),
    # full-time jobs
    ("Meta", "Production Engineering"),
    ("Meta", "Production Engineer (University Grad)"),
    ("Ramp", "Software Engineer, Frontend"),
    ("Google", "Software Engineer"),
    ("Acme", "Senior Staff Engineer"),
    # internships marked by a word norm_role does NOT strip
    ("JPMorgan Chase", "Software Engineer Summer Analyst"),
    ("Acme", "Business Analyst Apprentice"),
    ("Acme", "Software Engineer Interns"),
    # degenerate
    ("", ""),
    ("Acme", ""),
    ("Acme", "Intern"),
    ("", "Software Engineer Intern"),
]

drift = []
for company, title in FIXTURES:
    role, stripped = db._norm_role_parts(title)
    expected = old_key(company, title) + ("" if (stripped or not role) else "|ft")
    if key(company, title) != expected:
        drift.append((company, title, key(company, title), expected))
check("each key is the old key, plus |ft exactly when no internship word was stripped",
      not drift, "; ".join(f"{c!r}/{t!r}: got {g!r}, want {w!r}" for c, t, g, w in drift))
check("every fixture with an internship word kept its old key exactly",
      all(key(c, t) == old_key(c, t) for c, t in FIXTURES if db._norm_role_parts(t)[1]))


print("\n-- what norm_role's stripping was for still holds --")

check("Intern, Internship, Co-op and Coop spellings of one internship share a key",
      len({key("Acme", t) for t in ("Software Engineering Intern", "Software Engineering Internship",
                                    "Software Engineering Co-op", "Software Engineering Coop",
                                    "Software Engineering Co op")}) == 1)
check("word order does not matter ('Intern - X' and 'X Intern')",
      key("Acme", "Intern - Software Engineer") == key("Acme", "Software Engineer Intern"))
check("the Cloudflare pair recorded in db.py still shares one key",
      key("Cloudflare", "Software Engineer Intern (Fall 2026) - Austin, TX")
      == key("Cloudflare", "Software Engineer Intern - Fall 2026 - Austin - TX"))
check("Google's 'Intern, BS, Summer 2027' pair still shares one key",
      key("Google", "Software Engineering Intern, BS, Summer 2027")
      == key("Google", "Software Engineering Intern - BS - Summer 2027"))
check("Summer and Winter postings of one role stay distinct",
      key("Notion", "Software Engineer Intern, Summer 2027")
      != key("Notion", "Software Engineer Intern, Winter 2027"))
check("two spellings of one full-time job still share a key",
      key("Meta", "Production Engineering") == key("Meta", "Production-Engineering"))

SPELLINGS = ["Software Engineer Intern", "Software Engineer Internship", "SOFTWARE ENGINEER INTERN",
             "Software Engineer Co-op", "Software Engineer Co-Op", "Software Engineer Coop",
             "Software Engineer Co op", "Software Engineer Co–op", "Software Engineer Co_op",
             "Intern - Software Engineer", "Intern, Software Engineer", "Software Engineer (Intern)",
             "Software Engineer Intern/Co-op", "  Software   Engineer   Intern  "]
check("every spelling of one internship, including en-dash and underscore co-ops, is one key",
      {key("Acme", t) for t in SPELLINGS} == {"acme|software engineer"},
      str(sorted({key("Acme", t) for t in SPELLINGS})))
FT_SPELLINGS = ["Software Engineer", "SOFTWARE ENGINEER", "Software-Engineer",
                "Software Engineer,", "(Software Engineer)"]
check("every spelling of the full-time job is one |ft key",
      {key("Acme", t) for t in FT_SPELLINGS} == {"acme|software engineer|ft"},
      str(sorted({key("Acme", t) for t in FT_SPELLINGS})))


print("\n-- the blank keys the SQL guards rely on --")
# unknown_candidates and claim_job_notification treat '' and '|' as no key at
# all. '||ft' would slip past both and make every blank row match every other.

check("a blank company and title stays '|'", key("", "") == "|", repr(key("", "")))
check("a blank title stays 'acme|'", key("Acme", "") == "acme|", repr(key("Acme", "")))
check("a title that is only 'Intern' stays 'acme|'", key("Acme", "Intern") == "acme|")
check("no key ever ends in '||ft'", not any(key(c, t).endswith("||ft") for c, t in FIXTURES))
check("None company and title also give '|'", key(None, None) == "|", repr(key(None, None)))
check("punctuation-only title stays 'acme|'", key("Acme", "!!!") == "acme|")
check("a blank company with a full-time title", key("", "Software Engineer") == "|software engineer|ft")
check("a blank company with an internship title", key("", "Software Engineer Intern") == "|software engineer")


print("\n-- documented residual collisions and |ft titles that keep their word --")
# A full-time job ABOUT internships keeps a plain key and still collides with a
# same-base internship. Measured on the live table: no instance exists, and the
# internship it could hide ("Program Manager Intern") is rejected by the senior
# filter anyway. Pinned so a change to this behaviour is a decision, not drift.
check("'Coop/Internship Program Manager' and 'Program Manager Intern' share a key (known, harmless)",
      key("Acme", "Coop Program Manager") == key("Acme", "Internship Program Manager")
      == key("Acme", "Program Manager Intern") == "acme|program manager")
for t in ("Software Engineer Interns", "Director of Internships", "Internal Tools Engineer",
          "Co-operative Education Engineer", "Software Engineer Extern", "Software Apprentice",
          "Software Engineer Summer Analyst", "Year at Palantir - Software Engineer"):
    k = key("Acme", t)
    check(f"'{t}' takes |ft yet keeps its own words, so it cannot equal a plain full-time key",
          k.endswith("|ft") and k != key("Acme", "Software Engineer"), k)


print("\n-- key shape --")
shape = [(t, key("Acme", t)) for t in SPELLINGS + FT_SPELLINGS]
check("internship keys have one '|', full-time keys two and end in |ft",
      all(k.count("|") == (2 if k.endswith("|ft") else 1) for _, k in shape),
      str([k for _, k in shape if k.count("|") != (2 if k.endswith("|ft") else 1)]))


print("\n-- GitHub-tracker rows are keyed as internships whatever the title says --")
# The trackers are internship lists; of 3,607 stored tracker rows none was
# rejected for being new-grad or full-time. A tracker title without "Intern"
# must not become collidable with the company's full-time job.
gh = {"id": "gh:0123456789abcdef", "company": "Palantir Technologies",
      "title": "Year at Palantir - Forward Deployed Software Engineer"}
check("a gh: row without an intern word keeps an internship-class key",
      db.job_norm_key(gh) == "palantir|year at palantir forward deployed software engineer",
      db.job_norm_key(gh))
check("the same title from LinkedIn or an ATS takes |ft",
      db.job_norm_key({**gh, "id": "4414769839"}).endswith("|ft")
      and db.job_norm_key({**gh, "id": "ats:ab12"}).endswith("|ft"))
check("a gh: 'Software Engineer' row matches its LinkedIn '... Intern' twin, not the full-time job",
      db.job_norm_key({"id": "gh:x", "company": "Acme", "title": "Software Engineer"})
      == key("Acme", "Software Engineer Intern")
      != key("Acme", "Software Engineer"))
check("job_norm_key equals make_norm_key for every non-tracker row",
      all(db.job_norm_key({"id": "4470115507", "company": c, "title": t}) == key(c, t)
          for c, t in FIXTURES))


print("\n-- insert_job stores the job_norm_key --")


class _UpsertTable:
    def __init__(self, sink):
        self.sink = sink

    def upsert(self, payload, **kw):
        self.sink.append(payload)
        return self

    def execute(self):
        return type("R", (), {"data": []})()


class _UpsertClient:
    def __init__(self, sink):
        self.sink = sink

    def table(self, name):
        return _UpsertTable(self.sink)


_sink: list = []
_orig = db.get_client
db.get_client = lambda: _UpsertClient(_sink)
try:
    db.insert_job({"id": "gh:feedfacefeedface", "company": "Acme", "title": "Software Engineer",
                   "location": "NYC", "url": "https://example.test/x", "tier": "APPLY",
                   "reason": "", "suggested_resume": "General"})
    db.insert_job({"id": "4301092859", "company": "Meta", "title": "Production Engineering",
                   "location": "Menlo Park", "url": "https://example.test/y", "tier": "INELIGIBLE",
                   "reason": "", "suggested_resume": "General"})
finally:
    db.get_client = _orig
stored = {row["id"]: row["norm_key"] for row in _sink}
check("insert_job writes a tracker row's internship-class key",
      stored.get("gh:feedfacefeedface") == "acme|software engineer", str(stored))
check("insert_job writes a full-time row's |ft key",
      stored.get("4301092859") == "meta|production engineering|ft", str(stored))


print("\n-- replay of the 2026-09-25 page that swallowed the Meta internship --")
# find_known_candidates against an in-memory table. The internship must come
# back unknown once its full-time twin carries its new key, while a full-time
# repost under a new id is still caught.


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table):
        self.table = table
        self.column = None
        self.values = []

    def select(self, columns):
        return self

    def in_(self, column, values):
        self.column, self.values = column, set(values)
        return self

    def execute(self):
        return _Result([r for r in self.table if r[self.column] in self.values])


class _Client:
    def __init__(self, table):
        self.rows = table

    def table(self, name):
        assert name == "jobs"
        return _Query(self.rows)


def replay(stored_ft_key):
    table = [{"id": "4301092859", "norm_key": stored_ft_key}]   # Meta's full-time row
    page = [
        {"id": "4470115507", "company": "Meta", "title": "Production Engineering Intern"},
        {"id": "4470999999", "company": "Meta", "title": "Production Engineering"},  # FT repost
    ]
    for job in page:
        job["norm_key"] = key(job["company"], job["title"])
    original = db.get_client
    db.get_client = lambda: _Client(table)
    try:
        ids, keys = db.find_known_candidates(page)
    finally:
        db.get_client = original
    return {j["id"]: (j["id"] in ids or j["norm_key"] in keys) for j in page}


after = replay("meta|production engineering|ft")
check("after the backfill, the Meta internship is NEW", after["4470115507"] is False)
check("after the backfill, a full-time repost is still KNOWN", after["4470999999"] is True)

before = replay("meta|production engineering")
check("before the backfill the internship is still dropped — why the backfill must run",
      before["4470115507"] is True)


print("\n-- the three pipelines can never drift apart --")
# scraper_beyonce/ and scraper_hassan/ carry their own copy of the key code;
# Hassan's DEPLOY.md already says this gap must be fixed "all three together".

here = Path(__file__).resolve().parent
forks = {}
for name in ("scraper_beyonce", "scraper_hassan"):
    path = here.parent / name / "db.py"
    spec = importlib.util.spec_from_file_location(f"{name}_db", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    forks[name] = mod

import ast
import inspect


def _key_code_shape(module):
    """The AST of every function and constant the key depends on, docstrings
    removed. Comments are already absent from an AST, so forks may differ in
    commentary but not in logic."""
    src = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = {"norm_company", "_norm_role_parts", "norm_role", "make_norm_key"}
    parts = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                body = body[1:]
            parts.append((node.name, ast.dump(ast.Module(body=body, type_ignores=[]))))
    parts.append(("_COMPANY_NOISE", tuple(sorted(module._COMPANY_NOISE))))
    parts.append(("_INTERN_WORDS", module._INTERN_WORDS.pattern))
    parts.append(("FULL_TIME_KEY_SUFFIX", module.FULL_TIME_KEY_SUFFIX))
    return sorted(parts)


main_shape = _key_code_shape(db)
check("the main copy's key code has all four functions",
      {n for n, _ in main_shape} >= {"norm_company", "_norm_role_parts", "norm_role", "make_norm_key"})

for name, mod in forks.items():
    check(f"{name}'s key code is structurally identical to the main copy",
          _key_code_shape(mod) == main_shape,
          "compare norm_company / _norm_role_parts / norm_role / make_norm_key and the constants")
    mismatches = [(c, t, mod.make_norm_key(c, t), key(c, t))
                  for c, t in FIXTURES if mod.make_norm_key(c, t) != key(c, t)]
    check(f"{name} computes exactly the same key for every fixture", not mismatches,
          "; ".join(f"{c!r}/{t!r}: fork {f!r}, main {m!r}" for c, t, f, m in mismatches[:5]))
    check(f"{name} uses the same suffix", getattr(mod, "FULL_TIME_KEY_SUFFIX", None) == "|ft")


print()
if _fails:
    print(f"{_fails} FAILED")
    sys.exit(1)
print("all norm_key checks passed")
