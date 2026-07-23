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

assert len(ATS_COMPANIES) == 50, f"expected 50 companies, got {len(ATS_COMPANIES)}"
