import os
from pathlib import Path

# Repo root = one level above this file (LinkedIn_Job_Bot/)
REPO_ROOT = Path(__file__).parent.parent

SEARCH_TERMS = [
    "software engineer intern",
    "iOS developer intern",
    "AI engineer intern",
    "full stack developer intern",
    "python developer intern",
    "frontend developer intern",
    "software development intern",
]

LOCATIONS = ["United States", "Atlanta, GA"]

# Sized against real outages, not the nominal cron interval.
#
# Was 7200 (2h), chosen when the cron was assumed to fire every 20 min — a
# comfortable 6x overlap. Two things broke that assumption:
#
#   1. GitHub treats "*/20" on a public repo as best-effort and deprioritizes
#      it under load. Observed 2026-08-07: runs landing 50-70 min apart, not
#      20. That alone cuts the overlap to ~2x.
#   2. On 2026-08-06 a GitHub Actions runner-capacity incident left a ~6 hour
#      hole (15:00-21:00Z: no runs at all, plus 4 queued jobs cancelled with
#      no runner ever assigned — steps=0, runner=""). A 2h window cannot
#      cover a 6h gap, and nothing here provides catch-up: bot_state is
#      unused, so anything posted mid-gap was simply never seen.
#
# 6 hours covers the worst outage observed so far. Cost is near zero —
# re-seeing a job is free, since dedup on id/norm_key drops it before any
# description fetch or Claude call.
LOOKBACK_SECONDS = 21600  # 6 hours

CANDIDATE_PROFILE_PATH = REPO_ROOT / "Candidate_Profile_and_Filters.md"

# autofill/ personal data — lives outside the repo (never committed), same
# tier as the resumes folder and tracker.csv it sits alongside.
INTERNSHIP_DIR = REPO_ROOT.parent
APPLICATION_PROFILE_PATH = INTERNSHIP_DIR / "application_profile.yaml"
AUTOFILL_BROWSER_PROFILE_DIR = INTERNSHIP_DIR / "autofill_browser_profile"

# Loaded from environment (set in GitHub repo secrets or local .env)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "simeonchere@gmail.com")
