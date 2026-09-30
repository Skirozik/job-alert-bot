"""classifier.py: the classify_job tool text, the breaker, the overrides, and what never reaches the log.

Offline: the Anthropic client is a stub that records each create() call, the
profile is a placeholder string, and time.sleep is recorded instead of slept.

Run:  cd scraper_brice && python -X utf8 test_classifier_tool.py
"""

import testkit

testkit.block_network()

import ast  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402

import anthropic  # noqa: E402

import classifier  # noqa: E402
from testkit import captured_logs, check, patched, sdk_error, section  # noqa: E402

REAL_GET_CLIENT = classifier._get_client      # the tests below replace it with stubs
TOOL = classifier._CLASSIFY_TOOL
PROPS = TOOL["input_schema"]["properties"]
TIER_TEXT = PROPS["tier"]["description"]
REASON_TEXT = PROPS["reason"]["description"]

section("the classify_job tool")
check("tier enum is exactly APPLY / APPLY_CAVEAT / INELIGIBLE",
      PROPS["tier"]["enum"] == ["APPLY", "APPLY_CAVEAT", "INELIGIBLE"])
check("PENDING is not a verdict the model can return", "PENDING" not in classifier._VALID_TIERS)
check("required fields are tier and reason", TOOL["input_schema"]["required"] == ["tier", "reason"])
check("no suggested_resume (one resume) and no strict flag",
      "suggested_resume" not in PROPS and "strict" not in TOOL)
check("tier text points to the rubric's numbered list ('INELIGIBLE — the complete list', I-1)",
      "INELIGIBLE — the complete list" in TIER_TEXT and "I-1" in TIER_TEXT)
check("tier text says the list is exhaustive and judgment calls are APPLY_CAVEAT",
      "exhaustive" in TIER_TEXT and "judgment call" in TIER_TEXT)
check("tier text calls full-time / new-grad / entry-level the target, never a block",
      "never a block in themselves" in TIER_TEXT and "new-grad" in TIER_TEXT)
BANNED = ["internship", "new grad or full-time", "US citizen", "he is", "sponsorship"]
present = [w for w in BANNED if w.lower() in TIER_TEXT.lower()]
check("tier text names no candidate facts and no internship rule", not present, str(present))
present = [w for w in BANNED if w.lower() in REASON_TEXT.lower()]
check("reason text names none of them either", not present, str(present))
check("no GPA example leaks into the reason text (main's tool has one)", "gpa" not in REASON_TEXT.lower())

main_src = (testkit.REPO / "scraper" / "classifier.py").read_text(encoding="utf-8")
m = re.search(r"GROUNDING RULE — .*?even when the tier is right\.", main_src)
check("main's grounding sentence was found in ../scraper/classifier.py", bool(m))
check("the reason text carries main's grounding sentence verbatim", bool(m) and m.group(0) in REASON_TEXT)
check("INELIGIBLE reasons start with the condition label ('I-1:')", "(for example 'I-1:')" in REASON_TEXT)

section("the request")
calls = []


def stub_client(respond):
    def create(**kwargs):
        calls.append(kwargs)
        return respond(kwargs)
    return types.SimpleNamespace(messages=types.SimpleNamespace(create=create))


def tool_reply(**tool_input):
    return lambda _kw: types.SimpleNamespace(content=[types.SimpleNamespace(type="tool_use", input=dict(tool_input))])


slept = []
classifier.time = types.SimpleNamespace(sleep=slept.append)
classifier._profile = "PROFILE PLACEHOLDER"


def use(respond):
    calls.clear()
    slept.clear()
    classifier._API_HARD_DOWN = None
    client = stub_client(respond)
    classifier._get_client = lambda: client


JOB = {"id": "li:1", "title": "Associate Sales Engineer", "company": "Initech", "location": "Remote - US",
       "description": "x" * 600}

use(tool_reply(tier="APPLY", reason="SECRET-REASON", salary=""))
res = classifier.classify(dict(JOB))
kw = calls[0] if calls else {}
check("a tool_use reply comes back as the result", res.get("tier") == "APPLY" and not res.get("failed"))
check("Haiku 4.5, max_tokens 400, temperature 0",
      kw.get("model") == "claude-haiku-4-5-20251001" and kw.get("max_tokens") == 400 and kw.get("temperature") == 0)
check("the verdict is forced through classify_job",
      kw.get("tool_choice") == {"type": "tool", "name": "classify_job"} and kw.get("tools") == [TOOL])
