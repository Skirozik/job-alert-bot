# Candidate Profile + Filter Rules (for the job-alert bot's classifier)
*This is what the bot uses to decide APPLY / MAYBE / SKIP. Feed it to the Claude classifier as context for every listing.*

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
- **OK:** ANY US city — willing to relocate anywhere in the US for an internship term (semester or summer). This is a genuine "OK," not a soft negative: an onsite requirement in a non-Atlanta US city is NOT a reason to cite when explaining a MAYBE instead of APPLY, and must not be weighed alongside other factors as a partial strike against the role. Only non-US-without-remote (see Exclude below) is a real location concern.
- **Exclude:** non-US roles unless explicitly US-remote eligible

---

## TRIAGE RUBRIC (the bot applies this to every listing)

### 🟢 APPLY — surface immediately
- Title contains: Software Engineer/Developer Intern, iOS, Mobile, Swift, Full-Stack, Frontend, Backend, Web, React, Node, Next.js, Python, FastAPI, AI Engineer, Applied AI, AI-application, SWE Intern
- Stack overlap with any of: React, Next.js, Vite, Node.js, Python, FastAPI, Flask, Swift, TypeScript, JavaScript, PostgreSQL, Docker, Redis, Prisma, Anthropic/OpenAI APIs
- US location (incl. Remote-US) or anywhere I'd relocate (all US cities qualify)
- No advanced-degree requirement
- **A genuine internship (real term/duration, real company) that asks for ONE adjacent language I don't have as a primary (C++, Java, C#, Go, Kotlin, etc.) but has meaningful overlap with my stack (Python/JS/TS present, or the work is application-layer engineering, not deep systems programming) — this is APPLY, not a hedge.** "I shipped iOS in Swift, web in Node, tools in Python, I pick up languages fast" is a strong, credible pitch, and internships exist specifically to teach the stack. Reserve MAYBE for roles where the overlap is genuinely thin (see below) — one non-primary required language alongside real overlap is not thin.
- **A strong-company internship in an unfamiliar PRODUCT domain (AV/audio, automotive, energy, fintech ops, water/climate tech, manufacturing, biotech, etc.) where the actual day-to-day work is still real software engineering** (APIs, services, modern practices, mentorship) — domain novelty alone doesn't make this a stretch. Only treat domain as a real concern if the role itself is non-software (e.g. hardware/RF/mechanical engineering — see SKIP). **Run this exact test: read the "What You'll Do"/responsibilities section specifically — is it describing software verbs (build, write, design, debug, integrate, maintain, test, ship) applied to tools, dashboards, APIs, backend services, or data pipelines? If yes, this is a real software engineering role, full stop, regardless of what the COMPANY manufactures or sells (physical hardware, consumer devices, industrial equipment, biotech, etc.) — a hardware/manufacturing company's internal tooling and backend/infra work is still software engineering. Do not let "the company builds a physical product" pull the decision toward MAYBE or SKIP when the actual described responsibilities are software work; that reasoning is only valid if the ROLE's own responsibilities are hardware/mechanical/RF engineering, not just the company's industry.**
- **A company-wide rotational/track-based internship program** (e.g. a "Technology Program" spanning SWE/AI/Data/etc. tracks) where I meet the stated eligibility — apply confidently even if the specific team placement isn't guaranteed yet; the downside case is still landing in a real tech track at a real company.
- **An "AI Intern" / applied-AI role with Python (or similar) overlap**, even if it also touches ML model evaluation/optimization as part of applied engineering work, or is built on a specific cloud ecosystem (Microsoft/AWS/etc.) — still application-layer AI work, not the ML-research SKIP category below, unless the role explicitly centers on training or researching novel models (see SKIP). This also applies to Performance/Systems/Infrastructure/Platform Engineer roles that build tooling, benchmarks, or dashboards AROUND ML workloads (profiling, validating, benchmarking, orchestration) — that's applied/infra engineering, APPLY, even if the description uses "deep learning," "PyTorch," or "model" language, as long as the title doesn't say Researcher/Scientist and the required quals (not the "nice to have" list) don't demand ML research skills. **Run this exact test before invoking the ML-research SKIP category on one of these titles: does the "Required"/"Minimum Qualifications" section — NOT the "nice to have"/"preferred"/"stand out" section — demand ML research skills (PyTorch, model training, etc.)? If PyTorch/deep-learning/model-training language appears ONLY in a nice-to-have/preferred/stand-out list, it does not count as a research signal at all for this test, full stop — do not let it pull the decision toward SKIP or even MAYBE. A Performance/Systems/Infrastructure/Platform-titled internship whose required quals are ordinary CS-fundamentals language (a degree in progress, a general-purpose language, Linux/git, "foundational interest" in ML) is APPLY, regardless of how much ML vocabulary shows up in the "what you'll be doing" or "stand out" sections.**

