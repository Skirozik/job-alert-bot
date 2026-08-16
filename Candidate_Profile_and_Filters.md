# Candidate Profile + Filter Rules (for the job-alert bot's classifier)
*This is what the bot uses to decide APPLY / APPLY_CAVEAT / INELIGIBLE. Feed it to the Claude classifier as context for every listing.*

---

## Who I am
- **Name:** Ifiok Zachary Inyang
- **School:** Georgia State University · BS Computer Science · graduating **December 2027** (rising senior)
- **Location:** Atlanta, GA · **Work authorization:** U.S. Citizen (no sponsorship needed; eligible for ITAR/citizen-required roles)
- **Standout asset:** a **published iOS app on the App Store** (shipped solo, real paying customers)

## My stack
- **Languages:** Python, Swift, JavaScript, TypeScript, SQL, HTML/CSS
- **Frontend/Mobile:** SwiftUI, React, React Native, Next.js, Vite, Tailwind, Axios
- **Backend:** Node.js, Express, FastAPI, Flask, Uvicorn, Pydantic
- **Databases/Data:** PostgreSQL, Redis, Firebase, Prisma ORM, pandas
- **Infra/DevOps:** Docker, docker-compose, BullMQ (job queues), ioredis, Cloudflare Workers
- **AI:** Anthropic Claude + OpenAI APIs, agentic/tool-calling systems, prompt engineering (application layer, NOT ML research)
- **Other tools:** REST/GraphQL, Stripe, Git, BeautifulSoup, PDF parsing (pdfplumber), Streamlit

## Target areas (priority order)
1. **iOS / Mobile** (strongest — shipped App Store app)
2. **Full-stack / Web** (React, Next.js, Vite, Node, Python)
3. **Python / Backend** (FastAPI, Flask, Node, PostgreSQL)
4. **AI-application / AI Engineer** (building WITH LLM APIs and agents)
5. **General SWE Intern** at any tech-forward company

## Location rules
- **Prefer:** Atlanta GA (local), Remote (US)
- **OK:** ANY US city — willing to relocate anywhere in the US for an internship term (semester or summer). This is a genuine "OK," not a soft negative: an onsite requirement in a non-Atlanta US city is NOT a reason to cite when explaining a APPLY_CAVEAT instead of APPLY, and must not be weighed alongside other factors as a partial strike against the role. Only non-US-without-remote (see Exclude below) is a real location concern.
- **Exclude:** non-US roles unless explicitly US-remote eligible

---

## TRIAGE RUBRIC (the bot applies this to every listing)

**Three labels. Read this section before anything else — it replaced an older APPLY/MAYBE/SKIP scheme and the difference is not cosmetic.**

The old scheme had a MAYBE tier that I never looked at, so a MAYBE was in practice a deletion. The new scheme is built around what I actually do: I read one list, and I decide. Your job is to hand me that list with honest labels on it — **not** to decide for me.

| Label | Meaning | Where it goes |
|---|---|---|
| `APPLY` | Clean fit. No caveat worth mentioning. | My list |
| `APPLY_CAVEAT` | Worth applying, with exactly one specific reservation I should know first. | My list, badged |
| `INELIGIBLE` | A HARD block. I literally cannot be hired. | Collapsed behind a toggle |

**The asymmetry that matters:** labelling something `INELIGIBLE` hides it from me. Labelling it `APPLY_CAVEAT` costs me ten seconds. So when the two are close, it is always `APPLY_CAVEAT`. **If it is a judgment call rather than a hard rule, it is `APPLY_CAVEAT`, never `INELIGIBLE`.**

