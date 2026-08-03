"""Classifier unit check: run hand-written fixture postings through the
live pre-filter + classifier chain and assert the expected tier.

Unlike scraper/test_current_classifications.py (which samples real jobs
already stored in the DB), there's no existing DB to sample from yet for
this persona — these are synthetic fixtures covering every branch of the
inverted rubric in Beyonce_Candidate_Profile_and_Filters.md. This never
calls insert_job()/push_job(), so there is zero chance of a real DB write
or notification.

Run from the scraper_beyonce directory:
    cd scraper_beyonce && python test_classifications.py
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env.beyonce")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from main import _is_senior_role, _is_internship_or_student_title
from classifier import classify

logging.basicConfig(
    level=logging.WARNING,  # quiet the classifier's own INFO logs during the test
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


# Each fixture: (label, job dict, expected tier). Covers every branch of the
# inverted rubric — see Beyonce_Candidate_Profile_and_Filters.md.
FIXTURES = [
    (
        "Clear APPLY — Patient Access Rep, Atlanta hospital, pay in range",
        {
            "id": "test-1", "title": "Patient Access Representative",
            "company": "Piedmont Healthcare", "location": "Atlanta, GA",
            "description": (
                "Piedmont Healthcare is hiring a full-time Patient Access "
                "Representative for our Atlanta campus. Responsibilities include "
                "greeting patients, verifying insurance, collecting co-pays, "
                "scheduling appointments, and entering data into our EMR system. "
                "This is a full-time position paying $21.00-$24.00 per hour. "
                "HIPAA compliance training provided."
            ),
        },
        "APPLY",
    ),
    (
        "SKIP — internship title",
        {
            "id": "test-2", "title": "Healthcare Administration Intern — Summer 2026",
            "company": "WellStar Health System", "location": "Atlanta, GA",
            "description": (
                "Join our Summer 2026 internship program for students interested "
                "in healthcare administration. This internship runs 10 weeks and "
                "offers hands-on exposure to hospital operations."
            ),
        },
        "SKIP",
    ),
    (
        "SKIP — wrong location, no remote option",
        {
            "id": "test-3", "title": "Patient Registration Specialist",
            "company": "Atrium Health", "location": "Charlotte, NC",
            "description": (
                "Full-time Patient Registration Specialist needed onsite at our "
                "Charlotte, NC facility. Handles patient check-in, insurance "
                "verification, and scheduling. $19/hr. Onsite only, no remote work."
            ),
        },
        "SKIP",
    ),
    (
        "SKIP — explicit pay below floor",
        {
            "id": "test-4", "title": "Front Desk Receptionist",
            "company": "Downtown Family Practice", "location": "Atlanta, GA",
            "description": (
                "Small family practice seeking a front desk receptionist. Duties "
                "include answering phones, scheduling, and greeting patients. "
                "Pay starts at $16.00/hour. Full-time, Monday-Friday."
            ),
        },
        "SKIP",
    ),
    (
        "SKIP — hands-on clinical duties despite admin-sounding title",
        {
            "id": "test-5", "title": "Clinical Care Coordinator",
            "company": "Northside Animal Hospital", "location": "Atlanta, GA",
            "description": (
                "Clinical Care Coordinator responsible for restraining animals "
                "during exams, administering injections and vaccines, drawing "
                "blood for lab work, and assisting veterinarians with hands-on "
                "procedures. $20/hr."
            ),
        },
        "SKIP",
    ),
    (
        "SKIP — credential gap, requires CPC coding certification",
        {
            "id": "test-6", "title": "Medical Coder II",
            "company": "Emory Healthcare", "location": "Atlanta, GA",
            "description": (
                "Medical Coder II position. CPC certification required. Reviews "
                "charts and assigns ICD-10/CPT codes for billing. $24/hr."
            ),
        },
        "SKIP",
    ),
    (
        # NOTE: until the pre-filter was narrowed, this fixture passed for the
        # WRONG reason — "manager" was in _SENIOR_SIGNALS, so it never reached
        # Claude and the RN-credential rule it's meant to exercise was never
        # actually tested. It now goes to the classifier for real.
        "SKIP — credential gap, requires active RN license",
        {
            "id": "test-7", "title": "Case Manager",
            "company": "Grady Health System", "location": "Atlanta, GA",
            "description": (
                "Case Manager coordinates patient care plans across departments. "
                "Active RN license required. $34/hr."
            ),
        },
        "SKIP",
    ),
    (
        "MAYBE — hotel front desk, pay-risk (no shift/pay stated)",
        {
            "id": "test-8", "title": "Hotel Front Desk Agent",
            "company": "Marriott Atlanta Buckhead", "location": "Atlanta, GA",
            "description": (
                "Front Desk Agent needed for our Buckhead property. Check guests "
                "in and out, handle reservations, process payments, answer phones. "
                "Day shift, full-time."
            ),
        },
        "MAYBE",
    ),
    (
        "MAYBE — staffing agency, legitimate function, no named end client",
        {
            "id": "test-9", "title": "Administrative Assistant",
            "company": "Ultimate Staffing", "location": "Atlanta, GA",
            "description": (
                "Ultimate Staffing is seeking an Administrative Assistant for a "
                "client in the Atlanta area. Duties include data entry, phones, "
                "scheduling, and general office support. $19-21/hr, temp-to-hire."
            ),
        },
        "MAYBE",
    ),
    (
        "APPLY — Prior Auth at a medical practice (vs. SKIP if PBM)",
        {
            "id": "test-10", "title": "Prior Authorization Specialist",
            "company": "Peachtree Orthopedic Clinic", "location": "Atlanta, GA",
            "description": (
                "Peachtree Orthopedic Clinic seeks a Prior Authorization "
                "Specialist to obtain insurance pre-authorizations for procedures, "
                "verify coverage, and communicate with patients and payers. "
                "CMA or equivalent medical office experience preferred. $20-23/hr."
            ),
        },
        "APPLY",
    ),
    # --- Regression cases for the narrowed seniority pre-filter ---
    # Each of the next three titles was silently hard-SKIPped by the old
    # _SENIOR_SIGNALS list (inherited verbatim from the SWE-internship
    # pipeline) before ever reaching Claude. They are target-role fits.
    (
        "APPLY — Executive Assistant (was blocked by the 'executive' keyword)",
        {
            "id": "test-11", "title": "Executive Assistant",
            "company": "Cox Enterprises", "location": "Atlanta, GA",
            "description": (
                "Executive Assistant supporting a department leadership team. "
                "Manages calendars, schedules meetings and travel, answers and "
                "routes phone calls, prepares expense reports, greets visitors, "
                "and handles general office correspondence and data entry. "
                "Full-time, $24.00-$28.00 per hour. Proficiency with MS Office "
                "required."
            ),
        },
        "APPLY",
    ),
    (
        "APPLY — Staff Assistant (was blocked by the 'staff' keyword)",
        {
            "id": "test-12", "title": "Administrative Staff Assistant",
            "company": "Georgia Tech Research Institute", "location": "Atlanta, GA",
            "description": (
                "Administrative Staff Assistant providing front-office support: "
                "answering multi-line phones, scheduling conference rooms, data "
                "entry, filing, greeting visitors, and processing purchase "
                "requests. Full-time, non-exempt, $22.50/hour."
            ),
        },
        "APPLY",
    ),
    (
        "APPLY — Lead Patient Access Rep (was blocked by the 'lead' keyword)",
        {
            "id": "test-13", "title": "Lead Patient Access Representative",
            "company": "Northside Hospital", "location": "Atlanta, GA",
            "description": (
                "Lead Patient Access Representative — serves as the senior "
                "registrar on a hospital registration desk. Registers patients, "
                "verifies insurance eligibility, collects co-pays, and assists "
                "with escalated patient questions. Two years of patient access "
                "or medical front-office experience preferred. $23-26/hour."
            ),
        },
        "APPLY",
    ),
    # Guard: the trimmed pre-filter must STILL catch genuine leadership roles.
    (
        "SKIP — genuine director-level role (pre-filter must still fire)",
        {
            "id": "test-14", "title": "Director of Patient Access Services",
            "company": "Emory Healthcare", "location": "Atlanta, GA",
            "description": (
                "Director of Patient Access Services. Owns the patient access "
                "function across three hospital campuses, manages a team of 40+ "
                "registrars and four supervisors, sets departmental strategy and "
                "budget. Bachelor's required, 8+ years progressive leadership "
                "experience. $120,000-$150,000/yr."
            ),
        },
        "SKIP",
    ),
    # Management titles are no longer pre-filtered — the rubric now owns this
    # decision (it names Manager/Supervisor directly and treats Practice
    # Manager / Medical Office Manager as credential-gapped), so this verifies
    # the SKIP still happens, just via the classifier instead.
    (
        "SKIP — Medical Office Manager, now decided by the rubric not the pre-filter",
        {
            "id": "test-15", "title": "Medical Office Manager",
            "company": "Buckhead Family Medicine", "location": "Atlanta, GA",
            "description": (
                "Medical Office Manager to run daily operations of a five-provider "
                "practice. Supervises a front-desk team of six, handles staff "
                "scheduling, payroll approval, and vendor relationships. Requires "
                "5+ years of medical practice management experience. $30/hour."
            ),
        },
        "SKIP",
    ),
]


def _reclassify(job: dict) -> tuple[str, str]:
    if _is_senior_role(job["title"]):
        return "SKIP", "Pre-filtered: executive/leadership keyword in title"
    if _is_internship_or_student_title(job["title"]):
        return "SKIP", "Pre-filtered: internship/co-op/student-program marker in title"
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
        log.warning("[%s] expected=%s got=%s | %s | %s", flag, expected, tier, label, reason[:120])
    log.warning("=== %d/%d passed ===", passed, len(FIXTURES))
    log.warning("(No DB writes or notifications — classify() was called directly.)")
    return passed == len(FIXTURES)


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
