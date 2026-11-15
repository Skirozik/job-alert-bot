"""Company -> ATS platform/board-token mapping for the direct-ATS fast-path.

Every entry below was verified live (real HTTP request, real job data
returned) before being added here — see the project's ATS verification
research. Platform must be one of the 4 handled by ats_sources.py:
"greenhouse", "lever", "ashby", "smartrecruiters".

Companies were picked for a mix of stack fit (iOS/mobile, full-stack/web,
Python/backend, AI-application — the candidate's strongest lanes) and broad
general-SWE coverage across gaming, aerospace/automotive, enterprise SaaS,
healthtech, cybersecurity, and marketplace/delivery, per the goal of not
narrowly restricting to one lane.

Workday-hosted companies (Unity, Zoom, Etsy, GoodRx, Tempus, CrowdStrike —
all independently confirmed live via their CXS API during research) were
deliberately left out of this first pass: Workday's listing endpoint is a
different POST-based API not yet implemented in ats_sources.py. Add them
here once Workday support is built.
"""

ATS_COMPANIES = {
    # Fintech / payments
    "Stripe": {"platform": "greenhouse", "token": "stripe"},
    "Plaid": {"platform": "ashby", "token": "plaid"},
    "Coinbase": {"platform": "greenhouse", "token": "coinbase"},
    "Affirm": {"platform": "greenhouse", "token": "affirm"},
    "Ramp": {"platform": "ashby", "token": "ramp"},
    "Chime": {"platform": "greenhouse", "token": "chime"},
    "Brex": {"platform": "greenhouse", "token": "brex"},
    "SoFi": {"platform": "greenhouse", "token": "sofi"},

    # Consumer / mobile
    "Duolingo": {"platform": "greenhouse", "token": "duolingo"},
    "Robinhood": {"platform": "greenhouse", "token": "robinhood"},
    "Airbnb": {"platform": "greenhouse", "token": "airbnb"},
    "Pinterest": {"platform": "greenhouse", "token": "pinterest"},
    "Discord": {"platform": "greenhouse", "token": "discord"},
    "Reddit": {"platform": "greenhouse", "token": "reddit"},

    # Productivity / collaboration
    "Notion": {"platform": "ashby", "token": "notion"},
    "Figma": {"platform": "greenhouse", "token": "figma"},
    "Airtable": {"platform": "greenhouse", "token": "airtable"},
    "Asana": {"platform": "greenhouse", "token": "asana"},
    "Linear": {"platform": "ashby", "token": "linear"},

    # AI
    "Anthropic": {"platform": "greenhouse", "token": "anthropic"},
    "OpenAI": {"platform": "ashby", "token": "openai"},
    "Perplexity": {"platform": "ashby", "token": "perplexity"},
    "Scale AI": {"platform": "greenhouse", "token": "scaleai"},
    "Replit": {"platform": "ashby", "token": "replit"},
    "ElevenLabs": {"platform": "ashby", "token": "elevenlabs"},

    # Dev tools / infra
    "Vercel": {"platform": "greenhouse", "token": "vercel"},
    "Supabase": {"platform": "ashby", "token": "supabase"},
    "GitLab": {"platform": "greenhouse", "token": "gitlab"},
    "Datadog": {"platform": "greenhouse", "token": "datadog"},
    "MongoDB": {"platform": "greenhouse", "token": "mongodb"},
    "Cloudflare": {"platform": "greenhouse", "token": "cloudflare"},

    # Data / design
    "Databricks": {"platform": "greenhouse", "token": "databricks"},
    "Canva": {"platform": "smartrecruiters", "token": "canva"},

    # Marketplace / delivery
    "DoorDash": {"platform": "greenhouse", "token": "doordashusa"},
    "Instacart": {"platform": "greenhouse", "token": "instacart"},
    "Lyft": {"platform": "greenhouse", "token": "lyft"},
    "Toast": {"platform": "greenhouse", "token": "toast"},

    # Gaming
    "Riot Games": {"platform": "greenhouse", "token": "riotgames"},
    "Epic Games": {"platform": "greenhouse", "token": "epicgames"},
    "Roblox": {"platform": "greenhouse", "token": "roblox"},

    # Aerospace / automotive / robotics
    "SpaceX": {"platform": "greenhouse", "token": "spacex"},
    "Anduril": {"platform": "greenhouse", "token": "andurilindustries"},
    "Waymo": {"platform": "greenhouse", "token": "waymo"},

    # Enterprise SaaS
    "Okta": {"platform": "greenhouse", "token": "okta"},
    "Twilio": {"platform": "greenhouse", "token": "twilio"},
    "ServiceNow": {"platform": "smartrecruiters", "token": "ServiceNow"},

    # Healthtech
    "Oscar Health": {"platform": "greenhouse", "token": "oscar"},
    "Hims & Hers": {"platform": "ashby", "token": "hims-and-hers"},

    # Cybersecurity
    "SentinelOne": {"platform": "greenhouse", "token": "sentinellabs"},
    "Wiz": {"platform": "greenhouse", "token": "wizinc"},
}

