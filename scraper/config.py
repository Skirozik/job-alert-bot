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
]

LOCATIONS = ["United States", "Atlanta, GA"]

LOOKBACK_SECONDS = 86400  # 24 hours — temporarily widened for manual review

CANDIDATE_PROFILE_PATH = REPO_ROOT / "Candidate_Profile_and_Filters.md"

# Loaded from environment (set in GitHub repo secrets or local .env)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "simeonchere@gmail.com")
