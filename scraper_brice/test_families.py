"""families.py (title -> family, location -> U.S.?) and title_gate.source_gate, the ATS/jobright filter.

The source rows below are real public listings from the 2026-09-29/30 sweeps of
the verified company boards, one per live watchlist title, plus the non-US,
internship and senior copies of the same postings.

Run:  cd scraper_brice && python -X utf8 test_families.py
"""

import testkit

testkit.block_network()

import sys  # noqa: E402

import families as F  # noqa: E402
import title_gate  # noqa: E402
from testkit import check, section  # noqa: E402

section("classify_family: one family per title")
FAMILY_CASES = [
    ("Associate Sales Engineer", "", ("SALES_SOLUTIONS", "sales_solutions_engineer")),
    ("Associate Solutions Architect", "", ("SALES_SOLUTIONS", "solutions_architect")),
    ("Solutions Consultant", "", ("SALES_SOLUTIONS", "solution_consultant")),
    ("Systems Engineer", "Pure Storage", ("SALES_SOLUTIONS", "sales_solutions_engineer")),   # its presales title
    ("Junior Network Engineer", "", ("NETWORK_INFRA", "network")),
    ("NOC Analyst", "", ("NETWORK_INFRA", "network")),
    ("Associate Cloud Engineer", "", ("NETWORK_INFRA", "infrastructure_cloud")),
    ("Data Center Network Engineer", "", ("DATA_CENTER", "data_center_it")),
    ("Associate SOC Analyst", "", ("SECURITY", "security_analyst_ops")),
    ("Associate Security Engineer", "", ("SECURITY", "security_engineer")),
    ("Technical Support Engineer", "", ("SUPPORT_ENG", "support_engineer")),
    ("Cloud Support Engineer", "", ("SUPPORT_ENG", "support_engineer")),
    ("Junior Systems Administrator", "", ("SYSTEMS_IT", "sysadmin")),
    ("IT Systems Engineer", "", ("SYSTEMS_IT", "it_systems_engineer")),
    ("Associate Systems Engineer", "Palo Alto Networks", ("SYSTEMS_IT", "plain_systems_engineer")),
    ("Endpoint Engineer", "", ("ENDPOINT_ITSUP", "endpoint")),
    ("Intune Administrator", "", ("ENDPOINT_ITSUP", "endpoint")),
    ("IT Support Specialist", "", ("ENDPOINT_ITSUP", "it_support_helpdesk")),
    # known exclusions
    ("Test Solutions Engineer", "Micron Technology", (None, None)),     # semiconductor test, not presales
    ("Systems Engineer", "SpaceX", (None, None)),                       # aerospace systems engineering
    ("Physical Security Officer", "", (None, None)),
    ("Securities Analyst", "", (None, None)),
    ("Software Engineer, Networking", "", (None, None)),                # a SWE role that mentions networking
    ("Account Executive", "", (None, None)),
    ("Data Center Electrical Engineer", "", ("EXCLUDED_DC_FACILITIES", "dc_facilities_or_product")),
    ("Forward Deployed Engineer", "", ("ADJACENT_FDE", "forward_deployed")),
]
for title, company, want in FAMILY_CASES:
    got = F.classify_family(title, company)
    check(f"{title!r}{' @ ' + company if company else ''} -> {want[0]}", got == want, str(got))
check("empty title -> (None, None)", F.classify_family("") == (None, None))
check("the level section of the field module is not ported (title_gate owns levels)",
      not any(hasattr(F, n) for n in ("classify_level", "all_levels_flag", "TITLE_YOE", "SENIOR_RE")))

