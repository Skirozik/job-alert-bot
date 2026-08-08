import os
from pathlib import Path

# Repo root = one level above this file (LinkedIn_Job_Bot/)
REPO_ROOT = Path(__file__).parent.parent

# Hassan's stated targets were "help desk intern", "it support specialist",
# "desktop support", "junior system admin", and "cybersecurity analyst" —
# four of the five being full-time titles.
#
# This list originally rewrote all five into internship phrasings, because the
# search was internship-only (f_E="1"). That scope proved far too narrow: DC
# IT internships run ~3/month, and a 30-day backfill of 404 listings produced
# 3 actionable jobs. Meanwhile 739 entry-level IT roles in the DC metro were
# being scraped and discarded for lacking "intern" in the title.
#
# Now f_E="1,2" (internship + entry level), so his original titles work as
# posted AND the internship phrasings still catch summer programs. Both are
# kept: they surface genuinely different postings, and dedup makes any overlap
# free.
SEARCH_TERMS = [
    # His original five, as actually posted
    "help desk",
    "IT support specialist",
    "desktop support",
    "junior systems administrator",
    "cybersecurity analyst",
    # Same lanes, other common phrasings
    "service desk analyst",
    "IT support technician",
    "technical support specialist",
    "information technology specialist",
    "SOC analyst",
    "information security analyst",
    "network technician",
    "IAM analyst",                     # his Securcorp access-control experience
    # Internship phrasings — still the goal for summers, and these surface
    # postings the full-time terms miss entirely
    "help desk intern",
    "IT support intern",
    "information technology intern",
    "cybersecurity intern",
    "information security intern",
]

# He's in Lorton, VA. "Washington, DC" is metro-wide on LinkedIn, so it
# returns Arlington, Alexandria, Fairfax, Reston, Tysons, McLean, Springfield
# and DC proper.
#
# "United States" is here to catch REMOTE internships, which the rubric treats
# exactly like a DC-onsite role since he's Lorton-based either way. It was
# added after measuring, not on principle:
#
#   2026-08-03, 24h window, f_E=1, internship-titled results only:
#     term                             DC   US
#     IT support intern                 0    3
#     help desk intern                  0    4
#     cybersecurity intern              0    6
#     information technology intern     0   10
#                                      ---  ---
#                                       0   23
#
#   A 30-day backfill of DC alone produced 404 raw listings and just 3
#   actionable jobs. DC-only was not a viable feed in August.
#
# Cost is small because dedup classifies each job exactly once — later runs
# drop it before any description fetch or Claude call. ~23 new internship
# -titled jobs/day at roughly $0.005 each is about $0.12/day. Most will SKIP
# as out-of-area onsite; the genuinely remote ones are the point.
LOCATIONS = ["Washington, DC", "United States"]

# Cron runs every 2 hours (see .github/workflows/scrape_hassan.yml), so 9000s
# (2.5h) would be the minimum that covers the interval plus drift. This is
# deliberately much wider.
#
# Measured 2026-08-03: a 7-day window across the five strongest terms returned
# 50 results containing only 3 unique genuine internships. DC IT internships
# are sparse — roughly one every two days, and August is the low season.
#
# At that volume a 2.5h window gives each posting exactly ONE chance to be
# seen, and there is no watermark or catch-up (bot_state is unused), so a
# failed, skipped, or unlucky run loses that posting permanently. A 24h window
# gives each posting ~12 chances instead.
#
# The cost is near zero: re-seeing a job is free (dedup on id and norm_key
# drops it before any description fetch or Claude call), and ~90% of what
# LinkedIn returns for these terms fails the internship-title pre-filter and
# is dropped for free too.
LOOKBACK_SECONDS = 86400  # 24 hours

CANDIDATE_PROFILE_PATH = REPO_ROOT / "Hassan_Candidate_Profile_and_Filters.md"

# Loaded from environment (GitHub Actions secrets when deployed, or local
# .env.hassan) — a separate secret set from the other two personas, so the
# three pipelines never share credentials or notification channels even
# though the env var *names* are reused.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