# (count assertion moved to the end of the file — it previously sat here, above
#  later-appended entries, so it silently validated a stale number.)


# ─────────────────────────────────────────────────────────────────────────
# Second wave, added 2026-08-13. Every entry below was proposed by a sector
# sweep and then INDEPENDENTLY re-verified by a different agent making its own
# HTTP call — the re-verification specifically checked that the token reaches
# the right COMPANY, because a wrong token frequently returns HTTP 200 with a
# different employer's jobs, which would silently poison this file.
#
# Workday tokens are 'tenant:host:site' (e.g. 'tencent:wd1:Tencent_Careers').
# See fetch_workday_listings' docstring in ats_sources.py.
# Counts are postings/intern-titled at time of verification.
# ─────────────────────────────────────────────────────────────────────────

# --- ashby ---
ATS_COMPANIES["Abridge"] = {"platform": "ashby", "token": "abridge"}  # 45 postings, 1 intern
ATS_COMPANIES["ClickHouse"] = {"platform": "ashby", "token": "clickhouse"}  # 175 postings, 0 intern
ATS_COMPANIES["FullStory"] = {"platform": "ashby", "token": "fullstory"}  # 1 postings, 0 intern
ATS_COMPANIES["OpenGov"] = {"platform": "ashby", "token": "opengov"}  # 45 postings, 0 intern
ATS_COMPANIES["Snowflake"] = {"platform": "ashby", "token": "snowflake"}  # 389 postings, 8 intern

