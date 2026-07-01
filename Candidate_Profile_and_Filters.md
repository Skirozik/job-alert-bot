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
- **OK:** ANY US city — willing to relocate anywhere in the US for an internship term (semester or summer)
- **Exclude:** non-US roles unless explicitly US-remote eligible

---

## TRIAGE RUBRIC (the bot applies this to every listing)

### 🟢 APPLY — surface immediately
- Title contains: Software Engineer/Developer Intern, iOS, Mobile, Swift, Full-Stack, Frontend, Backend, Web, React, Node, Next.js, Python, FastAPI, AI Engineer, Applied AI, AI-application, SWE Intern
- Stack overlap with any of: React, Next.js, Vite, Node.js, Python, FastAPI, Flask, Swift, TypeScript, JavaScript, PostgreSQL, Docker, Redis, Prisma, Anthropic/OpenAI APIs
- US location (incl. Remote-US) or anywhere I'd relocate (all US cities qualify)
- No advanced-degree requirement

### 🟡 MAYBE — surface, lower priority
- Data Engineering, general "Software" intern at a non-tech company, DevOps/Cloud, QA/Test, startup generalist
- Asks for ONE adjacent language I don't have as a primary (Java, C#, Go, Kotlin) **but** the role has meaningful overlap with my stack (JS/Python/TS also accepted, or the work itself is web/backend/AI-application layer) — my "I shipped iOS in Swift, web in Node, tools in Python, I pick up languages fast" story covers this in interviews. Internships teach you the stack; do not SKIP these.
- An Android/Kotlin mobile role (adjacent to my iOS strength — flag as a stretch)
- A role at a strong company where I'd be a stretch candidate but the domain is interesting and not a hard mismatch
- **When in doubt between MAYBE and SKIP for an internship at a real company, always choose MAYBE.** It is always better to surface a stretch opportunity than to silently miss it.

### 🔴 SKIP — never surface
- **New grad / full-time roles** — any listing with "New Grad", "New Graduate", "New College Grad", "College Grad", "NCG", "University Grad", "Recent Grad", or "Recent Graduate" in the title. These are full-time hires, not internships.
- Quant / trading / HFT
- **ML Research / Applied Scientist / Computer Vision / Deep Learning research** (these want PyTorch, model training, foundation models, a PhD/MS — NOT my lane). Note: "AI Engineer building with LLM APIs" is 🟢, but "ML/CV researcher training models" is 🔴.
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
- **Java/C#/.NET as a core requirement:** MAYBE (not SKIP) — I can credibly pitch "I learn languages fast, I shipped iOS in Swift, web in Node, Python backend; I'll ramp on Java/C# on the job." This is a legitimate intern pitch and companies hire interns to teach them.
- **Pure Rust / pure Go / pure C++ systems/embedded** with zero JS/Python overlap: SKIP — these require deep systems programming I cannot defend.
- No **computer vision / ML model training** roles (PyTorch, CLIP, SAM, object detection, segmentation) — application-layer LLM work only.
- No roles where the SOLE core requirement is something I genuinely cannot defend at all AND there is no overlap with my stack.

## Resume to suggest per tier (the classifier can hint this)
- iOS/Mobile role → **Mobile** resume
- AI/agentic/LLM role → **AI** resume
- Frontend/React/Vite role → **Frontend** variant
- Security/dev-tooling → **1Password** variant
- Everything else → **General** resume

## Class-year note
Most internships accept rising juniors AND seniors. A few pre-internships target rising juniors only — surface them but flag "check class-year eligibility."