section("is_us: True = a U.S. location, False = only non-U.S., None = unknown (kept)")
US_CASES = [
    # the field study's shapes
    ("2 Locations", "https://x.wd5.myworkdayjobs.com/site/job/Austin-Texas/Network-Engineer_R1", "", True),
    ("IND MHLI A-45 2ND FL", "", "", False),      # an Indian FIS site code, not Florida
    ("Remote", "", "", None),
    ("Remote (Canada)", "", "", False),
    ("London", "", "", False),
    ("Office - Taiwan - Taipei City", "", "", False),
    ("(Remote, GBR)", "", "", False),
    ("US-VA-Vienna", "", "", True),
    ("Remote", "", "Senior Customer Engineer - Nashville", True),    # the title decides when the location cannot
    # U.S. towns named like foreign places (the port's fix; see families.py)
    ("Dublin, OH", "", "", True),
    ("Vienna, Virginia", "", "", True),
    ("Melbourne, FL, US", "", "", True),
    ("Rome, NY", "", "", True),
    ("New London, CT", "", "", True),
    ("Athens, OH, US", "", "", True),
    ("Vienna, WV 26105", "", "", True),
    ("2040 Amsterdam Ave., New York, NY", "", "", True),
    ("Hybrid - San Francisco, New York City, London, Berlin", "", "", True),
    ("Remote in the US, Chicago, Toronto", "", "", True),
    # ... while the foreign places stay foreign
    ("Dublin, Ireland", "", "", False),
    ("Dublin 2, IE", "", "", False),
    ("Vienna, Austria", "", "", False),
    ("Paris, France", "", "", False),
    ("Berlin, DE", "", "", False),
    ("Toronto, ON, CA", "", "", False),
    ("Cornwall, Ontario, CA", "", "", False),
    ("Richmond, BC, Canada (Richmond)", "", "", False),
    ("Washington, England, United Kingdom", "", "", False),
    ("Tijuana, Baja California, Mexico", "", "", False),
    ("Distributed", "", "Senior Named Account Executive, Federal Government (Ottawa)", False),
]
for loc, url, title, want in US_CASES:
    got = F.is_us(loc, url, title)
    check(f"is_us({loc!r}{', title=' + repr(title) if title else ''}) is {want}", got is want, str(got))