# --- greenhouse ---
ATS_COMPANIES["Akuna Capital"] = {"platform": "greenhouse", "token": "akunacapital"}  # 34 postings, 8 intern
ATS_COMPANIES["Astera Labs"] = {"platform": "greenhouse", "token": "asteralabs"}  # 169 postings, 3 intern
ATS_COMPANIES["AvidXchange"] = {"platform": "greenhouse", "token": "avidxchangeinc"}  # 16 postings, 0 intern
ATS_COMPANIES["Bandwidth"] = {"platform": "greenhouse", "token": "bandwidth"}  # 35 postings, 0 intern
ATS_COMPANIES["Block"] = {"platform": "greenhouse", "token": "block"}  # 193 postings, 2 intern
ATS_COMPANIES["Calendly"] = {"platform": "greenhouse", "token": "calendly"}  # 12 postings, 0 intern
ATS_COMPANIES["Cribl"] = {"platform": "greenhouse", "token": "cribl"}  # 66 postings, 2 intern
ATS_COMPANIES["Faire"] = {"platform": "greenhouse", "token": "faire"}  # 67 postings, 1 intern
ATS_COMPANIES["FanDuel"] = {"platform": "greenhouse", "token": "fanduel"}  # 96 postings, 2 intern
ATS_COMPANIES["Fleetio"] = {"platform": "greenhouse", "token": "fleetio"}  # 17 postings, 0 intern
ATS_COMPANIES["Flexport"] = {"platform": "greenhouse", "token": "flexport"}  # 158 postings, 0 intern
ATS_COMPANIES["Gusto"] = {"platform": "greenhouse", "token": "gusto"}  # 88 postings, 0 intern
ATS_COMPANIES["Insomniac Games"] = {"platform": "greenhouse", "token": "insomniac"}  # 3 postings, 0 intern
ATS_COMPANIES["Jump Trading"] = {"platform": "greenhouse", "token": "jumptrading"}  # 106 postings, 29 intern
ATS_COMPANIES["Kairos Power"] = {"platform": "greenhouse", "token": "kairospower"}  # 22 postings, 5 intern
ATS_COMPANIES["Nava PBC"] = {"platform": "greenhouse", "token": "navapbc"}  # 16 postings, 0 intern
ATS_COMPANIES["Nintendo of America"] = {"platform": "greenhouse", "token": "nintendo"}  # 48 postings, 0 intern
ATS_COMPANIES["Nuro"] = {"platform": "greenhouse", "token": "nuro"}  # 108 postings, 1 intern
# Old Mission left Greenhouse (the oldmissioncapital board has answered 404 since at
# least 2026-11-14); its careers page now embeds Ashby (42 postings, 2 intern, on 2026-11-14).
ATS_COMPANIES["Old Mission"] = {"platform": "ashby", "token": "old-mission-capital"}
ATS_COMPANIES["PlayOn Sports"] = {"platform": "greenhouse", "token": "playonsports"}  # 19 postings, 0 intern
ATS_COMPANIES["Pure Storage"] = {"platform": "greenhouse", "token": "purestorage"}  # 292 postings, 1 intern
ATS_COMPANIES["Rocket Lab"] = {"platform": "greenhouse", "token": "rocketlab"}  # 405 postings, 33 intern
ATS_COMPANIES["Rockstar Games"] = {"platform": "greenhouse", "token": "rockstargames"}  # 71 postings, 0 intern
ATS_COMPANIES["Tripadvisor"] = {"platform": "greenhouse", "token": "tripadvisor"}  # 94 postings, 2 intern
ATS_COMPANIES["Truveta"] = {"platform": "greenhouse", "token": "truveta"}  # 40 postings, 2 intern

# --- lever ---
ATS_COMPANIES["Gopuff"] = {"platform": "lever", "token": "gopuff"}  # 812 postings, 0 intern
ATS_COMPANIES["Included Health"] = {"platform": "lever", "token": "includedhealth"}  # 146 postings, 0 intern

# --- workday ---
ATS_COMPANIES["Adobe"] = {"platform": "workday", "token": "adobe:wd5:external_experienced"}  # 782 postings, 1 intern
ATS_COMPANIES["Broadridge"] = {"platform": "workday", "token": "broadridge:wd5:Careers"}  # 270 postings, 0 intern
ATS_COMPANIES["Capital One"] = {"platform": "workday", "token": "capitalone:wd12:Capital_One"}  # 1841 postings, 7 intern
ATS_COMPANIES["FIS"] = {"platform": "workday", "token": "fis:wd5:SearchJobs"}  # 379 postings, 1 intern
ATS_COMPANIES["HD Supply"] = {"platform": "workday", "token": "hdsupply:wd1:External"}  # 452 postings, 0 intern
ATS_COMPANIES["Invesco"] = {"platform": "workday", "token": "invesco:wd1:IVZ"}  # 238 postings, 11 intern
ATS_COMPANIES["LexisNexis Risk Solutions"] = {"platform": "workday", "token": "relx:wd3:RiskSolutions"}  # 169 postings, 1 intern
ATS_COMPANIES["Medtronic"] = {"platform": "workday", "token": "medtronic:wd1:MedtronicCareers"}  # 1079 postings, 35 intern
ATS_COMPANIES["Micron Technology"] = {"platform": "workday", "token": "micron:wd1:External"}  # 2724 postings, 51 intern
ATS_COMPANIES["NCR Voyix"] = {"platform": "workday", "token": "ncr:wd1:ext_us"}  # 113 postings, 0 intern
ATS_COMPANIES["Salesforce"] = {"platform": "workday", "token": "salesforce:wd12:External_Career_Site"}  # 1515 postings, 15 intern
ATS_COMPANIES["Tencent"] = {"platform": "workday", "token": "tencent:wd1:Tencent_Careers"}  # 305 postings, 86 intern
ATS_COMPANIES["Visa"] = {"platform": "workday", "token": "visa:wd5:Visa"}  # 771 postings, 0 intern
ATS_COMPANIES["Warner Bros. Discovery"] = {"platform": "workday", "token": "warnerbros:wd5:global"}  # 428 postings, 39 intern
ATS_COMPANIES["Zillow"] = {"platform": "workday", "token": "zillow:wd5:Zillow_Group_External"}  # 116 postings, 0 intern
ATS_COMPANIES["eBay"] = {"platform": "workday", "token": "ebay:wd5:apply"}  # 457 postings, 3 intern