sysblock = (kw.get("system") or [{}])[0]
check("the rubric is a cached system prompt block",
      sysblock.get("cache_control") == {"type": "ephemeral"}
      and sysblock.get("text", "").startswith("You evaluate job postings for a specific candidate.")
      and sysblock.get("text", "").endswith("PROFILE PLACEHOLDER"))
use(tool_reply(tier="APPLY_CAVEAT", reason="r"))
classifier.classify({**JOB, "description": None})
check("a missing description sends the rubric's title-only placeholder",
      "(not available — classify on title/company/location only)" in calls[0]["messages"][0]["content"])

section("tier validation and malformed replies")
use(tool_reply(tier="MAYBE", reason="r"))
check("an unknown tier becomes APPLY_CAVEAT", classifier.classify(dict(JOB)).get("tier") == "APPLY_CAVEAT")
use(lambda _kw: types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="hi")]))
res = classifier.classify(dict(JOB))
check("no tool_use block -> failed, kind 'malformed', after one call",
      res.get("failed") is True and res.get("failed_kind") == "malformed" and len(calls) == 1)

section("the breaker: billing/auth cost ONE call per run")


def raising(exc):
    def respond(_kw):
        raise exc
    return respond


use(raising(sdk_error(anthropic.BadRequestError, "Your credit balance is too low to access the Anthropic API")))
res = classifier.classify(dict(JOB))
check("billing -> failed, kind 'billing'", res.get("failed") is True and res.get("failed_kind") == "billing")
check("...after exactly one call and no backoff sleep", len(calls) == 1 and slept == [], f"{len(calls)} {slept}")
res2 = classifier.classify({**JOB, "id": "li:2"})
check("the next job returns failed without calling the API",
      len(calls) == 1 and res2.get("failed") is True and res2.get("failed_kind") == "billing")
use(raising(sdk_error(anthropic.AuthenticationError, "invalid x-api-key")))
check("auth -> kind 'auth'", classifier.classify(dict(JOB)).get("failed_kind") == "auth")
use(raising(sdk_error(anthropic.PermissionDeniedError, "no access to model")))
check("permission denied -> kind 'auth'", classifier.classify(dict(JOB)).get("failed_kind") == "auth")
use(raising(sdk_error(anthropic.RateLimitError, "slow down")))
res = classifier.classify(dict(JOB))
check("transient -> 3 attempts with 2 backoff sleeps, kind 'transient'",
      len(calls) == 3 and len(slept) == 2 and res.get("failed_kind") == "transient", f"{len(calls)} {slept}")
check("...and a transient error does not trip the breaker", classifier._API_HARD_DOWN is None)
use(raising(sdk_error(anthropic.BadRequestError, "max_tokens must be positive")))
check("a 400 that is not about credit is transient", classifier.classify(dict(JOB)).get("failed_kind") == "transient")
check("_error_kind: plain exception is transient", classifier._error_kind(RuntimeError("reset")) == "transient")
check("_failed defaults to transient and carries no suggested_resume",
      classifier._failed("x") == {"failed": True, "failed_kind": "transient", "tier": "APPLY_CAVEAT",
                                  "reason": "Classifier error — parked for retry (x)"})

main_ek = re.search(r"def _error_kind\(exc: Exception\) -> str:.*?\n    return \"transient\"\n", main_src, re.S)
fork_ek = re.search(r"def _error_kind\(exc: Exception\) -> str:.*?\n    return \"transient\"\n",
                    (testkit.HERE / "classifier.py").read_text(encoding="utf-8"), re.S)
check("_error_kind is byte-identical to scraper/classifier.py's", bool(main_ek and fork_ek)
      and main_ek.group(0) == fork_ek.group(0))

section("override: non-US (deterministic)")


def verdict(location, tier="APPLY", description="d" * 600):
    use(tool_reply(tier=tier, reason="model reason"))
    return classifier.classify({**JOB, "location": location, "description": description})


r = verdict("Toronto, ON, Canada")
check("'Toronto, ON, Canada' -> INELIGIBLE", r["tier"] == "INELIGIBLE")
check("...with a neutral reason that names the place and no candidate facts",
      r["reason"] == "Overridden: based in Toronto with no US or US-remote option stated.", r["reason"])