### 🟢 APPLY — clean fit
Everything below still applies, and no caveat is worth flagging.
- Title contains: Software Engineer/Developer Intern, iOS, Mobile, Swift, Full-Stack, Frontend, Backend, Web, React, Node, Next.js, Python, FastAPI, AI Engineer, Applied AI, AI-application, SWE Intern
- Stack overlap with any of: React, Next.js, Vite, Node.js, Python, FastAPI, Flask, Swift, TypeScript, JavaScript, PostgreSQL, Docker, Redis, Prisma, Anthropic/OpenAI APIs
- US location (incl. Remote-US) or anywhere I'd relocate (all US cities qualify)
- No advanced-degree requirement
- **A genuine internship (real term/duration, real company) that asks for ONE adjacent language I don't have as a primary (C++, Java, C#, Go, Kotlin, etc.) but has meaningful overlap with my stack (Python/JS/TS present, or the work is application-layer engineering, not deep systems programming) — this is APPLY, not a hedge.** "I shipped iOS in Swift, web in Node, tools in Python, I pick up languages fast" is a strong, credible pitch, and internships exist specifically to teach the stack. Reserve APPLY_CAVEAT for roles where the overlap is genuinely thin (see below) — one non-primary required language alongside real overlap is not thin.
- **A strong-company internship in an unfamiliar PRODUCT domain (AV/audio, automotive, energy, fintech ops, water/climate tech, manufacturing, biotech, etc.) where the actual day-to-day work is still real software engineering** (APIs, services, modern practices, mentorship) — domain novelty alone doesn't make this a stretch. Only treat domain as a real concern if the role itself is non-software (e.g. hardware/RF/mechanical engineering — see INELIGIBLE). **Run this exact test: read the "What You'll Do"/responsibilities section specifically — is it describing software verbs (build, write, design, debug, integrate, maintain, test, ship) applied to tools, dashboards, APIs, backend services, or data pipelines? If yes, this is a real software engineering role, full stop, regardless of what the COMPANY manufactures or sells (physical hardware, consumer devices, industrial equipment, biotech, etc.) — a hardware/manufacturing company's internal tooling and backend/infra work is still software engineering. Do not let "the company builds a physical product" pull the decision toward APPLY_CAVEAT or INELIGIBLE when the actual described responsibilities are software work; that reasoning is only valid if the ROLE's own responsibilities are hardware/mechanical/RF engineering, not just the company's industry.**
- **A company-wide rotational/track-based internship program** (e.g. a "Technology Program" spanning SWE/AI/Data/etc. tracks) where I meet the stated eligibility — apply confidently even if the specific team placement isn't guaranteed yet; the downside case is still landing in a real tech track at a real company.
- **An "AI Intern" / applied-AI role with Python (or similar) overlap**, even if it also touches ML model evaluation/optimization as part of applied engineering work, or is built on a specific cloud ecosystem (Microsoft/AWS/etc.) — still application-layer AI work, not the ML-research INELIGIBLE category below, unless the role explicitly centers on training or researching novel models (see INELIGIBLE). This also applies to Performance/Systems/Infrastructure/Platform Engineer roles that build tooling, benchmarks, or dashboards AROUND ML workloads (profiling, validating, benchmarking, orchestration) — that's applied/infra engineering, APPLY, even if the description uses "deep learning," "PyTorch," or "model" language, as long as the title doesn't say Researcher/Scientist and the required quals (not the "nice to have" list) don't demand ML research skills. **Run this exact test before invoking the ML-research INELIGIBLE category on one of these titles: does the "Required"/"Minimum Qualifications" section — NOT the "nice to have"/"preferred"/"stand out" section — demand ML research skills (PyTorch, model training, etc.)? If PyTorch/deep-learning/model-training language appears ONLY in a nice-to-have/preferred/stand-out list, it does not count as a research signal at all for this test, full stop — do not let it pull the decision toward INELIGIBLE or even APPLY_CAVEAT. A Performance/Systems/Infrastructure/Platform-titled internship whose required quals are ordinary CS-fundamentals language (a degree in progress, a general-purpose language, Linux/git, "foundational interest" in ML) is APPLY, regardless of how much ML vocabulary shows up in the "what you'll be doing" or "stand out" sections.**

### 🟡 APPLY_CAVEAT — worth applying, one reservation

Same list as APPLY, shown with a badge. Use this whenever the role is worth my time but something specific is worth knowing up front.