# The six Workday boards used to build and validate fetch_workday_listings.
# Note Unity's tenant is 'unitytech', not 'unity', and the site segment is
# case-sensitive.
ATS_COMPANIES["Unity"] = {"platform": "workday", "token": "unitytech:wd1:Unity"}
ATS_COMPANIES["Zoom"] = {"platform": "workday", "token": "zoom:wd5:Zoom"}
ATS_COMPANIES["Etsy"] = {"platform": "workday", "token": "etsy:wd5:Etsy_Careers"}
ATS_COMPANIES["GoodRx"] = {"platform": "workday", "token": "goodrx:wd1:Careers"}
ATS_COMPANIES["Tempus"] = {"platform": "workday", "token": "tempus:wd5:Tempus_Careers"}
ATS_COMPANIES["CrowdStrike"] = {"platform": "workday", "token": "crowdstrike:wd5:crowdstrikecareers"}

# --- added 2026-11-05: fast-poll expansion, verified live against ats_sources.py's own
# fetchers before being added (same bar as the rest of this file). Two buckets:
#  - named here from the jobs table's own history: the company had already produced an
#    intern-titled posting through LinkedIn or a GitHub tracker, and that posting's own
#    apply_url gave the real token directly (comment notes how many such rows exist).
#  - found by guessing the common lowercase-company-name token against each platform's
#    public API and confirming it returns real postings for the right company (comment
#    is the live job count at verification time, not a historical intern count — these
#    hadn't been seen posting an internship through this bot before).
# --- greenhouse ---
ATS_COMPANIES["Adyen"] = {"platform": "greenhouse", "token": "adyen"}  # 246 open postings at verification
ATS_COMPANIES["Baidu"] = {"platform": "greenhouse", "token": "baidu"}  # 28 open postings at verification
ATS_COMPANIES["Box"] = {"platform": "greenhouse", "token": "boxinc"}  # 1 intern-titled rows already seen
ATS_COMPANIES["Carvana"] = {"platform": "greenhouse", "token": "carvana"}  # 1688 open postings at verification
ATS_COMPANIES["Coursera"] = {"platform": "greenhouse", "token": "coursera"}  # 33 open postings at verification
ATS_COMPANIES["Dropbox"] = {"platform": "greenhouse", "token": "dropbox"}  # 31 open postings at verification
ATS_COMPANIES["Elastic"] = {"platform": "greenhouse", "token": "elastic"}  # 424 open postings at verification
ATS_COMPANIES["Flatiron Health"] = {"platform": "greenhouse", "token": "flatironhealth"}  # 25 open postings at verification
ATS_COMPANIES["Glean"] = {"platform": "greenhouse", "token": "gleanwork"}  # 1 intern-titled rows already seen
ATS_COMPANIES["GoDaddy"] = {"platform": "greenhouse", "token": "godaddy"}  # 28 open postings at verification
ATS_COMPANIES["IMC Trading"] = {"platform": "greenhouse", "token": "imc"}  # 158 open postings at verification
ATS_COMPANIES["Jane Street"] = {"platform": "greenhouse", "token": "janestreet"}  # 219 open postings at verification
ATS_COMPANIES["Mercury"] = {"platform": "greenhouse", "token": "mercury"}  # 76 open postings at verification
ATS_COMPANIES["Monzo"] = {"platform": "greenhouse", "token": "monzo"}  # 57 open postings at verification
ATS_COMPANIES["Peloton"] = {"platform": "greenhouse", "token": "peloton"}  # 47 open postings at verification
ATS_COMPANIES["Point72"] = {"platform": "greenhouse", "token": "point72"}  # 4 intern-titled rows already seen
ATS_COMPANIES["Samsara"] = {"platform": "greenhouse", "token": "samsara"}  # 312 open postings at verification
ATS_COMPANIES["Squarespace"] = {"platform": "greenhouse", "token": "squarespace"}  # 23 open postings at verification
ATS_COMPANIES["Upstart"] = {"platform": "greenhouse", "token": "upstart"}  # 87 open postings at verification
ATS_COMPANIES["Virtu"] = {"platform": "greenhouse", "token": "virtu"}  # 48 open postings at verification
ATS_COMPANIES["Zscaler"] = {"platform": "greenhouse", "token": "zscaler"}  # 371 open postings at verification
ATS_COMPANIES["xAI"] = {"platform": "greenhouse", "token": "xai"}  # 3 intern-titled rows already seen
# --- lever ---
ATS_COMPANIES["Palantir"] = {"platform": "lever", "token": "palantir"}  # 2 intern-titled rows already seen
ATS_COMPANIES["Spotify"] = {"platform": "lever", "token": "spotify"}  # 86 open postings at verification
ATS_COMPANIES["Veeva"] = {"platform": "lever", "token": "veeva"}  # 873 open postings at verification
# --- ashby ---
ATS_COMPANIES["Applied Intuition"] = {"platform": "ashby", "token": "applied"}  # 326 open postings at verification
ATS_COMPANIES["Cognition"] = {"platform": "ashby", "token": "cognition"}  # 107 open postings at verification
ATS_COMPANIES["Cohere"] = {"platform": "ashby", "token": "cohere"}  # 118 open postings at verification
ATS_COMPANIES["Cursor (Anysphere)"] = {"platform": "ashby", "token": "cursor"}  # 120 open postings at verification
ATS_COMPANIES["Rivian"] = {"platform": "ashby", "token": "rivianvw.tech"}  # 12 intern-titled rows already seen
ATS_COMPANIES["Strava"] = {"platform": "ashby", "token": "strava"}  # 27 open postings at verification
# --- smartrecruiters ---
ATS_COMPANIES["Grab"] = {"platform": "smartrecruiters", "token": "grab"}  # 100 open postings at verification
ATS_COMPANIES["LinkedIn"] = {"platform": "smartrecruiters", "token": "linkedin3"}  # 2 intern-titled rows already seen
ATS_COMPANIES["Wise"] = {"platform": "smartrecruiters", "token": "wise"}  # 3 intern-titled rows already seen
# --- workday ---
ATS_COMPANIES["Analog Devices"] = {"platform": "workday", "token": "analogdevices:wd1:external"}  # 1 intern-titled rows already seen
ATS_COMPANIES["Autodesk"] = {"platform": "workday", "token": "autodesk:wd1:Ext"}  # 10 intern-titled rows already seen
ATS_COMPANIES["BlackRock"] = {"platform": "workday", "token": "blackrock:wd1:BlackRock_Professional"}  # 15 intern-titled rows already seen
ATS_COMPANIES["Boeing"] = {"platform": "workday", "token": "boeing:wd1:EXTERNAL_CAREERS"}  # 4 intern-titled rows already seen
ATS_COMPANIES["Cadence"] = {"platform": "workday", "token": "cadence:wd1:external_careers"}  # 2 intern-titled rows already seen
ATS_COMPANIES["Cisco"] = {"platform": "workday", "token": "cisco:wd5:cisco_careers"}  # 1 intern-titled rows already seen
ATS_COMPANIES["Citi"] = {"platform": "workday", "token": "citi:wd5:2"}  # 5 intern-titled rows already seen
ATS_COMPANIES["Expedia Group"] = {"platform": "workday", "token": "expedia:wd108:private"}  # 7 intern-titled rows already seen
ATS_COMPANIES["F5"] = {"platform": "workday", "token": "ffive:wd5:f5jobs"}  # 4 intern-titled rows already seen
ATS_COMPANIES["Hewlett Packard Enterprise"] = {"platform": "workday", "token": "hpe:wd5:jobsathpe"}  # 5 intern-titled rows already seen
ATS_COMPANIES["Home Depot"] = {"platform": "workday", "token": "homedepot:wd5:careerdepot"}  # 2 intern-titled rows already seen
ATS_COMPANIES["Intel"] = {"platform": "workday", "token": "intel:wd1:external"}  # 18 intern-titled rows already seen
ATS_COMPANIES["Marvell"] = {"platform": "workday", "token": "marvell:wd1:MarvellCareers"}  # 46 intern-titled rows already seen
ATS_COMPANIES["Mastercard"] = {"platform": "workday", "token": "mastercard:wd1:CorporateCareers"}  # 4 intern-titled rows already seen
ATS_COMPANIES["Motorola Solutions"] = {"platform": "workday", "token": "motorolasolutions:wd5:Careers"}  # 840 open postings at verification
ATS_COMPANIES["Northrop Grumman"] = {"platform": "workday", "token": "ngc:wd1:Northrop_Grumman_External_Site"}  # 51 intern-titled rows already seen
ATS_COMPANIES["Nvidia"] = {"platform": "workday", "token": "nvidia:wd5:NVIDIAExternalCareerSite"}  # 2000 open postings at verification
ATS_COMPANIES["Palo Alto Networks"] = {"platform": "workday", "token": "paloaltonetworks:wd5:hidden-private-pawn-temp-gateway-147258369"}  # 1 intern-titled rows already seen
ATS_COMPANIES["RTX"] = {"platform": "workday", "token": "globalhr:wd5:rec_rtx_ext_gateway"}  # 55 intern-titled rows already seen
ATS_COMPANIES["Sony"] = {"platform": "workday", "token": "sonyglobal:wd1:SonyGlobalCareers"}  # 2 intern-titled rows already seen
ATS_COMPANIES["Wells Fargo"] = {"platform": "workday", "token": "wf:wd1:wellsfargojobs"}  # 2 intern-titled rows already seen
ATS_COMPANIES["Workday"] = {"platform": "workday", "token": "workday:wd5:Workday_Early_Career"}  # 5 intern-titled rows already seen

# Taken back out of this batch on 2026-11-14, before it ever ran, because the
# boards were dead by then: Target (Workday answers with a non-JSON page),
# Netflix (Workday board empty), Shield AI (Lever board empty), ByteDance and
# Wayfair (SmartRecruiters boards holding one or two postings from 2017-18).
#
# Adding a board is safe at any size. Its existing postings are stored on the
# first pass (Greenhouse, Lever, Ashby, SmartRecruiters answer in one request)
# or the next daily full sweep (Workday, ats_watch.FULL_SWEEP_HOUR_UTC). The
# title pre-filter stores the full-time ones in batches, and only internship
# titles the bot has never seen go to the classifier.

# Guard against a silently-truncated edit. Must stay at the END of the file:
# the previous assertion sat above later additions and validated a stale count.
assert len(ATS_COMPANIES) == 160, f"expected 160 companies, got {len(ATS_COMPANIES)}"
