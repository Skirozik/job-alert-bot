"""The fork's scaffold: config values, what linkedin.py sends, and what was copied unchanged.

Offline. linkedin.fetch_listings runs against a stubbed requests.get that
records its params, so no request leaves the machine.

Run:  cd scraper_brice && python -X utf8 test_config.py
"""

import testkit

testkit.block_network()

import ast  # noqa: E402
import difflib  # noqa: E402
import importlib  # noqa: E402
import os  # noqa: E402
import types  # noqa: E402

import config  # noqa: E402
import linkedin  # noqa: E402
from testkit import HERE, REPO, check, patched, section  # noqa: E402

section("LinkedIn search settings")
EXPECTED_TERMS = [
    "associate sales engineer", "sales engineer", "associate solutions engineer", "solutions engineer",
    "associate systems engineer", "presales engineer", "solutions consultant", "associate solutions architect",
    "junior network engineer", "network engineer", "network administrator", "NOC engineer",
    "associate network engineer", "data center network engineer", "junior systems administrator",
    "endpoint engineer", "cloud support engineer", "technical support engineer", "SOC analyst",
    "junior cybersecurity analyst", "associate security engineer", "infrastructure engineer",
]
check("22 search terms, in priority order", config.SEARCH_TERMS == EXPECTED_TERMS)
check("...all distinct", len({t.lower() for t in config.SEARCH_TERMS}) == 22)
check("...none below the engineer floor (no technician / help desk term)",
      not any(w in t.lower() for t in config.SEARCH_TERMS for w in ("technician", "help desk", "desktop support")))
check("one location: the whole United States", config.LOCATIONS == ["United States"])
check("f_E is '2,3' (Entry level + Associate)", config.LINKEDIN_EXPERIENCE_FILTER == "2,3")
check("24 h lookback", config.LOOKBACK_SECONDS == 86400)
check("10 pages per search", config.MAX_PAGES_PER_SEARCH == 10)
check("a search stops after 2 consecutive all-duplicate pages", config.ALL_DUP_PAGES_TO_STOP == 2)
check("search phase budget 25 min (from the LinkedIn pass's own start)", config.SEARCH_TIME_BUDGET_S == 25 * 60)
check("ATS sweep budget 10 min", config.ATS_SWEEP_BUDGET_S == 10 * 60)
check("...so the sweep and the searches together leave at least 10 min of the 48-min run for classifying",
      config.RUN_TIME_BUDGET_S - config.ATS_SWEEP_BUDGET_S - config.SEARCH_TIME_BUDGET_S >= 10 * 60)

section("caps and budgets")
check("per-source classification caps", config.MAX_CLASSIFY_PER_RUN == {"ats": 100, "linkedin": 180, "jobright": 100})
check("200 PENDING retries per run: a full run parked in an outage drains in two", config.RETRY_PENDING_MAX == 200
      and 2 * config.RETRY_PENDING_MAX >= sum(config.MAX_CLASSIFY_PER_RUN.values()))
check("run budget 48 min, under the 60-min workflow timeout", config.RUN_TIME_BUDGET_S == 48 * 60 < 60 * 60)
check("the search budget is inside the run budget", config.SEARCH_TIME_BUDGET_S < config.RUN_TIME_BUDGET_S)
check("pure sales dropped at the gate until the owner hears back", config.DROP_PURE_SALES is True)
check("alert throttles: 6 h, canaries 24 h", config.ALERT_THROTTLE_HOURS == 6 and config.CANARY_THROTTLE_HOURS == 24)

section("jobright lists: README fields only")
names = [n for n, _u, _s in config.JOBRIGHT_LISTS]
check("five lists", names == ["Engineering", "Sales", "Software-Engineer", "Consultant", "Support"])
check("only the Support list is limited to support-engineer titles",
      [n for n, _u, s in config.JOBRIGHT_LISTS if s] == ["Support"])
check("every list URL is a raw.githubusercontent.com README (never jobright.ai)",
      all(u.startswith("https://raw.githubusercontent.com/jobright-ai/") and u.endswith("/README.md")
          and "jobright.ai" not in u for _n, u, _s in config.JOBRIGHT_LISTS))
check("max age 10 days", config.JOBRIGHT_MAX_AGE_DAYS == 10)

section("profile path and secrets")
check("the rubric file name matches .gitignore", config.CANDIDATE_PROFILE_PATH.name == "Brice_Candidate_Profile_and_Filters.md")
check("...at the repo root by default", config.CANDIDATE_PROFILE_PATH.parent.resolve() == REPO.resolve()
      or "CANDIDATE_PROFILE_PATH" in os.environ)
gitignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
check(".gitignore lists the rubric and .env.brice",
      "Brice_Candidate_Profile_and_Filters.md" in gitignore and ".env.brice" in gitignore)
saved = os.environ.get("CANDIDATE_PROFILE_PATH")
os.environ["CANDIDATE_PROFILE_PATH"] = str(HERE / "elsewhere" / "rubric.md")
try:
    reloaded = importlib.reload(config)
    check("CANDIDATE_PROFILE_PATH can be overridden from the environment",
          reloaded.CANDIDATE_PROFILE_PATH == HERE / "elsewhere" / "rubric.md")
finally:
    if saved is None:
        os.environ.pop("CANDIDATE_PROFILE_PATH", None)
    else:
        os.environ["CANDIDATE_PROFILE_PATH"] = saved
    importlib.reload(config)
check("secrets come from the environment only (blank in tests)",
      all(getattr(config, k) == "" for k in testkit.SECRET_ENV))
check("the owner's topic is a separate setting from Brice's", hasattr(config, "OWNER_NTFY_TOPIC"))

section("what linkedin.fetch_listings actually sends")
sent = []


def fake_get(url, params=None, headers=None, timeout=None):
    sent.append((url, dict(params or {})))
    return types.SimpleNamespace(status_code=200, text="<ul></ul>", raise_for_status=lambda: None)


with patched(linkedin.requests, get=fake_get):
    jobs, err = linkedin.fetch_listings("sales engineer", "United States", config.LOOKBACK_SECONDS, start=20)
p = sent[0][1] if sent else {}
check("one request to the guest search endpoint", len(sent) == 1 and sent[0][0] == linkedin.SEARCH_URL)
check("f_E=2,3", p.get("f_E") == "2,3", str(p))
check("f_TPR=r86400", p.get("f_TPR") == "r86400", str(p))
check("keywords / location / start passed through",
      p.get("keywords") == "sales engineer" and p.get("location") == "United States" and p.get("start") == "20")
check("an empty page parses to no jobs and no error", jobs == [] and err is None)

section("copied files")


def lf(path):
    return path.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")


mine = lf(HERE / "linkedin.py").splitlines()
theirs = lf(REPO / "scraper_hassan" / "linkedin.py").splitlines()


def body(lines):
    """Everything after the module docstring."""
    src = "\n".join(lines)
    doc_end = src.index('"""', 3) + 3
    return src[doc_end:].splitlines()


changed = [l for l in difflib.unified_diff(body(theirs), body(mine), lineterm="", n=0)
           if l[:1] in "+-" and not l.startswith(("+++", "---"))]
check("linkedin.py = scraper_hassan's except the docstring, the config import and the f_E line",
      sorted(changed) == sorted([
          '-        "f_E": "1",               # Internship only — see the module docstring',
          '+        "f_E": LINKEDIN_EXPERIENCE_FILTER,',
          "+from config import LINKEDIN_EXPERIENCE_FILTER",
          "+",
      ]), "\n".join(changed))
check("salary_extraction.py is byte-identical to scraper_hassan's",
      (HERE / "salary_extraction.py").read_bytes() == (REPO / "scraper_hassan" / "salary_extraction.py").read_bytes())
check("requirements.txt is byte-identical to scraper_hassan's (keeps anthropic<1)",
      (HERE / "requirements.txt").read_bytes() == (REPO / "scraper_hassan" / "requirements.txt").read_bytes()
      and "anthropic>=0.40.0,<1" in (HERE / "requirements.txt").read_text(encoding="utf-8"))

section("Python 3.12 grammar and LF line endings")
py_files = sorted(HERE.glob("*.py"))
bad_parse = []
for f in py_files:
    try:
        ast.parse(f.read_text(encoding="utf-8"), filename=str(f), feature_version=(3, 12))
    except SyntaxError as exc:
        bad_parse.append(f"{f.name}: {exc}")
check(f"all {len(py_files)} scraper_brice/*.py parse as Python 3.12", not bad_parse, "; ".join(bad_parse))
crlf = [f.name for f in sorted(HERE.rglob("*")) if f.is_file() and "__pycache__" not in f.parts
        and b"\r" in f.read_bytes()]
check("no file in scraper_brice/ has a CR (new files are LF)", not crlf, str(crlf))

import sys  # noqa: E402

sys.exit(testkit.finish())