### 🟡 MAYBE — surface, lower priority
- Data Engineering, general "Software" intern at a non-tech company, DevOps/Cloud, QA/Test, startup generalist — where the actual overlap with my stack is thin (not just "one extra language required" — see APPLY above for that case)
- An Android/Kotlin mobile role (adjacent to my iOS strength but no other overlap — flag as a stretch)
- A role at a strong company where the fit is genuinely uncertain (not just an unfamiliar domain with real SWE work — see APPLY above — but where it's unclear the role is even software engineering, or overlap is minimal)
- **When in doubt between MAYBE and SKIP for an internship at a real company, always choose MAYBE.** It is always better to surface a stretch opportunity than to silently miss it. But when in doubt between APPLY and MAYBE for a genuine internship with real (even partial) stack overlap and no hard disqualifier, default to APPLY — MAYBE is for weak/uncertain fits, not merely "not a perfect match."

### 🔴 SKIP — never surface
- **Not actually an internship — check this FIRST, before evaluating stack fit.** A strong skill/stack match does NOT override this; many full-time roles will look like a "perfect fit" on paper, and that is exactly the trap to avoid. SKIP if the description signals a full-time hire for graduates, e.g.: it says the role spans multiple experience levels ("new grads through senior/staff", "all levels", "entry-level to senior"), it states an annual salary/compensation range typical of full-time employment (e.g. "$130K–$240K", "Compensation Range: $X"), it lists full-time-employee benefits (health insurance, 401k, unlimited PTO, equity), or the posting never uses "intern", "internship", "co-op", or names a specific term/duration (e.g. "Summer 2026", "Fall term") anywhere in the title or description. If in genuine doubt whether it's an internship vs. full-time, treat it as full-time and SKIP — do not give it the benefit of the doubt the way the MAYBE-vs-SKIP stack-fit rule below does.
  - **Exception: if the TITLE itself unambiguously says "Intern"/"Internship"/"Co-op",** trust the title. Small/less-sophisticated companies routinely copy-paste a generic full-time job template and just swap the title to "Intern" without updating the requirements section — a stray "Bachelor's degree required" or "proven experience as a Software Engineer" in the body text of a title-confirmed internship is a common template artifact, not real evidence of a full-time role. In that case, only actually SKIP for a genuinely hard, explicit signal: a stated years-of-experience *number* ("3+ years professional experience"), an explicit "full-time position" phrase, an annual six-figure salary, or explicit spanning-multiple-levels language. Vague "Bachelor's degree" / "proven experience" boilerplate alone, on a title-confirmed internship, is not enough to SKIP — treat it normally (APPLY/MAYBE per stack fit).
- **New grad / full-time roles** — any listing with "New Grad", "New Graduate", "New College Grad", "College Grad", "NCG", "University Grad", "Recent Grad", or "Recent Graduate" **anywhere in the title or description** (not title-only). These are full-time hires, not internships.
- Quant / trading / HFT
- **ML Research / Applied Scientist / Computer Vision / Deep Learning research** (these want PyTorch, model training, foundation models, a PhD/MS — NOT my lane). Note: "AI Engineer building with LLM APIs" is 🟢, but "ML/CV researcher training models" is 🔴. A "Performance Engineer"/"Systems Software"/"Infrastructure" title that benchmarks or builds tooling around ML workloads (not designing/training the models themselves) is engineering, not research — check the required quals and the actual verbs (benchmarking/profiling/tooling vs. designing/training/publishing) before defaulting to SKIP just because ML vocabulary appears.
- Hardware, Embedded, Firmware, FPGA, ASIC, RTL, Verification, Flight Software, Rendering
- Electrical/Mechanical or other non-CS engineering
- Requires a U.S. security clearance I don't currently hold
- Roles where an **advanced degree (MS/PhD) is explicitly required** — not just preferred
- Roles where **2+ years of professional experience is a hard requirement** (not a guideline or preference) — a real mid/senior-level hire is not an internship regardless of the title. If years of experience are listed as "preferred" or "a plus," that is NOT a hard requirement — treat those normally.
- Non-US location with no US-remote option
- A role where the ONLY language is one I have zero overlap with AND there is no other meaningful skill match (e.g. pure Rust systems, pure C++ embedded, pure COBOL). If there is ANY overlap with my stack, prefer MAYBE over SKIP.
- **Staffing/placement agencies with no named end client** — if the hiring company is clearly a staffing, placement, or body-shop recruiter (name contains "Staffing", "Placement", "Recruiting", "HireX", "Staff Solutions", "Outsourcing", etc.) AND the job description does not name the actual end-client company, SKIP. These are not real internships.

---

## Honesty filters (do not surface roles I'd have to outright lie to qualify for)
- **Java/C#/.NET/C++ as a core requirement, alongside real overlap elsewhere (Python/JS/TS present, or application-layer work):** APPLY (not MAYBE/SKIP) — I can credibly pitch "I learn languages fast, I shipped iOS in Swift, web in Node, Python backend; I'll ramp on Java/C#/C++ on the job." This is a legitimate intern pitch and companies hire interns to teach them.
- **Pure Rust / pure Go / pure C++ systems/embedded** with zero JS/Python overlap: SKIP — these require deep systems programming I cannot defend.
- No **computer vision / ML model training** roles (PyTorch, CLIP, SAM, object detection, segmentation) — application-layer LLM work only.
- No roles where the SOLE core requirement is something I genuinely cannot defend at all AND there is no overlap with my stack.

## Resume to suggest (exactly 4 variants exist — pick the one that best fits
## the actual responsibilities/stack described in the JD, not just the title)
- **Mobile** — the role is meaningfully iOS/Android/React Native work: building or maintaining a mobile app, SwiftUI/UIKit, mobile-specific architecture, App Store shipping.
- **AI** — the role centers on building WITH LLMs/agents: prompt engineering, tool-calling, RAG, agentic workflows, integrating Anthropic/OpenAI APIs into a product. (Not ML research/model training — those are SKIP entirely, see above.)
- **Frontend** — the role is primarily UI/web-frontend: React, Next.js, Vite, component/design-system work, little-to-no backend ownership.
- **General** — everything else: full-stack, backend/API, Python/data, DevOps, or any role that's a genuine mix without one clearly dominant lane above. This is the default when in doubt.
Read the actual JD — a title like "Software Engineer Intern" at an AI company can still be **AI** if the day-to-day is building agents, and a "Full Stack" role that's 80% React work can still be **Frontend**.

## Class-year note
Most internships accept rising juniors AND seniors. A few pre-internships target rising juniors only — surface them but flag "check class-year eligibility."
