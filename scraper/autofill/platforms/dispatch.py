"""Routes a job's apply_url to the right platform-specific filler.

Modelled on the domain-detection pattern in scraper/external_descriptions.py
(built for fetching descriptions via each platform's read API) — same shape,
different purpose (driving the live application form instead of reading a
JSON API).

The two lists deliberately DIVERGED as of the IBM work: this module knows
about careers.ibm.com and avature.net, external_descriptions.py does not and
should not. There is no Avature read API to call, and that module's
_fetch_generic already handles careers.ibm.com as a best-effort HTML scrape,
so adding a branch there that returned None would be strictly worse than the
fallback it already has.
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

    # IBM's careers portal runs on Avature. Every IBM posting is served from
    # careers.ibm.com, so that host alone is what routes to the IBM filler.
    if "careers.ibm.com" in domain:
        return "ibm"

    # avature.net is deliberately NOT mapped to "ibm". Avature is a multi-tenant
    # ATS — many employers besides IBM run on *.avature.net — and platforms/ibm.py
    # is built entirely on IBM's numeric field ids (10480 = work authorization,
    # 10530 = worked at IBM before, ...). Handing another company's Avature form
    # to it would type IBM's screening answers into whatever questions happen to
    # sit at those ids. Returning "avature" instead of "generic" is still an
    # improvement: autofill.py logs the platform name, so the user gets
    # "Platform 'avature' isn't supported yet" rather than silence.
    # endswith, not substring — "avature.net.evil.example" is not Avature.
    if domain == "avature.net" or domain.endswith(".avature.net"):
        return "avature"

    return "generic"


def get_filler(platform: str):
    """Returns the fill(page, job, profile) function for a platform, or
    None if that platform isn't supported yet."""
    if platform == "greenhouse":
        from autofill.platforms import greenhouse
        return greenhouse.fill
    if platform == "ibm":
        from autofill.platforms import ibm
        return ibm.fill
    return None