section("source_gate on real board rows: kept with the family label")
KEEP_ROWS = [
    ('Palo Alto Networks', 'Associate Systems Engineer',
     'Office - USA - TX',
     'https://paloaltonetworks.wd5.myworkdayjobs.com/panwexternalcareers/job/Office---USA---TX/Associate-Systems-Engineer_JR-011810',
     'SYSTEMS_IT'),
    ('Palo Alto Networks', 'Academy Systems Engineer',
     'Office - USA - TX',
     'https://paloaltonetworks.wd5.myworkdayjobs.com/panwexternalcareers/job/Office---USA---TX/Academy-Systems-Engineer_JR-011449',
     'PROGRAM'),
    ('Samsara', 'Associate Sales Engineer, SE Desk (French Bilingual)',
     'Remote - US',
     'https://www.samsara.com/company/careers/roles/7624120?gh_jid=7624120',
     'SALES_SOLUTIONS'),
    ('Verkada', 'Associate Solutions Engineer, San Mateo',
     'San Mateo, CA United States',
     'https://job-boards.greenhouse.io/verkada/jobs/4135277007',
     'SALES_SOLUTIONS'),
    ('Verkada', 'Technical Support Engineer - University Graduate 2027',
     'San Mateo, CA United States',
     'https://job-boards.greenhouse.io/verkada/jobs/5121488007',
     'SUPPORT_ENG'),
    ('Kyndryl', 'Early Career Consult Program – Network Support Associate',
     'Dallas (USDALFRI) Frisco AI HUB',
     'https://kyndryl.wd5.myworkdayjobs.com/KyndrylEarlyCareers/job/Dallas-USDALFRI-Frisco-AI-HUB/Early-Career-Consult-Program---Network-Support-Associate_R-67487-1',
     'PROGRAM'),
    ('Kyndryl', 'Early Career Consult Program – Cybersecurity Engineer',
     'Dallas (USDALFRI) Frisco AI HUB',
     'https://kyndryl.wd5.myworkdayjobs.com/KyndrylEarlyCareers/job/Dallas-USDALFRI-Frisco-AI-HUB/Early-Career-Consult-Program---Cybersecurity-Engineer_R-67304-1',
     'SECURITY'),
    ('AT&T', 'AT&T Technology Development Program',
     '4 Locations',
     'https://att.wd1.myworkdayjobs.com/ATTCollege/job/Dallas-Texas/AT-T-Technology-Development-Program_R-121905-1',
     'PROGRAM'),
    ('HPE', 'Cloud Engineer Graduate',
     'Aguadilla, Puerto Rico, Puerto Rico',
     'https://hpe.wd5.myworkdayjobs.com/Jobsathpe/job/Aguadilla-Puerto-Rico-Puerto-Rico/Cloud-Engineer-Graduate_1215862-3',
     'NETWORK_INFRA'),
    ('Drata', 'Associate Solutions Architect',
     'Remote - US',
     'https://jobs.ashbyhq.com/drata/b0d00418-9f98-4776-a83d-e0bbe9598025',
     'SALES_SOLUTIONS'),
    ('Cisco', 'Security Engineer I (Full Time) - United States',
     'RTP, North Carolina, US',
     'https://cisco.wd5.myworkdayjobs.com/Cisco_Careers/job/RTP-North-Carolina-US/Security-Engineer-I--Full-Time----United-States_2025883',
     'SECURITY'),
    ('Expel', 'Associate SOC Analyst',
     'Remote',
     'https://expel.com/about/career-listing/8588028002?gh_jid=8588028002',
     'SECURITY'),
    ('Tailscale', 'Customer Support Engineer (Tier 1)',
     'Remote (United States)',
     'https://job-boards.greenhouse.io/tailscale/jobs/4724309005',
     'SUPPORT_ENG'),
    ('OpenGov', 'ERP Solutions Engineer I',
     'US | Texas | Dallas',
     'https://jobs.ashbyhq.com/opengov/1c75bde3-7c63-494e-9bb1-cfaa9fd8d2c3',
     'SALES_SOLUTIONS'),
    ('Pure Storage', 'Associate Security Engineer',
     'Santa Clara, California',
     'https://job-boards.greenhouse.io/purestorage/jobs/8211954',
     'SECURITY'),
    ('Anduril', 'Entry Level Systems Engineer, C2 Networking, Clearance Eligible',
     'Costa Mesa, California, United States',
     'https://boards.greenhouse.io/andurilindustries/jobs/5241149007?gh_jid=5241149007',
     'NETWORK_INFRA'),
    ('FIS', 'Risk and Cybersecurity, FIS University Program',
     '3 Locations',
     'https://fis.wd5.myworkdayjobs.com/SearchJobs/job/US-FL-JAX-347/Risk-and-Cybersecurity--FIS-University-Program_JR0309682',
     'SECURITY'),
    ('Replit', 'Support Engineer I (FC, Weekend Shift)',
     'Foster City, CA',
     'https://jobs.ashbyhq.com/replit/951e0ebb-a957-45fa-8763-d56ba46750b4',
     'SUPPORT_ENG'),
    ('Jump Trading', 'Campus Systems Engineer (Full-Time)',
     'Chicago',
     'https://www.jumptrading.com/hr/job?gh_jid=8008112',
     'SYSTEMS_IT'),
    ('Micron Technology', 'New College Grad - IT Software Support Engineer',
     'Boise, ID - ID1',
     'https://micron.wd1.myworkdayjobs.com/External/job/Boise-ID---ID1/New-College-Grad---IT-Software-Support-Engineer_JR111038',
     'SUPPORT_ENG'),
    ('Micron Technology', 'New College Grad - ID1 IT System Administrator',
     'Boise, ID - ID1',
     'https://micron.wd1.myworkdayjobs.com/External/job/Boise-ID---ID1/New-College-Grad---ID1-IT-System-Administrator_JR108946',
     'SYSTEMS_IT'),
    ('Warner Bros. Discovery', 'Associate BIT Field Engineer',
     'CA Burbank Bldg. 750, Second Century, Tower 2',
     'https://warnerbros.wd5.myworkdayjobs.com/global/job/CA-Burbank-Bldg-750-Second-Century-Tower-2/Associate-BIT-Field-Engineer_R000106113',
     'SYSTEMS_IT'),
    ('Palo Alto Networks', 'Solutions Consultant 1',
     'Tallahassee, Florida',
     'https://paloaltonetworks.wd5.myworkdayjobs.com/panwexternalcareers/job/Tallahassee-Florida/Solutions-Consultant-2_JR-022074',
     'SALES_SOLUTIONS'),
    ('Okta', 'Technical Account Manager Analyst (New Grad)',
     'Chicago, Illinois',
     'https://www.okta.com/company/careers/opportunity/7363101?gh_jid=7363101',
     'PROGRAM'),
]
DROP_ROWS = [
    ('Palo Alto Networks', 'Associate Systems Engineer',
     'Office - Taiwan - Taipei City',
     'https://paloaltonetworks.wd5.myworkdayjobs.com/panwexternalcareers/job/Office---Taiwan---Taipei-City/Associate-Systems-Engineer_JR-011829',
     'non-US location'),
    ('SpaceX', 'Principal Cybersecurity Engineer (Starshield)',
     'Hawthorne, CA',
     'https://boards.greenhouse.io/spacex/jobs/8821415002?gh_jid=8821415002',
     'seniority/leadership title'),
    ('AT&T', 'AT&T Technology Development Program Internship',
     '4 Locations',
     'https://att.wd1.myworkdayjobs.com/ATTCollege/job/Dallas-Texas/AT-T-Technology-Development-Program-Internship_R-122670-1',
     'internship/co-op title'),
    ('Tailscale', 'Customer Support Engineer (Tier 1)',
     'Remote (Canada)',
     'https://job-boards.greenhouse.io/tailscale/jobs/4724307005',
     'non-US location'),
    ('FIS', 'Intern, Risk and Cybersecurity, FIS University Program',
     '3 Locations',
     'https://fis.wd5.myworkdayjobs.com/SearchJobs/job/US-FL-JAX-347/Intern--Risk-and-Cybersecurity--FIS-University-Program_JR0309680',
     'internship/co-op title'),
    ('Samsara', 'Product Support Engineer III',
     'Bengaluru - BLR1',
     'https://www.samsara.com/company/careers/roles/7960367?gh_jid=7960367',
     'non-US location'),
    ('Jump Trading', 'Campus Systems Engineer (Full-Time)',
     'London',
     'https://www.jumptrading.com/hr/job?gh_jid=7215943',
     'non-US location'),
    ('Palo Alto Networks', 'Solutions Consultant 1',
     'Toronto, Canada',
     'https://paloaltonetworks.wd5.myworkdayjobs.com/panwexternalcareers/job/Toronto-Canada/Solutions-Consultant-1_JR-021918',
     'non-US location'),
]
bad = [(t, want, title_gate.source_gate(t, c, loc, url)) for c, t, loc, url, want in KEEP_ROWS
       if title_gate.source_gate(t, c, loc, url) != (True, want)]
