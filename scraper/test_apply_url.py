"""Quick test: try the externalApply redirect on 5 jobs from the DB."""

import time
import random
from pathlib import Path

import requests
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from db import get_client

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.linkedin.com/jobs/",
}

client = get_client()
jobs = client.table("jobs").select("id, title, company").order("found_at", desc=True).limit(5).execute().data

for job in jobs:
    job_id = job["id"]
    url = f"https://www.linkedin.com/jobs/view/externalApply/{job_id}"
    resp = requests.get(url, headers=HEADERS, allow_redirects=False, timeout=15)
    location = resp.headers.get("Location", "—")
    print(f"\n{job['title']} @ {job['company']}")
    print(f"  status: {resp.status_code}")
    print(f"  location: {location[:100]}")
    time.sleep(random.uniform(1.5, 2.5))
