"""Guards the insider-group override in classifier.py.

This override closes a leak that put jobs the candidate is categorically
ineligible for INTO the feed — postings open only to a company's own returning
interns, its internal employees, a pre-selected cohort, active-duty service
members, or students enrolled abroad. 24 such rows were live at APPLY /
APPLY_CAVEAT when it was written, including gh:957897d315475c83, the RTX
"Software Engineer Intern - Spring 2027" that sent a false push notification on
the strength of one sentence buried in its Security Clearance block: "This
requisition is for an RTX intern returning for an internship in 2027."

Both directions matter, and the second one more. A miss leaves an ineligible
job on the list, which costs a few seconds to dismiss. A false positive
silently removes a real job, and nothing downstream ever surfaces it again —
which is why every "must NOT fire" case below is either a sentence that really
occurs in the stored corpus or the one-word rewrite of a real gate that turns
it into an ordinary posting.

Run:  cd scraper && python test_returning_intern_override.py
"""

import sys

import classifier as C

_pass = _fail = 0


def check(name, cond, detail=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{f' — {detail}' if detail else ''}")


def tier(title, desc="", company="Acme"):
    return C._apply_returning_intern_override(
        {"id": "test", "title": title, "description": desc, "company": company},
        {"tier": "APPLY", "reason": ""},
    )["tier"]


def blocked(title, desc="", company="Acme"):
    return tier(title, desc, company) == "INELIGIBLE"


# ── The bug this exists for. ────────────────────────────────────────────────
print("\n-- the RTX sentence --")

RTX_DESC = (
    "Security Clearance Type: None/Not Required&#xa;"
    "Security Clearance Status: Not Required This requisition is for an RTX "
    "intern returning for an internship in 2027.&#xa;"
    "Are you ready to explore the world of aerospace and defense? You will use "
    "Python, CI/CD, Git and Linux to build automation and dashboards."
)
check("gh:957897d315475c83 verbatim — gate glued to the end of the clearance block",
      blocked("Software Engineer Intern - Spring 2027", RTX_DESC, "RTX"),
      "the title is clean; the only evidence is one sentence 4,000 chars in")

# RTX regenerates this boilerplate with the year embedded, so the plural and the
# relative-clause rewrite are the likeliest recurrence of this exact bug.
check("...pluralised: 'for RTX interns returning'",
      blocked("Software Engineer Intern - Spring 2027",
              "This requisition is for RTX interns returning for an internship in 2027.", "RTX"))
check("...as a relative clause: 'for an RTX intern who is returning'",
      blocked("Software Engineer Intern - Spring 2027",
              "This requisition is for an RTX intern who is returning in 2027.", "RTX"))


# ── Must be blocked. Every one of these was live in the database. ───────────
print("\n-- insider-group gates must be ruled ineligible --")

check("Truist — 'This requisition is open to 2026 Truist Interns only.'",
      blocked("Technology Intern", "This requisition is open to 2026 Truist Interns only.", "Truist"))
check("Truist — 'Applicants who were not 2026 Truist interns will not be considered.'",
      blocked("Technology Intern",
              "Applicants who were not 2026 Truist interns will not be considered.", "Truist"))
check("Truist — 'Must be a 2026 Truist Intern' in the qualifications",
      blocked("Technology Intern", "Must be a 2026 Truist Intern", "Truist"))
check("Baker Tilly — 'Must be a current Summer 2026 Baker Tilly Intern'",
      blocked("Associate", "Must be a current Summer 2026 Baker Tilly Intern", "Baker Tilly"))
check("Kearney — 'Must have been a 2026 Summer Kearney & Company Intern'",
      blocked("Intern", "Must have been a 2026 Summer Kearney & Company Intern", "Kearney & Company"))
check("HNTB — the gate sits alone on its own line",
      blocked("Strategic Technology Intern - SED Division",
              "What We're Looking For&#xa;For current/former HNTB Interns ONLY.&#xa;"
              "At HNTB, you can create a career that is meaningful to you.", "HNTB"),
      "'&#xa;' must not break the phrase, and must not weld the two lines together")
check("Regions — the gate is in the TITLE, after the role name",
      blocked("2027 ETP Analyst-Corporate Banking Group. 2026 Regions Interns Only.",
              "Thank you for your interest in a career at Regions.", "Regions Bank"))
check("Target — 'Current Interns Only-' (the rubric's confirmed live miss)",
      blocked("Current Interns Only- Technology Leadership Program -Summer 2026 Intern Posting",
              "An ordinary strong SWE internship description.", "Target"))
check("Williams — the gate sits immediately after the EEO paragraph",
      blocked("Engineering Intern",
              "...or any other basis protected under applicable discrimination law. This job "
              "posting is intended for Williams' summer 2026 interns to apply to and will "
              "un-post after 8/05/26.", "Williams"))
check("Travelers — 'Applications outside of this audience will not be considered'",
      blocked("Engineering Development Program (EDP) - Intern",
              "The intent of this position is to provide our internal employees, 2026 Travelers "
              "Summer Interns and Summer Students the ability to apply. Applications outside of "
              "this audience will not be considered at this time.", "Travelers"))
check("Regions — 'only open to former Regions contract workers'",
      blocked("Software Engineer",
              "At this time, this role is only open to former Regions contract workers.",
              "Regions Bank"))
check("PhoenixTeam — a pre-selected cohort",
      blocked("GenAI Value Engineering Co-op / Intern",
              "This opportunity is open only to Drexel University students who have already been "
              "officially selected for an interview through the upcoming Fall Drexel Co-op "
              "Program.", "PhoenixTeam"))
check("Scale AI — an event-attendee cohort",
      blocked("ICML 2026 - University Recruiting",
              "This posting is for candidates (interested in research intern roles) who attended "
              "ICML '26 and met with a member of our team.", "Scale AI"))
check("AVEVA — '(Drexel University Co-ops Only)' buried in the body",
      blocked("Software Developer Intern- Drexel Co-op US",
              "Job Title: Software Developer Intern (Drexel University Co-ops Only) Employment "
              "type: Full-time Intern (Fall)", "AVEVA"))
check("Blue Origin — 'Successfully completed an internship with Blue Origin in 2026.'",
      blocked("2026 Intern Conversion - Aerospace Software Apps Engineer I",
              "Minimum Qualifications: Successfully completed an internship with Blue Origin in "
              "2026. Enrolled or recently graduating student.", "Blue Origin"))
check("SIG — a cohort defined by where the university is",
      blocked("Trading Operations Analyst Intern",
              "This program offers students currently enrolled at universities in Hong Kong or "
              "Singapore the opportunity to intern in the US.",
              "Susquehanna International Group (SIG)"))
check("'internal candidates only'",
      blocked("Software Engineer Intern", "This role is posted for internal candidates only."))

# Title-only gates. Three of these have an EMPTY description in the database:
# the title is the only evidence that exists, and the stored reason on the
# Northrop row is a hallucinated guess about hardware and clearances.
print("\n-- title-only gates (the description proves nothing, or is empty) --")
for title, company in [
    ("Intern Conversion: Software Developer", "IBM"),
    ("Research Extern Intern conversion", "IBM"),          # lowercase 'c'
    ("2026 Intern Conversion - Aerospace Software Apps Engineer I", "Blue Origin"),
    ("Platform Software Engineer 1 - Full-time Intern Conversion", "Oracle"),
    ("Tax and Audit Associates (BT Summer Intern Conversions Only)", "Baker Tilly US"),
]:
    check(f"conversion req — {title[:44]}…", blocked(title, "", company))

for title, company in [
    ("2027 Returning Intern Software Engineer", "Northrop Grumman"),
    ("Returning Intern: Software Developer", "IBM"),
    ("Returning Summer Analyst", "Accenture Federal Services"),   # 'Analyst', not 'Intern'
]:
    check(f"returning-intern req — {title[:40]}…", blocked(title, "", company))

print("\n-- military-status gates --")
check("'SkillBridge' in the title", blocked("SkillBridge Intern - Software Engineer", "", "Rise8"))
check("'Skillbridge' lowercase b", blocked("Skillbridge Internship -IO", "", "Two Six Technologies"))
check("'SKILLBRIDGE' all caps",
      blocked("NUMERICAL CONTROL PROGRAMMER SKILLBRIDGE INTERN", "", "Newport News Shipbuilding"))
check("'Skill Bridge' spaced", blocked("Skill Bridge Internship", "", "DS2"))
check("'Active Duty Only' in the title", blocked("Internship - Active Duty Only", "", "Aura Health"))
check("the statutory 180-day separation window",
      blocked("Mission Ops Intern",
              "Eligibility: Has served at least 180 days on active duty. Is within 180 days of "
              "separation or retirement.", "Two Six Technologies"))
check("'Currently on active duty and eligible'",
      blocked("Software Engineer Intern",
              "Qualifications: Currently on active duty and eligible for the program.", "Rise8"))
check("Oracle's OVIP — programme name in the body, OVIP in the title",
      blocked("OCI Software Engineer Intern - OVIP",
              "About the Oracle Veteran Internship Program (OVIP): Oracle is proud to sponsor an "
              "internship that exposes transitioning military veterans and active-duty Military "
              "Spouses to the corporate culture.", "Oracle"))


# ── Must stay actionable. A false positive here silently deletes a real job. ──
print("\n-- real internships must NOT be caught --")

# The flattener turns block markup and "&#xa;" into a SENTENCE STOP, not a
# space. Without that, a bullet ending in "...interns" welds onto the next
# block's "Only those selected..." and manufactures a gate out of two innocent
# sentences. This is the false-positive class the override itself would create.
check("a bullet ending in 'interns' + 'Only those selected…' in the next block",
      not blocked("Software Engineer Intern",
                  "<ul><li>Mentorship from our 2026 summer interns</li></ul>"
                  "<p>Only those selected for an interview will be contacted.</p>"))
check("...the same thing across an '&#xa;' newline",
      not blocked("Software Engineer Intern",
                  "Hear from our 2026 summer interns&#xa;"
                  "Only candidates selected for interview will be contacted."))
check("...and 'current interns' + 'Only applicants with work authorization'",
      not blocked("Software Engineer Intern",
                  "You will be paired with current interns&#xa;"
                  "Only applicants with work authorization will be considered."))
check("...and across the title/description seam",
      not blocked("Software Engineering Summer Interns",
                  "Only candidates selected for an interview will be contacted."))

# The region gate must never fire on the candidate's own region. This is the
# worst outcome the whole family can produce: hiding a job in his home city.
for region in ["Metro Atlanta", "Greater Atlanta", "North Georgia", "the United States",
               "Georgia or Florida", "the Atlanta area"]:
    check(f"'enrolled at universities in {region}'",
          not blocked("Software Intern", f"Open to students enrolled at universities in {region}."),
          "a US region is not a restriction — he attends Georgia State, in Atlanta")

# A veteran programme MENTIONED is not a veteran programme APPLIED FOR. The
# title token is the required second factor.
check("a posting that merely sponsors a Military Internship Program",
      not blocked("Software Engineer Intern",
                  "We also sponsor a Military Internship Program for transitioning service "
                  "members; this role is open to all students."))
check("'Ask about our Veterans Internship Program…'",
      not blocked("Software Engineer Intern",
                  "Ask about our Veterans Internship Program if you are a service member."))
check("EEO boilerplate naming protected veterans",
      not blocked("Software Engineer Intern",
                  "All qualified applicants receive consideration without regard to protected "
                  "veteran status, including active duty wartime or campaign badge veterans."),
      "10,575 stored rows contain 'veteran', almost all of it this paragraph")

# A prior internship at SOMEBODY ELSE is a preferred qualification, not a gate.
check("'completed an internship at NASA in 2025' on a posting that is not NASA's",
      not blocked("Software Engineer Intern",
                  "Preferred: has completed an internship at NASA in 2025 or similar research "
                  "experience.", "Anduril"))
check("a generic prior-internship preference",
      not blocked("Software Engineer Intern",
                  "Preferred: completed an internship in software engineering at a technology "
                  "company."))

# "…interns only" as a PERK is not a restriction on who may apply. Zero stored
# rows do this with interns, but 14 do the equivalent with employees.
check("'Housing is provided for our 2026 summer interns only.'",
      not blocked("Software Engineer Intern", "Housing is provided for our 2026 summer interns only."))
check("'Social events are available to current interns only.'",
      not blocked("Software Engineer Intern", "Social events are available to current interns only."))
check("Bilt's 'Exclusive Employee only Bilt Points'",
      not blocked("Software Engineer Intern",
                  "Exclusive Employee only Bilt Points — we give our employees points.", "Bilt"))
check("UST's 'Company-paid Employee Only benefits'",
      not blocked("Software Engineer Intern",
                  "US employees are eligible for the following Company-paid Employee Only "
                  "benefits: basic life insurance.", "UST"))
check("Copart's E-Verify '(For U.S. applicants and employees only)'",
      not blocked("Software Engineer Intern",
                  "Participates in E-Verify (For U.S. applicants and employees only).", "Copart"))

# The word "returning" is taught elsewhere in the rubric as a POSITIVE signal —
# its absence is a tell for a full-time role. These are the four shapes it
# actually takes in the corpus, and all four are legitimately surfaced jobs.
check("Google's 'Returning to a degree program after completion of the internship'",
      not blocked("Software Engineering Intern",
                  "Returning to a degree program after completion of the internship.", "Google"),
      "the rubric already records this exact sentence as a confirmed false SKIP")
check("Optiver's 'earn a return internship or full-time offer'",
      not blocked("Software Engineer Intern",
                  "You will have the opportunity to earn a return internship or full-time offer.",
                  "Optiver"))
check("Citi's 'may be invited to return as full-time Analysts'",
      not blocked("Summer Analyst",
                  "Top performers may be invited to return as full-time Analysts.", "Citi"))
check("AWS's 'students returning to school after the internship'",
      not blocked("SDE Intern",
                  "Students returning to school after the internship are eligible for a return "
                  "offer.", "AWS"))

# Title-scoped tokens are ordinary prose in a body.
check("Notion's 'internship conversion' as a list of job duties",
      not blocked("Head of Early Career Recruiting",
                  "Own university recruiting, internship conversion, emerging pipelines and "
                  "campus events.", "Notion"),
      "this is why the conversion pattern never reads a description")
check("an intern-to-full-time conversion RATE quoted as a selling point",
      not blocked("Software Engineer Intern",
                  "Our intern conversion rate is over 70%; many interns receive offers."))
check("Penn State ARL: 'an authorized DoD SkillBridge partner'",
      not blocked("Research and Development Engineer Intern",
                  "ARL is an authorized DoD SkillBridge partner and welcomes all applicants.",
                  "The Applied Research Laboratory at Penn State University"),
      "SkillBridge in a BODY is a partnership boast; only the TITLE is a gate")

# Groups that name nobody, and the one group he IS in.
check("'Summer Interns Only' names no company",
      not blocked("Software Engineer Intern", "Parking passes are issued to Summer Interns Only."))
check("'Full-Time Students Only' is an ordinary requirement",
      not blocked("Software Engineer Intern", "Full-Time Students Only may apply."))
check("the candidate's OWN school is not a restriction",
      not blocked("Software Engineer Co-Op",
                  "Job Title: Developer Intern (Georgia State University Co-ops Only)"),
      "blocking his own school would be the worst possible failure")

# Near-miss sentences for each text pattern.
check("'This position is for a software engineering intern joining our team.'",
      not blocked("Software Engineer Intern",
                  "This position is for a software engineering intern joining our platform team."),
      "the exclusivity frame alone matches 1,337 stored rows — it needs the second half")
check("'This role is for current students who will complete an internship in 2027.'",
      not blocked("Software Engineer Intern",
                  "This role is for current students who will complete an internship in 2027."))
check("'Applicants who are not available for the full internship period…'",
      not blocked("Software Engineer Intern",
                  "Applicants who are not available for the full internship period will not be "
                  "considered."),
      "a scheduling requirement he can satisfy, not a statement about intern status")
check("'Incomplete applications will not be considered.'",
      not blocked("Software Engineer Intern", "Incomplete applications will not be considered."))
check("'Must be a current student…; prior intern experience is a plus.'",
      not blocked("Software Engineer Intern",
                  "Must be a current student enrolled full time in a degree program; prior intern "
                  "experience is a plus."),
      "the semicolon stop is what keeps the two clauses apart")
check("'Must be a 2027 graduating senior with prior intern experience.'",
      not blocked("Software Engineer Intern",
                  "Must be a 2027 graduating senior with prior intern experience."),
      "no NAMED employer in front of 'intern', so it is experience, not membership")
check("'IBM co-op program' in an IBM posting",
      not blocked("Software Developer Spring Co-op 2027",
                  "IBM co-op program, open to any accredited college or university.", "IBM"),
      "the original false positive this whole family of overrides is calibrated against")


# ── The override only ever downgrades an actionable verdict. ────────────────
print("\n-- the override never promotes, and marks hard ineligibility --")
already = C._apply_returning_intern_override(
    {"id": "t", "title": "Intern Conversion: Software Developer", "description": "", "company": "IBM"},
    {"tier": "INELIGIBLE", "reason": "already out"},
)
check("an INELIGIBLE verdict is left alone", already["reason"] == "already out")

pending = C._apply_returning_intern_override(
    {"id": "t", "title": "Intern Conversion: Software Developer", "description": "", "company": "IBM"},
    {"tier": "PENDING", "reason": "classifier down"},
)
check("a PENDING verdict is left alone", pending["tier"] == "PENDING",
      "a parked job must never be given a verdict the model did not produce")

gated = C._apply_returning_intern_override(
    {"id": "gh:957897d315475c83", "title": "Software Engineer Intern - Spring 2027",
     "description": RTX_DESC, "company": "RTX"},
    {"tier": "APPLY", "reason": "Python/CI-CD, real SWE work"},
)
check("it sets hard_ineligible", gated.get("hard_ineligible") is True,
      "without this, _never_skip_github_sourced resurrects the gh: row to APPLY_CAVEAT "
      "and it pushes anyway")
check("...and _never_skip_github_sourced then leaves the gh: row alone",
      C._never_skip_github_sourced({"id": "gh:957897d315475c83"}, dict(gated))["tier"] == "INELIGIBLE")
check("...and the reason quotes the sentence that decided it",
      "intern returning" in gated["reason"])
check("it never touches suggested_resume/salary",
      set(gated) == {"tier", "reason", "hard_ineligible"})

# A missing description is normal: main.retry_pending builds the job dict from a
# DB row whose description can be NULL.
check("a job with no description at all does not raise",
      tier("Software Engineer Intern", None) == "APPLY")
check("a job with no title at all does not raise",
      C._apply_returning_intern_override({"id": "t"}, {"tier": "APPLY"})["tier"] == "APPLY")


# ── Regression: the gh: resurrection path. ─────────────────────────────────
#
# The override originally returned EARLY whenever the tier was already
# INELIGIBLE, so hard_ineligible was never stamped on a verdict the model got
# right by itself. _never_skip_github_sourced then read that verdict as an
# ordinary judgment call and promoted the gh: row back to APPLY_CAVEAT, which
# still pushes. The failure was invisible in isolation and only appeared end to
# end: two consecutive classify() calls on RTX gh:957897d315475c83 returned
# APPLY_CAVEAT and INELIGIBLE from byte-identical input at temperature=0,
# depending purely on whether the model happened to reach INELIGIBLE first.
#
# The perverse part, and the reason this test exists: improving the rubric made
# it WORSE, because a better rubric makes the model reach INELIGIBLE unaided
# more often, and every one of those was silently undone.
print("")
print("-- regression: a model-issued INELIGIBLE on a gh: row survives --")

_model_first = C._apply_returning_intern_override(
    {"id": "gh:957897d315475c83", "title": "Software Engineer Intern - Spring 2027",
     "description": RTX_DESC, "company": "RTX"},
    {"tier": "INELIGIBLE", "reason": "Restricted to RTX interns returning in 2027."},
)
check("a model-issued INELIGIBLE on a gated row is stamped hard_ineligible",
      _model_first.get("hard_ineligible") is True,
      "without the stamp _never_skip_github_sourced resurrects it to APPLY_CAVEAT")
check("...and the model's own reason is preserved, not overwritten",
      _model_first["reason"] == "Restricted to RTX interns returning in 2027.")
check("...and the tier is not disturbed",
      _model_first["tier"] == "INELIGIBLE")

_rescued = C._never_skip_github_sourced({"id": "gh:957897d315475c83"}, dict(_model_first))
check("...so the gh: row still reads INELIGIBLE after the rescue pass",
      _rescued["tier"] == "INELIGIBLE",
      "this is the assertion that actually failed in production")

# An UNGATED gh: row must still be rescued — the flag must not leak onto rows
# the gate never matched, or this override would start hiding real jobs.
_ungated = C._apply_returning_intern_override(
    {"id": "gh:deadbeef", "title": "Software Engineer Intern",
     "description": "Build backend services in Python. Returning to your degree "
                    "program after the internship is required.", "company": "Acme"},
    {"tier": "INELIGIBLE", "reason": "model judgment call"},
)
check("an ungated gh: row is NOT stamped hard_ineligible",
      _ungated.get("hard_ineligible") is None)
check("...and _never_skip_github_sourced still rescues it",
      C._never_skip_github_sourced({"id": "gh:deadbeef"}, dict(_ungated))["tier"] != "INELIGIBLE",
      "the curated-source rescue must keep working for ordinary judgment calls")

# PENDING is a queue state and must survive the new three-tier guard untouched.
_pending_gated = C._apply_returning_intern_override(
    {"id": "gh:957897d315475c83", "title": "Software Engineer Intern - Spring 2027",
     "description": RTX_DESC, "company": "RTX"},
    {"tier": "PENDING", "reason": "classifier down"},
)
check("a PENDING row on a GATED posting is still left completely alone",
      _pending_gated["tier"] == "PENDING" and _pending_gated.get("hard_ineligible") is None)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)