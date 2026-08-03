"""Classifier unit check: run hand-written fixture postings through the
live pre-filter + classifier chain and assert the expected tier.

Synthetic fixtures covering every branch of the rubric in
Hassan_Candidate_Profile_and_Filters.md — there's no stored DB to sample
from for this persona. This never calls insert_job()/push_job(), so there
is zero chance of a real DB write or notification.

Run from the scraper_hassan directory:
    cd scraper_hassan && python test_classifications.py
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env.hassan")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from main import _is_senior_role, _is_new_grad_role, _is_non_internship_title
from classifier import classify

logging.basicConfig(
    level=logging.WARNING,  # quiet the classifier's own INFO logs during the test
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


# Each fixture: (label, job dict, expected tier).
FIXTURES = [
    (
        "APPLY — Help Desk Intern, near-exact match to his ICOM internship",
        {
            "id": "t1", "title": "Help Desk Intern - Summer 2027",
            "company": "Leidos", "location": "Reston, VA",
            "description": (
                "Leidos is seeking a Help Desk Intern for Summer 2027 at our Reston "
                "campus. You will resolve user tickets in ServiceNow, troubleshoot "
                "hardware and software issues, image and deploy laptops, and escalate "
                "complex issues to senior technicians. CompTIA A+ preferred. Open to "
                "undergraduate students. US citizenship required."
            ),
        },
        "APPLY",
    ),
    (
        "APPLY — Cybersecurity Intern, Security+ listed as 'a plus'",
        {
            "id": "t2", "title": "Cybersecurity Intern (Fall 2026)",
            "company": "Booz Allen Hamilton", "location": "Arlington, VA",
            "description": (
                "Fall 2026 Cybersecurity Intern. Support the security operations team "
                "with alert triage, incident documentation, and vulnerability tracking. "
                "Learn SIEM tooling and security best practices. CompTIA Security+ is a "
                "plus but not required. Ability to obtain a Public Trust is required. "
                "Open to all undergraduate students."
            ),
        },
        "APPLY",
    ),
    (
        "APPLY — IAM Intern, maps to his Securcorp access-control experience",
        {
            "id": "t3", "title": "Identity and Access Management Intern",
            "company": "Capital One", "location": "McLean, VA",
            "description": (
                "IAM Intern, Summer 2027. Assist with user account provisioning and "
                "deprovisioning, access reviews, entitlement documentation, and "
                "supporting the identity governance team. Strong attention to detail "
                "and documentation skills required. No prior IAM experience needed. "
                "12-week paid internship."
            ),
        },
        "APPLY",
    ),
    (
        "SKIP — requires an ALREADY-HELD active TS/SCI clearance",
        {
            "id": "t4", "title": "Cyber Operations Intern",
            "company": "Northrop Grumman", "location": "Chantilly, VA",
            "description": (
                "Cyber Operations Intern supporting a classified program. Candidates "
                "MUST currently hold an active TS/SCI clearance with CI polygraph at "
                "time of application. No exceptions; this program cannot sponsor. "
                "Summer 2027, 12 weeks, paid."
            ),
        },
        "SKIP",
    ),
    (
        "APPLY — clearance SPONSORED, not already held (the exception to the rule)",
        {
            "id": "t5", "title": "IT Support Intern",
            "company": "MITRE", "location": "McLean, VA",
            "description": (
                "IT Support Intern, Summer 2027. Provide desktop support to staff, "
                "manage tickets, image and configure workstations, and assist with "
                "account setup. Must be a US citizen and able to obtain a Public Trust "
                "clearance — MITRE will sponsor. CompTIA A+ preferred."
            ),
        },
        "APPLY",
    ),
    (
        "SKIP — software engineering role, wrong track entirely",
        {
            "id": "t6", "title": "Software Engineer Intern - Summer 2027",
            "company": "Amazon Web Services", "location": "Arlington, VA",
            "description": (
                "Software Engineer Intern. Design, build, test, and ship features for "
                "distributed backend services. Required: proficiency in Java, Python, "
                "or C++, data structures and algorithms coursework, and experience "
                "building software projects. You will own a project end to end and "
                "submit production code."
            ),
        },
        "SKIP",
    ),
    (
        "SKIP — physical security guard role, the direction he's leaving",
        {
            "id": "t7", "title": "Security Officer Intern",
            "company": "Allied Universal", "location": "Springfield, VA",
            "description": (
                "Security Officer Intern. Patrol assigned premises, monitor "
                "surveillance systems, enforce access control procedures, verify "
                "identification at entry points, and maintain daily activity logs and "
                "incident reports. No experience necessary; training provided."
            ),
        },
        "SKIP",
    ),
    (
        "SKIP — 'Current Interns Only' restriction, lives in the title",
        {
            "id": "t8", "title": "Current Interns Only - Technology Analyst Program Summer 2027",
            "company": "Deloitte", "location": "Arlington, VA",
            "description": (
                "This posting is for returning interns in the Technology Analyst "
                "Program. Support infrastructure and IT operations projects, assist "
                "with service desk escalations, and document technical procedures."
            ),
        },
        "SKIP",
    ),
    (
        "SKIP — requires an already-held CCNA plus years of experience",
        {
            "id": "t9", "title": "Network Operations Intern",
            "company": "Verizon", "location": "Ashburn, VA",
            "description": (
                "Network Operations Intern. Requirements: active CCNA certification "
                "required at time of hire, plus 3+ years of enterprise network "
                "operations experience. Monitor network health, respond to outages, "
                "and configure routers and switches."
            ),
        },
        "SKIP",
    ),
    (
        "SKIP — onsite outside the DC metro, no remote option",
        {
            "id": "t10", "title": "Desktop Support Intern",
            "company": "Cleveland Clinic", "location": "Cleveland, OH",
            "description": (
                "Desktop Support Intern. Provide onsite hardware and software support "
                "to clinical staff, image workstations, and manage the ticket queue. "
                "This role is 100% onsite in Cleveland, Ohio. No remote option."
            ),
        },
        "SKIP",
    ),
    (
        # Originally written expecting MAYBE. The classifier returned APPLY and
        # was right: the scripting rule exists to stop a SKIP, not to force a
        # downgrade, and the rubric's tie-break sends a clean Target-role match
        # in the DC metro with no hard disqualifier to APPLY. Expectation
        # corrected rather than the rubric.
        "APPLY — scripting mentioned but not central, otherwise a clean match",
        {
            "id": "t11", "title": "IT Operations Intern",
            "company": "Fannie Mae", "location": "Reston, VA",
            "description": (
                "IT Operations Intern, Summer 2027. Support Windows server "
                "administration, assist with account provisioning, and monitor system "
                "health dashboards. Exposure to PowerShell scripting is helpful; "
                "willingness to learn automation is expected. No prior scripting "
                "experience required."
            ),
        },
        "APPLY",
    ),
    (
        "MAYBE — Baltimore metro, commutable-but-a-stretch per the location rule",
        {
            "id": "t11b", "title": "Help Desk Support Intern",
            "company": "Johns Hopkins Health System", "location": "Baltimore, MD",
            "description": (
                "Help Desk Support Intern, Summer 2027. Answer inbound support "
                "requests, reset accounts, troubleshoot desktop and printer issues, "
                "and document resolutions in the ticketing system. Onsite in "
                "Baltimore, Maryland. Open to undergraduate students."
            ),
        },
        "MAYBE",
    ),
    (
        # Also originally expected MAYBE. The classifier said APPLY and exposed
        # a genuine contradiction in the rubric: the APPLY section called
        # "willingness to obtain" an APPLY while the MAYBE section called "or in
        # progress" a MAYBE, and those overlap. Rubric now states one rule —
        # any carve-out is APPLY, a hard "must currently hold" is SKIP, no
        # middle tier — so this cannot flip between runs.
        "APPLY — Security+ required but with an 'or in progress' carve-out",
        {
            "id": "t11c", "title": "SOC Analyst Intern",
            "company": "Peraton", "location": "Herndon, VA",
            "description": (
                "SOC Analyst Intern, Summer 2027. Monitor security alerts, perform "
                "Tier 1 triage, and document incidents. CompTIA Security+ required, "
                "or in progress with completion within 6 months of start. Must be a "
                "US citizen. Herndon, VA onsite."
            ),
        },
        "APPLY",
    ),
    (
        "SKIP — Security+ hard required with no carve-out (the other side of that rule)",
        {
            "id": "t11d", "title": "Information Assurance Intern",
            "company": "General Dynamics IT", "location": "Fairfax, VA",
            "description": (
                "Information Assurance Intern. Candidates must currently hold an "
                "active CompTIA Security+ certification at time of application; this "
                "is a DoD 8570 IAT Level II requirement and cannot be waived or "
                "obtained after start. Support STIG compliance checks and audit "
                "documentation. Summer 2027, Fairfax VA."
            ),
        },
        "SKIP",
    ),
    (
        "SKIP — unpaid internship",
        {
            "id": "t12", "title": "IT Support Intern (Unpaid)",
            "company": "Community Nonprofit Alliance", "location": "Washington, DC",
            "description": (
                "Unpaid internship for academic credit only. Assist our small IT team "
                "with help desk tickets, printer troubleshooting, and laptop setup. "
                "This is an unpaid position; college credit can be arranged."
            ),
        },
        "SKIP",
    ),
    (
        "APPLY — genuinely remote internship, treated same as DC onsite",
        {
            "id": "t13", "title": "Information Security Intern - Remote",
            "company": "Cloudflare", "location": "United States (Remote)",
            "description": (
                "Information Security Intern, Summer 2027, fully remote within the US. "
                "Assist the security team with alert triage, phishing report review, "
                "access review documentation, and security awareness materials. Open to "
                "undergraduates. No certifications required."
            ),
        },
        "APPLY",
    ),
    (
        "SKIP — veteran-only program (OVIP-style restriction)",
        {
            "id": "t14", "title": "IT Support Intern - Veterans Program",
            "company": "Oracle", "location": "Reston, VA",
            "description": (
                "This internship is part of the Oracle Veteran Internship Program. "
                "Open to US veterans transitioning from active service and military "
                "spouses new to corporate experience. Provide desktop and help desk "
                "support to internal teams."
            ),
        },
        "SKIP",
    ),
    (
        "SKIP — new grad program, he graduates 2028",
        {
            "id": "t15", "title": "New Graduate IT Rotational Program",
            "company": "Accenture Federal", "location": "Arlington, VA",
            "description": (
                "Our New Graduate Rotational Program places recent graduates into IT "
                "operations, service desk, and infrastructure rotations. For students "
                "graduating in 2026. Full-time, permanent placement upon completion."
            ),
        },
        "SKIP",
    ),
]


def _reclassify(job: dict) -> tuple[str, str]:
    """Mirror of main.py's pre-filter chain + classifier. Must be kept in
    lockstep with run()'s gates or the tests will pass while prod diverges."""
    if _is_senior_role(job["title"]):
        return "SKIP", "Pre-filtered: seniority keyword in title"
    if _is_new_grad_role(job["title"]):
        return "SKIP", "Pre-filtered: new grad / full-time role, not an internship"
    if _is_non_internship_title(job["title"]):
        return "SKIP", "Pre-filtered: no internship marker in title"
    result = classify(job)
    return result.get("tier", "MAYBE"), result.get("reason", "")


def run():
    log.warning("=== Classifier fixture check: %d cases ===", len(FIXTURES))
    passed = 0
    for label, job, expected in FIXTURES:
        tier, reason = _reclassify(job)
        ok = tier == expected
        passed += ok
        flag = "PASS" if ok else "FAIL"
        log.warning("[%s] expected=%s got=%s | %s | %s", flag, expected, tier, label, reason[:110])
    log.warning("=== %d/%d passed ===", passed, len(FIXTURES))
    log.warning("(No DB writes or notifications — classify() was called directly.)")
    return passed == len(FIXTURES)


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
