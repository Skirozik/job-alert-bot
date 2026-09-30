"""title_gate.gate: which new LinkedIn titles may cost a description fetch and a Claude call.

A false DROP is invisible (stored INELIGIBLE "Pre-filtered: <rule>"), a false
PASS costs one classification -- so these fixtures pin both directions: 92
titles that must reach Claude and 70 that must not, each with the rule that
drops it. They are the field study's measured fixtures plus real titles from
the verified company boards, adjusted to the owner's engineer-level floor, and
the false drops and passes the 2026-09-30 reviews found in live results.

Run:  cd scraper_brice && python -X utf8 test_title_gate.py
"""

import testkit

testkit.block_network()

import sys  # noqa: E402

import title_gate  # noqa: E402
from testkit import check, section  # noqa: E402

MUST_PASS = [
    # technical pre-sales
    "Associate Sales Engineer", "Associate Sales Engineer, SE Desk - Southeast", "Sales Engineer",
    "Sales Engineer - New Grad", "Associate Solutions Engineer", "Associate Solutions Engineer, Auth0",
    "Associate Solution Engineer (New Grad)", "Solutions Engineer I", "Solutions Consultant",
    "Associate Solution Consultant", "Pre-Sales Engineer", "Associate Systems Engineer",
    "Associate Systems Engineer - SE Academy", "Network Systems Engineer (Pre-Sales)",
    "Associate Solutions Architect, AGI-Tech, Early Career - 2027", "Solutions Architect, AWSI - 2027",
    "Solutions Architect", "Inside Sales Engineer", "Technical Sales Representative",
    "Associate Technical Account Manager",
    # network, systems, endpoint, support engineering, security -- engineer level
    "Network Engineer I", "Network Engineer, Entry Level", "Junior Network Engineer", "Associate Network Engineer",
    "Junior Network Administrator", "Network Administrator", "Systems Administrator", "Systems Engineer (Junior)",
    "Endpoint Engineer", "Intune Administrator", "Cloud Support Associate", "Cloud Support Engineer I",
    "Technical Support Engineer", "L1 Technical Support Engineer", "Customer Support Engineer (Tier 1)",
    "Support Engineer I", "SOC Analyst I", "SOC Tier 1 Analyst", "Associate SOC Analyst", "Security Analyst I",
    "Cybersecurity Analyst", "Junior Security Engineer", "Associate Security Engineer", "Field Engineer",
    "IT Support Engineer", "Infrastructure Engineer",
    # the gate never judges graduation years; the rubric does
    "Technical Support Engineer - University Graduate 2027", "Technical Support Engineer - University Graduate 2026",
    # a regional or segment 'Mid' is not a level
    "Associate Sales Engineer, SE Desk - Mid West", "Solutions Engineer, Mid-Market",
    # a range that starts at entry level
    "Network Engineer I/II", "Systems Administrator I-II", "Junior/Mid Network Engineer", "Security Analyst I or II",
    # a technical account manager with an entry marker (a live new-grad TAM title)
    "Technical Account Manager Analyst (New Grad)",
    # program and early-career titles from the verified company boards
    "Security Engineer I (Full Time) - United States", "Cloud Engineer Graduate", "Academy Systems Engineer",
    "Early Career Consult Program - Network Support Associate", "AT&T Technology Development Program",
    "Junior Data Security Engineer", "Campus Systems Engineer (Full-Time)",
    "New College Grad - IT Software Support Engineer", "New College Grad - ID1 IT System Administrator",
    "Associate BIT Field Engineer", "Risk and Cybersecurity, FIS University Program",
    "Associate Solutions Engineer (Presales Academy)", "Sales Engineer - New Grad 2026/2027",
    "Entry Level Systems Engineer, C2 Networking, Clearance Eligible",
    # engineer-level network and support titles
    "NOC Engineer I", "NOC Analyst", "Associate Cloud Engineer", "Network Technician/Engineer",
    "Support Engineer - External, AWS Marketplace", "System Vulnerability Analyst - Entry to Mid Level",
    "Jr-Sr. Systems Administrator", "Associate/Senior Solutions Engineer",
    # a strong new-grad marker outranks a level suffix or an architect noun
    "Associate Sales Engineer II (New Grad)", "AI GPU Power Architect - New College Grad",
    # multi-level postings are judged at their lowest level (live titles, 2026-09-30)
    "Technical Architect (Pre-Sales) - All Levels", "Solution Architect/Senior Solution Architect - Data 360",
    "Solution Architect / Senior Solution Architect - PubSec - FedCiv", "TDCJ - Network Specialist I,II,III - Field Support",
    "Technical Support Engineer - Senior Technical Support Engineer", "Data Center L2/L3 Support Engineer (Junior)",
    "Jr. Mid Level Security Engineer – Cloud & Infrastructure Security",
    "Network / Hybrid Engineer (Senior & Junior level) - Active Secret clearance",
    # L2 before a technology is the layer; a product named "... Manager" is not a manager
    "Technical Support Engineer - L2 Switching", "Systems Engineer I - Mission Sensor Manager (Onsite)",
    "Endpoint Manager Engineer I", "Configuration Manager (SCCM) Engineer - Entry Level",
    # a support phrase in parentheses beside an administrator role noun is left to the rubric (I-3, by duties)
    "Network Administrator (Network + Desktop Support)",
]