check(f"{len(KEEP_ROWS) - len(bad)}/{len(KEEP_ROWS)} watchlist rows kept with the expected label", not bad,
      "; ".join(f"{t!r}: want {w}, got {g}" for t, w, g in bad))
bad = [(t, want, title_gate.source_gate(t, c, loc, url)) for c, t, loc, url, want in DROP_ROWS
       if title_gate.source_gate(t, c, loc, url) != (False, want)]
check(f"{len(DROP_ROWS) - len(bad)}/{len(DROP_ROWS)} non-US / internship / senior copies dropped by the named rule",
      not bad, "; ".join(f"{t!r}: want {w}, got {g}" for t, w, g in bad))

section("source_gate: early-career program pass-through (ATS rows only)")
PROGRAMS = [("AT&T Technology Development Program", "AT&T", "4 Locations"),
            ("Early Career Consult Program – Network Support Associate", "Kyndryl", "Dallas (USDALFRI) Frisco AI HUB"),
            ("Academy Systems Engineer", "Palo Alto Networks", "Office - USA - TX")]
for t, c, loc in PROGRAMS:
    check(f"{t!r} is kept as PROGRAM", title_gate.source_gate(t, c, loc) == (True, "PROGRAM"),
          str(title_gate.source_gate(t, c, loc)))
    check(f"...and dropped off-family when program_passthrough=False (jobright)",
          title_gate.source_gate(t, c, loc, program_passthrough=False) == (False, "off-family"))