check("'Dublin, OH' is left alone (a US state code wins)", verdict("Dublin, OH")["tier"] == "APPLY")
check("'Remote - US' is left alone", verdict("Remote - US")["tier"] == "APPLY")
check("'New York, NY / London, UK' is left alone (any US site)", verdict("New York, NY / London, UK")["tier"] == "APPLY")
check("'Hyderabad - Phoenix Equinox Tower 2' -> INELIGIBLE (the one foreign place source_gate lets through)",
      verdict("Hyderabad - Phoenix Equinox Tower 2")["tier"] == "INELIGIBLE")
check("an INELIGIBLE verdict is not touched", verdict("London, UK", tier="INELIGIBLE")["reason"] == "model reason")


def assignments(src, names):
    out = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names:
            out[node.targets[0].id] = ast.get_source_segment(src, node)
    return out


REGEXES = ("_US_STATE_RE", "_US_WORD_RE", "_FOREIGN_RE")
fork_src = (testkit.HERE / "classifier.py").read_text(encoding="utf-8")
check("the three location regexes are byte-identical to scraper/classifier.py's",
      assignments(fork_src, REGEXES) == assignments(main_src, REGEXES) and len(assignments(fork_src, REGEXES)) == 3)

section("override: non-US -- U.S. places spelled out (this fork's boards and lists)")
SPELLED_US = ["Vienna, Virginia", "Dublin, Ohio", "Cambridge, Massachusetts", "Albuquerque, New Mexico", "Paris, Texas",
              "London, Kentucky", "Manchester, New Hampshire", "Melbourne, Florida", "Waterloo, Iowa",
              "Hybrid - San Francisco, New York City, London, Berlin", "Hybrid - San Francisco, London, Berlin",
              "3627 Denmark Dr Ste 200, Council Bluffs, Iowa", "9935 Coors BLVD NW, Albuquerque, New Mexico",
              "Bellevue, Washington; Chicago, Illinois; Toronto, Ontario, Canada", "Chicago, New York, London"]
wrong = [loc for loc in SPELLED_US if verdict(loc)["tier"] != "APPLY"]
check(f"{len(SPELLED_US) - len(wrong)}/{len(SPELLED_US)} U.S. locations the old test forced INELIGIBLE keep their tier",
      not wrong, str(wrong))
STILL_FOREIGN = ["Hyderabad - Phoenix Equinox Tower 2", "Toronto, ON, Canada", "Washington, England, United Kingdom",
                 "Tijuana, Baja California, Mexico", "Remote - Mexico", "London", "Ariana, Ariana, Tunisia",
                 "Kuwait City, Kuwait", "Quito, Ecuador", "Remote - United Arab Emirates"]
wrong = [loc for loc in STILL_FOREIGN if verdict(loc)["tier"] != "INELIGIBLE"]
check(f"{len(STILL_FOREIGN) - len(wrong)}/{len(STILL_FOREIGN)} foreign locations are still INELIGIBLE "
      "(a building named after a U.S. city is not one; this fork's extra countries count)", not wrong, str(wrong))
check("...naming the place from the extra list", verdict("Ariana, Ariana, Tunisia")["reason"]
      == "Overridden: based in Tunisia with no US or US-remote option stated.")

section("override: family net -- I-5 on an in-family title becomes APPLY_CAVEAT")
JUMP = {"id": "ats:jump", "title": "Campus Systems Engineer (Full-Time)", "company": "Jump Trading",
        "location": "Chicago", "description": "d" * 600}


def net(title, reason, tier="INELIGIBLE", company="Jump Trading", location="Chicago"):
    use(tool_reply(tier=tier, reason=reason))
    return classifier.classify({**JUMP, "title": title, "company": company, "location": location})


r = net("Campus Systems Engineer (Full-Time)", "I-5: software development role; requires Python and shell scripting.")
check("Jump's 'Campus Systems Engineer' flagged I-5 -> APPLY_CAVEAT with the verify reason",
      r["tier"] == "APPLY_CAVEAT" and r["reason"] == classifier.FAMILY_NET_REASON, str(r))
check("...a caveat under 12 words (the rubric's APPLY_CAVEAT rule)", len(classifier.FAMILY_NET_REASON.split()) < 12)
check("'I-5' wrapped in markdown still counts", net("Associate Network Engineer", "**I-5**: not IT")["tier"] == "APPLY_CAVEAT")
KEEP_INELIGIBLE = [
    ("IT Associate Software Engineer (Hybrid)", "I-5: software development role.", "a software role noun"),
    ("Associate Construction Engineer - Power Infrastructure", "I-5: construction engineering.", "no target family"),
    ("Help Desk Analyst", "I-5: help desk.", "the help-desk subfamily (owner floor)"),
    ("Associate Network Engineer", "I-1: requires 3+ years.", "another numbered block"),
    ("Associate Network Engineer", "Software development role (I-5).", "a reason that does not start with I-5"),
]
for title, reason, why in KEEP_INELIGIBLE:
    r = net(title, reason)
    check(f"stays INELIGIBLE: {why} ({title!r})", r["tier"] == "INELIGIBLE" and r["reason"] == reason, str(r))
