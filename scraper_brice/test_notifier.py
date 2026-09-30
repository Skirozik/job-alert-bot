"""notifier.py: tier priorities, and the rule that infrastructure alerts go to the owner, never to Brice.

Offline: notifier.requests is replaced by a fake that records every post and
answers with scripted status codes; time.sleep is recorded, not slept.

Run:  cd scraper_brice && python -X utf8 test_notifier.py
"""

import testkit

testkit.block_network()

import sys  # noqa: E402
import types  # noqa: E402

import config  # noqa: E402
import notifier  # noqa: E402
from testkit import captured_logs, check, patched, section  # noqa: E402

BRICE = "brice-topic-under-test"
OWNER = "owner-topic-under-test"


class FakeRequests:
    """Records posts; answers from a list of status codes (default 200) or raises `exc`."""

    def __init__(self, statuses=None, exc=None):
        self.posts = []
        self.statuses = list(statuses or [])
        self.exc = exc

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append({"url": url, "body": data.decode("utf-8"), "headers": dict(headers or {}), "timeout": timeout})
        if self.exc is not None:
            raise self.exc
        return types.SimpleNamespace(status_code=self.statuses.pop(0) if self.statuses else 200)


slept = []


def run(fn, *, brice=BRICE, owner=OWNER, statuses=None, exc=None):
    fake = FakeRequests(statuses, exc)
    slept.clear()
    with patched(config, NTFY_TOPIC=brice, OWNER_NTFY_TOPIC=owner), \
            patched(notifier, requests=fake, time=types.SimpleNamespace(sleep=slept.append)):
        result = fn()
    return result, fake


APPLY = {"id": "li:1", "tier": "APPLY", "company": "Initech", "title": "Associate Sales Engineer",
         "location": "Remote - US", "url": "https://www.linkedin.com/jobs/view/1/", "reason": "Pre-sales role, 0-2 yrs"}
CAVEAT = {**APPLY, "id": "li:2", "tier": "APPLY_CAVEAT", "reason": "asks 2+ years"}

section("push_job: Brice's topic, priority by tier")
ok, fake = run(lambda: notifier.push_job(dict(APPLY)))
p = fake.posts[0] if fake.posts else {"headers": {}, "url": "", "body": ""}
check("APPLY -> sent (True), one post", ok is True and len(fake.posts) == 1)
check("...to Brice's topic", p["url"] == f"https://ntfy.sh/{BRICE}")
check("...at high priority (it sounds)", p["headers"].get("Priority") == "high")
check("...tagged green_circle, Click = the job URL",
      p["headers"].get("Tags") == "green_circle" and p["headers"].get("Click") == APPLY["url"])
check("...Title '<company> - <title>'", p["headers"].get("Title") == "Initech - Associate Sales Engineer")
check("...body: tier line, location, 'Why: <reason>'",
      p["body"] == "🟢 APPLY\nRemote - US\nWhy: Pre-sales role, 0-2 yrs", repr(p["body"]))
check("...10 s timeout", p["timeout"] == 10)

ok, fake = run(lambda: notifier.push_job(dict(CAVEAT)))
p = fake.posts[0]
check("APPLY_CAVEAT -> low priority (silent), warning tag, yellow circle",
      ok is True and p["headers"].get("Priority") == "low" and p["headers"].get("Tags") == "warning"
      and p["body"].startswith("🟡 APPLY_CAVEAT"))
check("the priority table is exactly the owner's decision",
      notifier._TIER_PRIORITY == {"APPLY": "high", "APPLY_CAVEAT": "low"})

ok, fake = run(lambda: notifier.push_job({**APPLY, "company": "Café Größe", "title": "Ingénieur 🟢"}))
check("non-ASCII in the Title header is replaced, not sent raw (headers are latin-1)",
      fake.posts[0]["headers"]["Title"] == "Caf? Gr??e - Ing?nieur ?", fake.posts[0]["headers"]["Title"])

ok, fake = run(lambda: notifier.push_job(dict(APPLY)), brice="")
check("NTFY_TOPIC unset -> nothing posted, False", ok is False and fake.posts == [])
ok, fake = run(lambda: notifier.push_job(dict(APPLY)), brice="", owner=OWNER)
check("...and a job ping never falls back to the owner's topic", fake.posts == [])

section("push_owner_alert: the owner's topic only")
ok, fake = run(lambda: notifier.push_owner_alert("Claude classifier is DOWN (billing)"))
p = fake.posts[0] if fake.posts else {"headers": {}, "url": ""}
check("sent to the OWNER's topic", ok is True and p["url"] == f"https://ntfy.sh/{OWNER}")
check("...never to Brice's", all(BRICE not in x["url"] for x in fake.posts))
check("...urgent, warning/robot tags, default title",
      p["headers"] == {"Title": "Brice pipeline alert", "Priority": "urgent", "Tags": "warning,robot"}, str(p["headers"]))
ok, fake = run(lambda: notifier.push_owner_alert("recovered", title="Classifier recovered", priority="default"))
check("title and priority can be set per alert",
      fake.posts[0]["headers"]["Title"] == "Classifier recovered" and fake.posts[0]["headers"]["Priority"] == "default")
ok, fake = run(lambda: notifier.push_owner_alert("down"), owner="", brice=BRICE)
check("OWNER_NTFY_TOPIC unset -> nothing posted at all, even with Brice's topic set", ok is False and fake.posts == [])
check("there is no push_canary (every alert goes through push_owner_alert)", not hasattr(notifier, "push_canary"))

section("ntfy rate limit and failures")
ok, fake = run(lambda: notifier.push_job(dict(APPLY)), statuses=[429, 200])
check("429 then 200 -> exactly one retry, True", ok is True and len(fake.posts) == 2)
check("...after a 6 s wait", slept == [6], str(slept))
ok, fake = run(lambda: notifier.push_job(dict(APPLY)), statuses=[429, 429])
check("429 twice -> False, no third attempt", ok is False and len(fake.posts) == 2)
ok, fake = run(lambda: notifier.push_job(dict(APPLY)), statuses=[500])
check("a 500 -> False, not retried", ok is False and len(fake.posts) == 1)

leak = RuntimeError(f"500 Server Error for url: https://ntfy.sh/{BRICE}")
with captured_logs() as logs:
    ok, fake = run(lambda: notifier.push_job(dict(APPLY)), exc=leak)
    ok2, _ = run(lambda: notifier.push_owner_alert("down"), exc=RuntimeError(f"failed https://ntfy.sh/{OWNER}"))
    run(lambda: notifier.push_job(dict(APPLY)), statuses=[503])
    run(lambda: notifier.push_job(dict(APPLY)))
    run(lambda: notifier.push_owner_alert("secret alert body"))
text = logs.text()
check("a raising post -> False, never raises", ok is False and ok2 is False)
check("the logs never contain a topic or an ntfy URL",
      BRICE not in text and OWNER not in text and "ntfy.sh" not in text, text[:200])
check("...name the exception type and status instead",
      "ntfy post failed (RuntimeError, status=None)" in text and "status=503" in text)
check("...never the classifier reason (it goes to the phone only)", "Pre-sales role" not in text)
check("...and never an owner alert's body", "secret alert body" not in text)

sys.exit(testkit.finish())