SENIOR = "seniority/leadership title"
LEVEL = "level II+/2+ title"
SALES = "pure-sales title (AE/SDR/BDR/account manager)"
DESK = "below the engineer floor (help desk/service desk/desktop support)"
TECH = "below the engineer floor (technician)"
TAM = "technical account manager without an entry marker"
ARCH = "architect title other than solutions architect"
INTERN = "internship/co-op title"

MUST_DROP = [
    # seniority and leadership
    ("Senior Sales Engineer", SENIOR), ("Sr. Sales Engineer", SENIOR), ("Sr Network Engineer (TS/SCI)", SENIOR),
    ("Lead Network Engineer", SENIOR), ("Staff Solutions Architect", SENIOR), ("Staff Sales Engineer", SENIOR),
    ("Principal Solutions Engineer", SENIOR), ("Cyber Security Analyst Mid", SENIOR),
    ("SME Network Administrator", SENIOR), ("Security Operations, Senior Manager", SENIOR),
    ("Director, Solutions Engineering", SENIOR), ("Head of Sales Engineering", SENIOR),
    ("Manager, Solutions Engineering", SENIOR), ("SOC Supervisor", SENIOR),
    ("Chief Information Security Officer", SENIOR), ("Mid-Level Network Engineer", SENIOR),
    ("Senior Network Engineer", SENIOR), ("Data Center Specialist Mid (Government)", SENIOR),
    ("Cyber Analyst - Mid (Infrastructure Systems)", SENIOR), ("Network Analyst - Mid to Experienced Level", SENIOR),
    ("AI Builder, Emerging Talent Manager (Manager/Sr. Manager/Director)", SENIOR),
    ("Staff - Advanced Technical Support Engineer - Switching (EX/QFX)", SENIOR),
    ("Leader, Solutions Engineer-US Commercial", SENIOR), ("Learning and Development Program Manager, Quality", SENIOR),
    # a new-grad marker never outranks a seniority word
    ("Senior Sales Engineer - New Grad Program Mentor", SENIOR),
    # SVP/AVP and plural seniority words (live board titles), and a senior twin of a senior role
    ("SVP, Global Solution Engineering - Tableau", SENIOR), ("AVP — Network Automation & Infrastructure AI", SENIOR),
    ("Systems Administrator Seniors- Unity Application Engineer", SENIOR),
    ("Cyber Security Analyst Leads – Cyber Threat Hunting", SENIOR), ("Lead Engineer / Senior Lead Engineer", SENIOR),
    # level II and above
    ("Sales Engineer 2 (Customer Success) - Denver", LEVEL), ("Commercial Sales Engineer 2 (AMER - West)", LEVEL),
    ("SOC Analyst, Tier II", LEVEL), ("Network Engineer III", LEVEL), ("Systems Administrator Level 3", LEVEL),
    ("Security Analyst L2", LEVEL), ("Network Engineer 3", LEVEL), ("Technology Risk Analyst Associate-2", LEVEL),
    ("L2 Network Engineer", LEVEL),                    # a level: "network" is not a layer-2 technology
    # pure sales (technical pre-sales only, while config.DROP_PURE_SALES holds)
    ("Account Executive", SALES), ("Enterprise Account Executive", SALES), ("Associate Account Executive", SALES),
    ("Sales Development Representative", SALES), ("Business Development Representative", SALES),
    ("Account Manager", SALES), ("Account Executive - New Grad", SALES),
    ("Sales Development Representative (New Grad 2027)", SALES),
    ("(New Grad) Account Development Representative II - Phoenix", SALES),
    # engineer-level floor: help desk / service desk / desktop support never pass ...
    ("Help Desk Associate", DESK), ("IT Support Specialist I", DESK), ("Help Desk Technician - New Grad", DESK),
    ("Desktop Support Engineer", DESK), ("Service Desk Analyst", DESK),
    ("IT Support (Operations) Specialist", DESK), ("IT Specialist (Help Desk)", DESK),
    # ... and a technician title passes only if it also names an engineer role
    ("NOC Technician", TECH), ("Network Operations Center Technician", TECH), ("Data Center Technician", TECH),
    ("Datacenter Networking Technician", TECH), ("Data Center Operations Technician", TECH),
    ("Field Technician", TECH), ("IT Technician", TECH), ("Field Service Tech", TECH),
    # other rules
    ("Technical Account Manager", TAM),
    ("Enterprise Architect", ARCH), ("Cloud Architect", ARCH), ("Network Architect", ARCH),
    ("Sales Engineer Intern", INTERN), ("Solutions Engineering Intern (Summer 2027)", INTERN),
    ("Network Engineer Co-op", INTERN),
]