check("an APPLY is never touched by the net", net("Help Desk Analyst", "fit", tier="APPLY")["tier"] == "APPLY")
r = net("Campus Systems Engineer (Full-Time)", "I-5: software development role.", location="London, UK")
check("runs before the non-US override: a foreign I-5 in-family title still ends INELIGIBLE (non-US)",
      r["tier"] == "INELIGIBLE" and r["reason"].startswith("Overridden: based in"), str(r))

section("the client: an explicit per-request timeout")
made = []


class RecordingAnthropic:
    def __init__(self, **kwargs):
        made.append(kwargs)


with patched(classifier, anthropic=types.SimpleNamespace(Anthropic=RecordingAnthropic),
             ANTHROPIC_API_KEY="key-under-test", _client=None):
    REAL_GET_CLIENT()
check("the Anthropic client is built with timeout=60 s (the SDK default is 600 s per attempt)",
      made == [{"api_key": "key-under-test", "timeout": 60.0}], str([sorted(k) for k in made]))

section("override: title-only cap (runs last)")
r = verdict("Remote - US", description=None)
check("APPLY without a description -> APPLY_CAVEAT", r["tier"] == "APPLY_CAVEAT")
check("...with the title-only reason", r["reason"] == "Title-only: no description available — check the posting")
check("APPLY with 150 chars of description -> APPLY_CAVEAT",
      verdict("Remote - US", description="z" * 150)["tier"] == "APPLY_CAVEAT")
check("APPLY with 500 chars of description stays APPLY", verdict("Remote - US", description="z" * 500)["tier"] == "APPLY")
check("INELIGIBLE without a description stays INELIGIBLE",
      verdict("Remote - US", tier="INELIGIBLE", description=None)["tier"] == "INELIGIBLE")
r = verdict("Remote - US", tier="APPLY_CAVEAT", description=None)
check("APPLY_CAVEAT keeps its own caveat", r["tier"] == "APPLY_CAVEAT" and r["reason"] == "model reason")
r = verdict("Toronto, ON, Canada", description=None)
check("non-US runs before the cap: a foreign title-only APPLY is INELIGIBLE, not capped", r["tier"] == "INELIGIBLE")

section("override: salary fallback")
use(tool_reply(tier="APPLY", reason="r", salary=""))
r = classifier.classify({**JOB, "description": "Pay: $85,000 - $110,000 per year. " + "y" * 300})
check("an empty model salary is filled from the description", r.get("salary") == "$85,000 - $110,000 per year", str(r.get("salary")))
use(tool_reply(tier="APPLY", reason="r", salary="$30/hr"))
r = classifier.classify({**JOB, "description": "Pay: $85,000 - $110,000 per year. " + "y" * 300})
check("the model's own salary wins", r.get("salary") == "$30/hr")

section("nothing private reaches the log")
with captured_logs() as logs:
    use(tool_reply(tier="APPLY", reason="SECRET-REASON"))
    classifier.classify(dict(JOB))
    classifier.classify({**JOB, "description": None})
    classifier.classify({**JOB, "location": "Toronto, ON, Canada"})
    use(tool_reply(tier="SECRET-REASON because he fits", reason="SECRET-REASON"))
    classifier.classify(dict(JOB))
check("a classify call never logs its reason (or the profile)",
      "SECRET-REASON" not in logs.text() and "PROFILE PLACEHOLDER" not in logs.text(), logs.text()[:300])
check("...even when the model puts prose in the tier field (logged by length only)",
      "he fits" not in logs.text())
with captured_logs() as logs:
    verdict("Toronto, ON, Canada")                       # non-US override
    verdict("Remote - US", description=None)             # title-only cap
    net("Campus Systems Engineer (Full-Time)", "I-5: software development role.")   # family net
shown = [r.getMessage() for r in logs.records if r.levelno >= logging.INFO]
check("the overrides log nothing at INFO: a verdict beside a job id is a per-job tier (DEBUG only)",
      not shown and any("override" in r.getMessage() for r in logs.records), str(shown))

sys.exit(testkit.finish())
