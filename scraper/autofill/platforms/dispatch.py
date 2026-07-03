"""Routes a job's apply_url to the right platform-specific filler.

Mirrors the domain-detection pattern in scraper/external_descriptions.py
(built for fetching descriptions via each platform's read API) — same
domains, same routing logic, different purpose (driving the live
application form instead of reading a JSON API).
"""

from urllib.parse import urlparse


def detect_platform(apply_url: str) -> str:
    if not apply_url:
        return "unknown"
    domain = urlparse(apply_url).netloc.lower()

    if "greenhouse.io" in domain:
        return "greenhouse"
    if "lever.co" in domain:
        return "lever"
    if "ashbyhq.com" in domain:
        return "ashby"
    if "myworkdayjobs.com" in domain:
        return "workday"
    if "icims.com" in domain:
        return "icims"
    return "generic"


def get_filler(platform: str):
    """Returns the fill(page, job, profile) function for a platform, or
    None if that platform isn't supported yet."""
    if platform == "greenhouse":
        from autofill.platforms import greenhouse
        return greenhouse.fill
    return None
