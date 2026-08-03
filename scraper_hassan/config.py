import os
from pathlib import Path

# Repo root = one level above this file (LinkedIn_Job_Bot/)
REPO_ROOT = Path(__file__).parent.parent

# Hassan's stated targets were "help desk intern", "it support specialist",
# "desktop support", "junior system admin", and "cybersecurity analyst".
# Four of those five are FULL-TIME job titles. This pipeline searches with
# LinkedIn's internship filter (f_E="1", see linkedin.py), under which
# "IT Support Specialist" / "Desktop Support" / "Junior System Admin" /
# "Cybersecurity Analyst" return almost nothing — nobody posts an internship
# under those names. Each target is therefore rewritten below into how the
# internship version is actually posted, with the original intent noted.
SEARCH_TERMS = [
    "help desk intern",                # his "help desk intern"
    "IT support intern",               # his "it support specialist"
    "desktop support intern",          # his "desktop support"
    "technical support intern",
    "information technology intern",   # common umbrella posting
    "information systems intern",      # matches his MIS major
    "systems administrator intern",    # his "junior system admin"
    "network intern",
    "cybersecurity intern",            # his "cybersecurity analyst"
    "information security intern",
    "security operations intern",      # SOC — the real entry-level cyber title
    "IT security intern",
]

# He's in Lorton, VA. LinkedIn's location filter is metro-wide, so this one
# entry returns Arlington, Alexandria, Fairfax, Reston, Tysons, McLean,
# Springfield, and DC proper.
#
# Tunable: adding "United States" would surface nationwide-remote internships
# too, but doubles the request volume AND spends a Haiku call on every
# out-of-area posting the rubric then SKIPs. Left off until there's evidence
# the DC metro alone is too thin.
LOCATIONS = ["Washington, DC"]

# Cron runs every 2 hours (see .github/workflows/scrape_hassan.yml) —
# lookback just needs to comfortably exceed the cron interval plus drift.
LOOKBACK_SECONDS = 9000  # 2.5 hours

CANDIDATE_PROFILE_PATH = REPO_ROOT / "Hassan_Candidate_Profile_and_Filters.md"

# Loaded from environment (GitHub Actions secrets when deployed, or local
# .env.hassan) — a separate secret set from the other two personas, so the
# three pipelines never share credentials or notification channels even
# though the env var *names* are reused.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
