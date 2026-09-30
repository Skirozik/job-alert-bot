"""The 26 company career boards the Brice pipeline sweeps on every run (ats_pass.py).

Same shape as scraper/ats_config.py's ATS_COMPANIES -- {company: {"platform", "token"}} -- so the vendored
ats_sources.fetch_all_listings() takes it as is.

The key is the name stored in jobs.company. It follows LinkedIn's spelling of the employer, so the
company|title norm_key dedups one posting across the ATS, LinkedIn and jobright passes. "Pure Storage" must
keep exactly that spelling: families.classify_family() special-cases it (its presales engineers are titled
"Systems Engineer"), as it does "Anduril" and "Micron Technology".

Tokens: the first 15 are copied from scraper/ats_config.py; the other 11 were verified live on 2026-09-30
(a real request that returned the named company's own postings). A Workday token is "tenant:host:site",
read off the board's URL https://<tenant>.<host>.myworkdayjobs.com/<site>; see
ats_sources.fetch_workday_listings(). Greenhouse and Ashby boards cost one GET per sweep; a Workday board
costs ceil(open postings / 20) POSTs -- the per-board figures below were measured 2026-09-29/30.
"""

ATS_BOARDS = {
    # ── also watched by the main pipeline (scraper/ats_config.py) ─────────────
    "Salesforce": {"platform": "workday", "token": "salesforce:wd12:External_Career_Site"},     # ~77 POSTs
    "NCR Voyix": {"platform": "workday", "token": "ncr:wd1:ext_us"},                            # ~9 POSTs
    "OpenGov": {"platform": "ashby", "token": "opengov"},
    "Okta": {"platform": "greenhouse", "token": "okta"},
    "Pure Storage": {"platform": "greenhouse", "token": "purestorage"},                         # board now titled Everpure
    "CrowdStrike": {"platform": "workday", "token": "crowdstrike:wd5:crowdstrikecareers"},      # ~19 POSTs
    "Anduril": {"platform": "greenhouse", "token": "andurilindustries"},
    "Capital One": {"platform": "workday", "token": "capitalone:wd12:Capital_One"},             # ~93 POSTs
    "FIS": {"platform": "workday", "token": "fis:wd5:SearchJobs"},                              # ~25 POSTs
    "Replit": {"platform": "ashby", "token": "replit"},
    "Jump Trading": {"platform": "greenhouse", "token": "jumptrading"},
    "Micron Technology": {"platform": "workday", "token": "micron:wd1:External"},              # ~153 POSTs, the costliest
    "Warner Bros. Discovery": {"platform": "workday", "token": "warnerbros:wd5:global"},       # ~15 POSTs
    "HD Supply": {"platform": "workday", "token": "hdsupply:wd1:External"},                     # ~18 POSTs
    "Tempus": {"platform": "workday", "token": "tempus:wd5:Tempus_Careers"},                    # ~8 POSTs

    # ── added for this pipeline, verified 2026-09-30 ─────────────────────────
    "Palo Alto Networks": {"platform": "workday", "token": "paloaltonetworks:wd5:panwexternalcareers"},  # ~76 POSTs
    "Verkada": {"platform": "greenhouse", "token": "verkada"},
    "Samsara": {"platform": "greenhouse", "token": "samsara"},
    "Kyndryl": {"platform": "workday", "token": "kyndryl:wd5:KyndrylEarlyCareers"},            # early-careers site, ~3 POSTs
    "AT&T": {"platform": "workday", "token": "att:wd1:ATTCollege"},                             # college site, ~1 POST
    "Hewlett Packard Enterprise": {"platform": "workday", "token": "hpe:wd5:Jobsathpe"},        # ~71 POSTs
    "Drata": {"platform": "ashby", "token": "drata"},
    "Cisco": {"platform": "workday", "token": "cisco:wd5:Cisco_Careers"},                       # ~66 POSTs
    "Verizon": {"platform": "workday", "token": "verizon:wd12:verizon-careers"},                # ~54 POSTs; wd12 (wd5 returns HTTP 422)
    "Expel": {"platform": "greenhouse", "token": "expel"},
    "Tailscale": {"platform": "greenhouse", "token": "tailscale"},
}

# Guard against a silently truncated edit; keep it after the dict.
assert len(ATS_BOARDS) == 26, f"expected 26 boards, got {len(ATS_BOARDS)}"