for t, c, loc in [("Finance Development Program", "Capital One", "Remote - US"),
                  ("New College Grad - Dry Etch Process Engineer", "Micron Technology", "Boise, ID - ID1"),
                  ("Sales Rotation Program", "Salesforce", "Remote - US")]:
    check(f"{t!r} is not a program in the families -> off-family",
          title_gate.source_gate(t, c, loc) == (False, "off-family"), str(title_gate.source_gate(t, c, loc)))
title_gate.DROP_PURE_SALES = False
try:
    check("with DROP_PURE_SALES=False a sales program passes through",
          title_gate.source_gate("Sales Rotation Program", "Salesforce", "Remote - US") == (True, "PROGRAM"))
finally:
    title_gate.DROP_PURE_SALES = True

section("source_gate: jobright Support list keeps support-ENGINEER titles only")
check("'Technical Support Engineer' kept",
      title_gate.source_gate("Technical Support Engineer", "Tailscale", "Remote (United States)",
                             support_list=True, program_passthrough=False) == (True, "SUPPORT_ENG"))
check("'Customer Service Representative' dropped",
      title_gate.source_gate("Customer Service Representative", "Tailscale", "Remote (United States)",
                             support_list=True, program_passthrough=False)
      == (False, "support list: not a support-engineer title"))
check("'Technical Support Specialist' dropped from the Support list (not an engineer title)",
      title_gate.source_gate("Technical Support Specialist", "Acme", "Remote - US", support_list=True,
                             program_passthrough=False)[0] is False)

section("source_gate: the engineer floor and the family filter")
check("help desk / IT support subfamily is excluded (owner floor)",
      title_gate.source_gate("IT Support Specialist", "Acme", "Remote - US") == (False, "off-family"))
check("a data-center facilities role is not a data-center IT role",
      title_gate.source_gate("Data Center Electrical Engineer", "Acme", "Remote - US") == (False, "off-family"))
check("an adjacent forward-deployed role is not kept",
      title_gate.source_gate("Forward Deployed Engineer", "Acme", "Remote - US") == (False, "off-family"))
check("a family title still goes through gate(): 'Senior Network Engineer' drops for seniority",
      title_gate.source_gate("Senior Network Engineer", "Acme", "Remote - US") == (False, "seniority/leadership title"))
check("a U.S. false-friend town is kept: 'Associate Network Engineer' in Dublin, OH",
      title_gate.source_gate("Associate Network Engineer", "Acme", "Dublin, OH") == (True, "NETWORK_INFRA"))
check("an unknown location is kept (only a definite non-US drops)",
      title_gate.source_gate("Associate Network Engineer", "Acme", "") == (True, "NETWORK_INFRA"))
check("FAMILY_RANK orders the primary families first",
      sorted(title_gate.FAMILY_RANK, key=title_gate.FAMILY_RANK.get)[:3] == ["SALES_SOLUTIONS", "NETWORK_INFRA", "PROGRAM"])
check("every allowed family has a rank", title_gate.ALLOWED_FAMILIES <= set(title_gate.FAMILY_RANK))

sys.exit(testkit.finish())