section("fixture lists")
drop_titles = [t for t, _ in MUST_DROP]
check("92 must-pass and 70 must-drop titles", len(MUST_PASS) == 92 and len(MUST_DROP) == 70,
      f"{len(MUST_PASS)} / {len(MUST_DROP)}")
check("no duplicates inside either list",
      len(set(MUST_PASS)) == len(MUST_PASS) and len(set(drop_titles)) == len(drop_titles))
check("no title is in both lists", not set(MUST_PASS) & set(drop_titles))

section("must pass: every one reaches the classifier")
wrongly_dropped = [(t, title_gate.gate(t)) for t in MUST_PASS if title_gate.gate(t) is not None]
check(f"must-pass {len(MUST_PASS) - len(wrongly_dropped)}/{len(MUST_PASS)}", not wrongly_dropped,
      "; ".join(f"{t!r} -> {r}" for t, r in wrongly_dropped))

section("must drop: every one drops, by the named rule")
wrong = [(t, want, title_gate.gate(t)) for t, want in MUST_DROP if title_gate.gate(t) != want]
check(f"must-drop {len(MUST_DROP) - len(wrong)}/{len(MUST_DROP)} with the expected rule name", not wrong,
      "; ".join(f"{t!r}: want {w!r}, got {g!r}" for t, w, g in wrong))
rules_seen = {want for _, want in MUST_DROP}
check("every rule the gate can return is pinned by at least one fixture",
      rules_seen == {SENIOR, LEVEL, SALES, DESK, TECH, TAM, ARCH, INTERN}, str(rules_seen))

section("rule precedence")
check("internship beats everything ('Senior Sales Engineer Intern' is an internship)",
      title_gate.gate("Senior Sales Engineer Intern") == INTERN)
check("'External' is not an externship ('Support Engineer - External, AWS Marketplace' passes)",
      title_gate.gate("Support Engineer - External, AWS Marketplace") is None)
check("the floor beats a new-grad marker ('Help Desk Technician - New Grad' drops)",
      title_gate.gate("Help Desk Technician - New Grad") == DESK)
check("an engineer role rescues a technician title ('Network Technician/Engineer' passes)",
      title_gate.gate("Network Technician/Engineer") is None)
check("'Inside Sales Engineer' is technical, not pure sales", title_gate.gate("Inside Sales Engineer") is None)
check("'Technical Sales Representative' carries a technical marker", title_gate.gate("Technical Sales Representative") is None)

section("DROP_PURE_SALES = False (if the owner opts pure sales in)")
title_gate.DROP_PURE_SALES = False
try:
    check("'Account Executive' passes", title_gate.gate("Account Executive") is None)
    check("'Sales Development Representative' passes", title_gate.gate("Sales Development Representative") is None)
    check("'Help Desk Technician' still drops (the floor is not a sales rule)",
          title_gate.gate("Help Desk Technician") == DESK)
    check("'Senior Account Executive' still drops for seniority",
          title_gate.gate("Senior Account Executive") == SENIOR)
finally:
    title_gate.DROP_PURE_SALES = True
check("restored: 'Account Executive' drops again", title_gate.gate("Account Executive") == SALES)

section("is_entry_marked (queue ordering and the TAM / architect rescues)")
for t, want in [("Associate Sales Engineer", True), ("Junior Network Engineer", True), ("Network Engineer I", True),
                ("SOC Tier 1 Analyst", True), ("Sales Engineer - New Grad", True), ("Early Career Program", True),
                ("Cloud Engineer Graduate", True), ("Network Engineer", False), ("Solutions Architect", False),
                ("Senior Network Engineer", False), ("", False), (None, False)]:
    check(f"is_entry_marked({t!r}) is {want}", title_gate.is_entry_marked(t) is want)

section("degenerate input")
check("gate(None) -> None", title_gate.gate(None) is None)
check("gate('') -> None", title_gate.gate("") is None)
check("whitespace is normalised ('Senior   Network\\tEngineer' drops)",
      title_gate.gate("Senior   Network\tEngineer") == SENIOR)

sys.exit(testkit.finish())