**The `reason` field for an `APPLY_CAVEAT` IS the caveat, and it must be under 12 words.** Not a sentence about the match — just the reservation, stated plainly. Good examples:
- `strong C++ + Unreal, no game projects`
- `prefers 3.5 GPA`
- `Rust-heavy, mentorship offered`
- `hybrid 4 days onsite in Seattle`
- `posting is vague about actual duties`

Write the caveat so I can act on it in one glance. If you cannot name a specific reservation in under 12 words, the label is `APPLY`, not `APPLY_CAVEAT` — a vague caveat is worse than none.

#### Unfamiliar programming languages — three bands, and none of them is INELIGIBLE

A language I don't know is **never** a hard block. It cannot produce `INELIGIBLE` under any circumstances; that label is reserved for the eight conditions listed below and "doesn't know C++" is not one of them. My strengths are **Python, TypeScript, JavaScript and Swift**, with **Java, C and C++ at coursework level**. Decide the band from how the posting phrases the requirement:

| How the posting names it | Label |
|---|---|
| Listed among several accepted options — "C++, Java, Python, or similar" | `APPLY`, no caveat |
| Named once, no framework or domain attached — "experience with Go" | `APPLY`, no caveat |
| Named with an **intensifier** ("strong", "expert", "proficient", "deep experience") **AND** paired with an **unfamiliar domain or framework** (Unreal, embedded, firmware, graphics, kernel, HFT) | `APPLY_CAVEAT`, naming **both** in the reason |

Both conditions must hold for the third band. "Strong Python" is my own language — no caveat. "Some exposure to Unreal" has no intensifier — no caveat. Only the combination earns one, and the caveat names the language *and* the domain, e.g. `strong C++ + Unreal, no game projects`.

**This applies identically to Rust, Go, Kotlin, C# and Scala.** An internship exists to teach the stack; employers hiring interns expect to train them. Limited knowledge of a language is a normal thing for a rising junior to bring to a role, and it is my call whether to spend the application, not yours.

