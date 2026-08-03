import os
from pathlib import Path

# Repo root = one level above this file (LinkedIn_Job_Bot/)
REPO_ROOT = Path(__file__).parent.parent

SEARCH_TERMS = [
    "patient access representative",
    "patient registration specialist",
    "insurance verification specialist",
    "medical front desk coordinator",
    "medical scheduling coordinator",
    "health unit coordinator",
    "referral coordinator",
    "front desk receptionist",
    "administrative assistant",
    "prior authorization specialist",
    "hotel front desk agent",
]

# Hard metro lock — unlike the original's ["United States", "Atlanta, GA"],
# there's no nationwide fallback: this search only makes sense in Atlanta.
LOCATIONS = ["Atlanta, GA"]

# Cron runs every 2 hours (see modal_app_beyonce.py) — timing isn't a
# priority for this search (postings run ~20 days median time-to-fill), so
# lookback just needs to comfortably exceed the cron interval + drift.
LOOKBACK_SECONDS = 9000  # 2.5 hours

CANDIDATE_PROFILE_PATH = REPO_ROOT / "Beyonce_Candidate_Profile_and_Filters.md"

# Loaded from environment (set via `modal secret create job-alert-secrets-beyonce`
# or local .env.beyonce) — a separate secret/env file from the original
# persona's, so the two pipelines never share credentials or notification
# channels even though some env var *names* are reused.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
