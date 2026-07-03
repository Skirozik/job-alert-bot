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

LOOKBACK_SECONDS = 7200  # 2 hours — wide enough to survive GitHub Actions cron drift

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
