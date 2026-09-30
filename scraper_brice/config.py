"""Configuration for the Brice pipeline (scraper_brice/).

A full-time, entry-level search -- technical pre-sales (sales / solutions engineering), network and
infrastructure, and adjacent IT-engineering families -- anywhere in the United States. Three sources per run:
LinkedIn's guest search, 26 company ATS boards (ats_boards.py) and jobright-ai's new-grad lists (README only).
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# ── LinkedIn ────────────────────────────────────────────────────────────────
# Run order is priority order: if SEARCH_TIME_BUDGET_S ever cuts the search short, the primary families
# have already run. Keyword qualifiers (associate/junior), not f_E, are what steer LinkedIn toward entry
# level (measured on the Hassan fork's logs).
SEARCH_TERMS = [
    # Primary: technical pre-sales
    "associate sales engineer",
    "sales engineer",
    "associate solutions engineer",
    "solutions engineer",
    "associate systems engineer",
    "presales engineer",
    "solutions consultant",
    "associate solutions architect",
    # Primary: network and infrastructure
    "junior network engineer",
    "network engineer",
    "network administrator",
    "NOC engineer",
    "associate network engineer",
    "data center network engineer",
    # Also in scope
    "junior systems administrator",
    "endpoint engineer",
    "cloud support engineer",
    "technical support engineer",
    "SOC analyst",
    "junior cybersecurity analyst",
    "associate security engineer",
    # Lowest priority: the first to drop if runs run long
    "infrastructure engineer",
]
LOCATIONS = ["United States"]
LINKEDIN_EXPERIENCE_FILTER = "2,3"   # f_E: 2 = Entry level, 3 = Associate (the guest endpoint does not enforce it)
LOOKBACK_SECONDS = 86400             # 24 h: covers every gap GitHub's scheduler has left (max measured 14.1 h)
MAX_PAGES_PER_SEARCH = 10            # 100 results; the guest search is not newest-first (scraper/main.py:235-245)
ALL_DUP_PAGES_TO_STOP = 2            # stop a search after this many consecutive all-duplicate pages (not while
                                     # main.LI_LEFTOVER_KEY says an earlier run left LinkedIn jobs unstored)
SEARCH_TIME_BUDGET_S = 25 * 60       # stop starting new searches this long after the LinkedIn pass began
ATS_SWEEP_BUDGET_S = 10 * 60         # the whole board sweep (76-91 s measured 2026-09-30); later requests refused

# ── Per-run work caps ───────────────────────────────────────────────────────
# Jobs that reach Claude, per source, per run. Leftovers are simply not stored, so the next run finds them
# again (LinkedIn: while inside the 24 h window, paging past stored results; ATS: while open; jobright: 7-day
# README window).
MAX_CLASSIFY_PER_RUN = {"ats": 100, "linkedin": 180, "jobright": 100}
RETRY_PENDING_MAX = 40
RUN_TIME_BUDGET_S = 48 * 60          # stop starting new jobs after this; workflow timeout is 60, run-lock 75

# Owner decision pending (see .github/workflows/reminder.yml): technical pre-sales only. False lets AE /
# SDR / BDR / account-manager titles and sales-program titles through to the rubric.
DROP_PURE_SALES = True

# ── jobright-ai new-grad lists: README fields only. Never fetch jobright.ai itself (robots.txt). ────────
JOBRIGHT_LISTS = [
    # (name, raw README URL, the Support list's rule: support-type titles only if support-ENGINEER titles)
    ("Engineering", "https://raw.githubusercontent.com/jobright-ai/2026-Engineering-New-Grad/master/README.md", False),
    ("Sales", "https://raw.githubusercontent.com/jobright-ai/2026-Sales-New-Grad/master/README.md", False),
    ("Software-Engineer", "https://raw.githubusercontent.com/jobright-ai/2026-Software-Engineer-New-Grad/master/README.md", False),
    ("Consultant", "https://raw.githubusercontent.com/jobright-ai/2026-Consultant-New-Grad/master/README.md", False),
    ("Support", "https://raw.githubusercontent.com/jobright-ai/2026-Support-New-Grad/master/README.md", True),
]
JOBRIGHT_MAX_AGE_DAYS = 10

# ── Owner alerts (never Brice's topic) ──────────────────────────────────────
ALERT_THROTTLE_HOURS = 6             # classifier down, all classifications failed, LinkedIn returned nothing
CANARY_THROTTLE_HOURS = 24           # a jobright list or every ATS board returned nothing usable

# Overridable so local runs can read the rubric from outside this public repo.
CANDIDATE_PROFILE_PATH = Path(os.environ.get(
    "CANDIDATE_PROFILE_PATH", REPO_ROOT / "Brice_Candidate_Profile_and_Filters.md"))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")               # Brice's topic (secret NTFY_TOPIC_BRICE): job pings
OWNER_NTFY_TOPIC = os.environ.get("OWNER_NTFY_TOPIC", "")   # owner's topic (secret NTFY_TOPIC): infrastructure alerts
