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
- **Frontend/Mobile:** SwiftUI, React, Next.js, React Native, Tailwind
- **Backend/Data:** Node.js, Express, Flask, PostgreSQL, Redis, Firebase, Cloudflare Workers
- **AI:** Anthropic Claude + OpenAI APIs, agentic/tool-calling systems, prompt engineering (application layer, NOT ML research)
- **Other:** REST/GraphQL, Stripe, Git, Stripe + blockchain payments

## Target areas (priority order)
1. **iOS / Mobile** (strongest — shipped App Store app)
2. **Full-stack / Web** (React, Next.js, Node, Python)
3. **Python / Backend**
4. **AI-application / AI Engineer** (building WITH LLM APIs and agents)
5. **General SWE Intern** at any tech-forward company

## Location rules
- **Prefer:** Atlanta GA (local), Remote (US)
- **OK:** any US city I'd relocate to for a summer/fall term
- **Exclude:** non-US roles unless explicitly US-remote eligible

---

## TRIAGE RUBRIC (the bot applies this to every listing)

### 🟢 APPLY — surface immediately
- Title contains: Software Engineer/Developer Intern, iOS, Mobile, Swift, Full-Stack, Frontend, Backend, Web, React, Node, Next.js, Python, AI Engineer, Applied AI, AI-application, SWE Intern
- US location (incl. Remote-US) or somewhere I'd relocate
- No advanced-degree requirement

### 🟡 MAYBE — surface, lower priority
- Data Engineering, general "Software" intern at a non-tech company, DevOps/Cloud, QA/Test, startup generalist
- Asks for ONE adjacent language I don't have (Java, C#, Go, Kotlin) — my "I shipped iOS in Swift, web in Node, tools in Python, I pick up languages fast" story covers it
- An Android/Kotlin mobile role (adjacent to my iOS strength — flag as a stretch)

### 🔴 SKIP — never surface
- Quant / trading / HFT
- **ML Research / Applied Scientist / Computer Vision / Deep Learning research** (these want PyTorch, model training, foundation models, a PhD/MS — NOT my lane). Note: "AI Engineer building with LLM APIs" is 🟢, but "ML/CV researcher training models" is 🔴.
- Hardware, Embedded, Firmware, FPGA, ASIC, RTL, Verification, Flight Software (C++), Rendering
- Electrical/Mechanical or other non-CS engineering
- Anything requiring an advanced degree (MS/PhD), or 2-3+ years of professional experience as a hard requirement (intern postings that tack this on are usually fine, but a real senior/mid role is a SKIP)
- Requires a U.S. security clearance I don't have
- Non-US location with no US-remote option

---

## Honesty filters (do not surface roles I'd have to lie to qualify for)
- No roles centered on **Rust, Go, C#/.NET, AutoCAD/CAD/LISP** as core requirements (I don't have these; C# only as "familiarity").
- No **computer vision / ML model training** roles (PyTorch, CLIP, SAM, object detection, segmentation) — application-layer LLM work only.
- No roles where the core requirement is something I can't defend in an interview.

## Resume to suggest per tier (the classifier can hint this)
- iOS/Mobile role → **Mobile** resume
- AI/agentic/LLM role → **AI** resume
- Frontend/React role → **Frontend** variant
- Security/dev-tooling → **1Password** variant
- Everything else → **General** resume

## Class-year note
Most internships accept rising juniors AND seniors. A few pre-internships target rising juniors only — surface them but flag "check class-year eligibility."