**Typical caveat cases:**
- Data Engineering, general "Software" intern at a non-tech company, DevOps/Cloud, QA/Test, startup generalist — where the actual overlap with my stack is thin (not just "one extra language required" — see APPLY above for that case)
- An Android/Kotlin mobile role (adjacent to my iOS strength but no other overlap — flag as a stretch)
- A role at a strong company where the fit is genuinely uncertain (not just an unfamiliar domain with real SWE work — see APPLY above — but where it's unclear the role is even software engineering, or overlap is minimal)
- **An unfamiliar programming language is NEVER a reason to INELIGIBLE an internship. Not C++, not Rust, not Go, not C#, not Scala, not COBOL — no language, at any depth, in any quantity.** An internship is a position whose explicit purpose is to teach the stack; employers hiring interns expect to train them, and "limited knowledge of C++" is a normal thing for a rising junior to bring to a C++ internship. Judge the role on whether the WORK is software engineering and whether the posting is a real internship. If the answer to both is yes, the language it happens to use cannot move it below APPLY_CAVEAT, and a genuine Target-area match with real stack overlap elsewhere still goes APPLY.
  - This is the single most important calibration in this document, because its failures are invisible: a job silently hidden for a language never appears on the dashboard's default view, so it looks identical to never having been found at all. Confirmed live twice — a Go distributed-systems internship labelled INELIGIBLE because "Go is not a language the candidate has credible experience defending", and Epic Games' "Gameplay Programmer Intern" labelled INELIGIBLE for requiring "deep C++ systems and game-engine expertise". Both were wrong. Both were internships. Both are exactly the kind of role worth applying to.
  - **This overrides any stack-overlap reasoning elsewhere in this document.** If you find yourself writing a reason that contains a phrase like "no overlap with the candidate's stack", "not a language he can credibly defend", or "requires deep expertise in X" about an INTERNSHIP, stop — the correct tier is at least APPLY_CAVEAT. Reasons of that shape are a full-time-hire standard applied to a role that exists to train people.
  - **C++-heavy software domains are APPLY_CAVEAT, not INELIGIBLE:** gameplay and engine programming, graphics and rendering, game tools, simulation, flight and avionics software, robotics software, systems and performance work. These are all software engineering. What they are not is *my strongest* lane, which is exactly what APPLY_CAVEAT is for.
  - The genuine exclusions below are about the role not being SOFTWARE at all — hardware, electrical, mechanical, RF, FPGA/ASIC/RTL design. That is a different axis from which language the software is written in, and it is the only one that still SKIPs.
- **When in doubt between APPLY_CAVEAT and INELIGIBLE for an internship at a real company, always choose APPLY_CAVEAT.** It is always better to surface a stretch opportunity than to silently miss it. But when in doubt between APPLY and APPLY_CAVEAT for a genuine internship with real (even partial) stack overlap and no hard disqualifier, default to APPLY — APPLY_CAVEAT is for weak/uncertain fits, not merely "not a perfect match."

### 🔴 INELIGIBLE — hard blocks only

**This list is exhaustive. Nothing else may be labelled `INELIGIBLE`.** These are the only conditions under which I literally cannot be hired:

1. **Graduation date outside the posting's stated window** — it names a window and December 2027 falls outside it.
2. **A security clearance I do not already hold.** Note carefully: "must be able to obtain", "clearance sponsorship available", "eligible for", Public Trust, and background investigations are all post-hire processes and are **APPLY**, not blocks. Only an already-held active clearance blocks.
3. **MS or PhD required** — genuinely required, not "preferred". A posting saying "Bachelor's or Master's" is open to me.
4. **2+ years of professional experience as a HARD requirement** — "preferred" or "a plus" is not hard.
5. **Not actually an internship** — a new-grad or full-time role.
6. **A school-specific program I am not eligible for** — e.g. a Drexel-only co-op. I attend Georgia State.
7. **Unpaid full-time.**
8. **Work authorization I do not have** — which in practice means **the role is based outside the United States**. I am a US citizen with no right to work in India, Canada, the UK, the EU, Singapore, or anywhere else, so a posting based abroad is a hard block, exactly like a clearance I don't hold. This is the ONLY location-based block, and it is a real one: mark it `INELIGIBLE`, not `APPLY_CAVEAT`. A role is only *not* blocked if it is US-based, or explicitly open to US-remote candidates.
   - **Do not confuse this with sponsorship.** A US-based role saying "we do not sponsor visas" is fine — I need no sponsorship. Visa language is never a blocker; a foreign *location* always is.
   - **Watch for US cities that share a name with a foreign one — read the state code, not just the city.** `Dublin, OH`, `Delhi, MI`, `London, KY`, `Toronto, OH`, `Paris, TX`, `Berlin, NH`, `Manchester, NH` and `Birmingham, AL` are all in the United States and are all fine. A two-letter US state abbreviation after the city is the tell.
   - Multi-location postings that include at least one US site are **not** blocked — judge them on the US option.

Anything not on that list — weak stack overlap, unfamiliar language, unfamiliar domain, an odd company, a vague posting, a long commute, a competitive-sounding program — is `APPLY` or `APPLY_CAVEAT`. Those are my calls to make, not yours.

**Still genuinely out of scope** (these are not software-engineering roles at all, so they are `INELIGIBLE` under rule 5 — the role is not the job I am looking for):
- **Not actually an internship — check this FIRST, before evaluating stack fit.** A strong skill/stack match does NOT override this; many full-time roles will look like a "perfect fit" on paper, and that is exactly the trap to avoid. INELIGIBLE if the description signals a full-time hire for graduates, e.g.: it says the role spans multiple experience levels ("new grads through senior/staff", "all levels", "entry-level to senior"), it states an annual salary/compensation range typical of full-time employment (e.g. "$130K–$240K", "Compensation Range: $X"), it lists full-time-employee benefits (health insurance, 401k, unlimited PTO, equity), or the posting never uses "intern", "internship", "co-op", or names a specific term/duration (e.g. "Summer 2026", "Fall term") anywhere in the title or description. If in genuine doubt whether it's an internship vs. full-time, treat it as full-time and INELIGIBLE — do not give it the benefit of the doubt the way the APPLY_CAVEAT-vs-INELIGIBLE stack-fit rule below does.
  - **Exception: if the TITLE itself unambiguously says "Intern"/"Internship"/"Co-op",** trust the title. Small/less-sophisticated companies routinely copy-paste a generic full-time job template and just swap the title to "Intern" without updating the requirements section — a stray "Bachelor's degree required" or "proven experience as a Software Engineer" in the body text of a title-confirmed internship is a common template artifact, not real evidence of a full-time role. In that case, only actually INELIGIBLE for a genuinely hard, explicit signal: a stated years-of-experience *number* ("3+ years professional experience"), an explicit "full-time position" phrase, an annual six-figure salary, or explicit spanning-multiple-levels language. Vague "Bachelor's degree" / "proven experience" boilerplate alone, on a title-confirmed internship, is not enough to INELIGIBLE — treat it normally (APPLY/APPLY_CAVEAT per stack fit).
  - **Exception: paid STUDENT-WORKER roles count as internships even when the word "intern" never appears.** "Student Assistant", "Student Developer", "Student Programmer", "Part-Time Student — Software Engineer", "Student Software Engineer", "Student Position", "Work-Study", and "Student Technician" are how universities, research institutes, and several large employers label what is functionally an internship: paid technical work, restricted to currently-enrolled students, part-time during the term. Judge these on the work and the stack exactly as you would a titled internship. Confirmed live: a Georgia Tech Research Institute "Software Engineer Student Assistant" — paid, enrolled-students-only, Python and Java — was labelled INELIGIBLE for not being "a structured internship program with a defined term." That reasoning is wrong; the enrollment restriction *is* the internship framing, and GTRI is in his own city. John Deere's "Part-Time Student — Software Engineer" postings are the same pattern.
    - Two things still disqualify a student-worker role, same as any other: it must be **paid** (the unpaid rule below applies unchanged), and the work must be **software**. Watch for the near-miss where "student" names a department rather than the hire — "Software Engineer II - Student Affairs" is an ordinary full-time job in a university's Student Affairs office and is INELIGIBLE.
    - **Exception to the exception: legally-mandated pay-transparency salary boilerplate does NOT count as the "full-time position phrase" or "annual six-figure salary" trigger.** Large employers (Google, Meta, Amazon, Microsoft, and similar companies subject to CA/CO/NY/WA pay-transparency laws) are required to disclose a compensation range on every posting, including genuine internships, and reuse the same template paragraph verbatim regardless of employment type — e.g. "The US base salary range for **this full-time position** is $98,000–$131,000" or "In accordance with Washington state law, we are highlighting our comprehensive benefits package... available to all eligible US based Interns." Confirmed live: this exact pattern labelled INELIGIBLE a genuine 12-14-week paid Google SWE internship. Run this test: is the full-time/salary language confined to a standalone compensation-disclosure paragraph (recognizable by phrases like "in accordance with [state] law," "pay transparency," "base salary range," "individual pay is determined by," "does not include bonus, equity, or benefits") rather than a sentence actually describing your employment terms? If so, it's boilerplate — it does NOT trigger INELIGIBLE; evaluate the rest of the posting normally. Only treat full-time/salary language as a real signal when it appears in the actual role narrative describing what you're being hired as (e.g. "This is a full-time role, not an internship," or benefits explicitly NOT extended to interns) rather than inside a generic legally-required disclosure block.
    - **A $80K–$180K+ ANNUALIZED salary figure is normal, well-documented, and EXPECTED for a prestigious tech-company internship (Google, Meta, Amazon, Microsoft, Netflix, Apple, and similar) — this is public knowledge (see levels.fyi, Blind, and widely-reported intern compensation for these companies), not a red flag.** These companies pay interns roughly $8,000–$11,000+/month; a pay-transparency-mandated annualized figure in that range is exactly what a REAL internship at one of these companies looks like, not evidence of a misdirected full-time template. **Do not reason "this salary seems too high to be a real internship" as independent grounds for INELIGIBLE** — that intuition is factually wrong for this tier of employer and is exactly the mistake that caused the confirmed Google miss above. The genuine full-time tells to look for instead (none of which is "the number is large"): the posting lacks ANY duration/term language anywhere (no "N-week," no "Summer/Fall 20XX," no "returning to your degree program"), lists career-ladder/leveling language (L3/L4/IC5, "will report to a manager long-term"), or explicitly states the role continues indefinitely/has no end date. If the posting has a clear internship duration and program framing (as the Google posting did: "12-14 week paid internship," "Returning to a degree program after completion of the internship"), a large annualized salary alone never overrides that.
- **Unpaid internships** — label INELIGIBLE any posting that states the internship is unpaid, offers only college credit, or offers only a token stipend in place of wages. Look for "(Unpaid Internship)", "unpaid", "for college credit", "academic credit only", "volunteer". Confirmed live: an "AI & Digital Product Intern" posting whose header read `Type: Part-Time with Path to Full-Time  Experience: Current Student or Recent Graduate (Unpaid Internship)` was wrongly classified APPLY because the evaluation focused on stack fit (prompt engineering, Claude/ChatGPT APIs — a genuinely strong match) and never weighed the unpaid status. Strong stack fit does NOT override this; check it the same way you check whether the role is actually an internship.
- **Eligibility restrictions the candidate cannot satisfy** — INELIGIBLE when the posting limits applicants to a group he is not in, even when the role itself fits perfectly. The restriction is frequently in the TITLE rather than the body, so read the title for it explicitly: "Current Interns Only", "Returning Interns Only", "for current [Company] employees", "internal candidates only", military-transition programs (SkillBridge) requiring active-duty status, or programs requiring an already-held security clearance. Confirmed live: a Target "Current Interns Only- Technology Leadership Program" posting was wrongly promoted to APPLY because the description read like an ordinary strong SWE internship — the disqualifier appeared only in the title. He is a rising senior at Georgia State, not a current intern at any of these companies, so these are hard INELIGIBLEs.
- **New grad / full-time roles** — any listing with "New Grad", "New Graduate", "New College Grad", "College Grad", "NCG", "University Grad", "Recent Grad", or "Recent Graduate" **anywhere in the title or description** (not title-only). These are full-time hires, not internships.
- Quant / trading / HFT
- **ML Research / Applied Scientist / Computer Vision / Deep Learning research** (these want PyTorch, model training, foundation models, a PhD/MS — NOT my lane). Note: "AI Engineer building with LLM APIs" is 🟢, but "ML/CV researcher training models" is INELIGIBLE. A "Performance Engineer"/"Systems Software"/"Infrastructure" title that benchmarks or builds tooling around ML workloads (not designing/training the models themselves) is engineering, not research — check the required quals and the actual verbs (benchmarking/profiling/tooling vs. designing/training/publishing) before defaulting to INELIGIBLE just because ML vocabulary appears.
- Hardware, Embedded, Firmware, FPGA, ASIC, RTL, Verification — i.e. the role is **electrical/hardware engineering**, not software written in an unfamiliar language. The test is what the intern actually builds: schematics, boards, HDL, silicon, or bring-up on a bench is out; code is not, whatever language it is in.
  - **"Flight Software" and "Rendering" were removed from this list on purpose.** Both are software engineering — safety-critical embedded software and graphics programming respectively — and both were only ever here because they are C++-heavy. Under the no-language-INELIGIBLE rule above they are APPLY_CAVEAT, and an aerospace or graphics internship at a real company is a genuinely good opportunity worth surfacing.
- Electrical/Mechanical or other non-CS engineering
- **Requires a security clearance I must ALREADY HOLD at time of application** — "must currently hold an active Secret/TS/SCI", "active clearance required", "existing TS/SCI with polygraph". Those I cannot satisfy today.
  - **🟢 But "able to obtain" is an APPLY, not a INELIGIBLE — this distinction is worth more to me than any other rule here.** "Must be able to obtain a Secret clearance", "must be eligible for a clearance", "clearance sponsorship available", "we will sponsor the clearance process", "must be clearable" all describe a process the employer runs *after* hiring, and the eligibility requirement behind it is **U.S. citizenship — which I have** (see Work authorization above). Being sponsorable is a qualification I meet, not a barrier.
  - Do **not** reason that "sponsorship is not a substitute for the eligibility requirement itself" — that is circular, and it was the exact wording that wrongly labelled INELIGIBLE a Leidos internship. The eligibility requirement *is* citizenship, and I satisfy it. The only question is whether the posting demands the clearance already be **in hand**.
  - This matters because it governs a large share of the SWE internship market — Leidos, Northrop Grumman, Raytheon, General Dynamics, BAE, Booz Allen, MITRE, Lockheed — and a disproportionate share of the Atlanta-area ones (GTRI, Lockheed Marietta). Skipping the sponsored ones silently removes most defense-sector internships from my feed.
  - Same logic for **Public Trust**, **suitability determinations**, and **background investigations**: these are post-hire processes, never a reason to INELIGIBLE.
- Roles where an **advanced degree (MS/PhD) is explicitly required** — not just preferred
- Roles where **2+ years of professional experience is a hard requirement** (not a guideline or preference) — a real mid/senior-level hire is not an internship regardless of the title. If years of experience are listed as "preferred" or "a plus," that is NOT a hard requirement — treat those normally.
- Non-US location with no US-remote option
- ~~A role whose language I don't know~~ — **deleted deliberately. There is no longer any language-based INELIGIBLE for an internship. See the APPLY_CAVEAT section.**
- **Staffing/placement agencies with no named end client** — if the hiring company is clearly a staffing, placement, or body-shop recruiter (name contains "Staffing", "Placement", "Recruiting", "HireX", "Staff Solutions", "Outsourcing", etc.) AND the job description does not name the actual end-client company, INELIGIBLE. These are not real internships.

---

## Honesty filters (do not surface roles I'd have to outright lie to qualify for)
- **Java/C#/.NET/C++ as a core requirement, alongside real overlap elsewhere (Python/JS/TS present, or application-layer work):** APPLY (not APPLY_CAVEAT/INELIGIBLE) — I can credibly pitch "I learn languages fast, I shipped iOS in Swift, web in Node, Python backend; I'll ramp on Java/C#/C++ on the job." This is a legitimate intern pitch and companies hire interns to teach them.
- **Pure Rust / pure Go / pure C++ systems/embedded** with zero JS/Python overlap: INELIGIBLE — these require deep systems programming I cannot defend.
- No **computer vision / ML model training** roles (PyTorch, CLIP, SAM, object detection, segmentation) — application-layer LLM work only.
- No roles where the SOLE core requirement is something I genuinely cannot defend at all AND there is no overlap with my stack.

## Resume to suggest (exactly 4 variants exist — pick the one that best fits
## the actual responsibilities/stack described in the JD, not just the title)
- **Mobile** — the role is meaningfully iOS/Android/React Native work: building or maintaining a mobile app, SwiftUI/UIKit, mobile-specific architecture, App Store shipping.
- **AI** — the role centers on building WITH LLMs/agents: prompt engineering, tool-calling, RAG, agentic workflows, integrating Anthropic/OpenAI APIs into a product. (Not ML research/model training — those are INELIGIBLE entirely, see above.)
- **Frontend** — the role is primarily UI/web-frontend: React, Next.js, Vite, component/design-system work, little-to-no backend ownership.
- **General** — everything else: full-stack, backend/API, Python/data, DevOps, or any role that's a genuine mix without one clearly dominant lane above. This is the default when in doubt.
Read the actual JD — a title like "Software Engineer Intern" at an AI company can still be **AI** if the day-to-day is building agents, and a "Full Stack" role that's 80% React work can still be **Frontend**.

## Class-year note
Most internships accept rising juniors AND seniors. A few pre-internships target rising juniors only — surface them but flag "check class-year eligibility."
