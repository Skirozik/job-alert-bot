#!/usr/bin/env python3
"""web/lib/greenhouse_boards.json must equal the Greenhouse entries of ATS_COMPANIES.

The dashboard builds the same Greenhouse backup link the phone alerts carry
(notifier.greenhouse_form_url), and for that it needs each Greenhouse board's
token. The browser cannot import ats_config.py, so the board tokens are copied
into a JSON file next to the TypeScript. This test keeps the copy honest: add
or rename a Greenhouse board in ats_config.py and this goes red until the JSON
is regenerated.

Run:        cd scraper && python test_greenhouse_boards_json.py
Regenerate: cd scraper && python test_greenhouse_boards_json.py --write
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ats_config import ATS_COMPANIES

JSON_PATH = Path(__file__).parent.parent / "web" / "lib" / "greenhouse_boards.json"
REGEN = "cd scraper && python test_greenhouse_boards_json.py --write"

_fails = 0
_ran = 0


def check(label, cond, why=""):
    global _fails, _ran
    _ran += 1
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"\n        {why}" if not cond and why else ""))
    if not cond:
        _fails += 1


def expected_boards() -> dict:
    return {name: cfg["token"] for name, cfg in sorted(ATS_COMPANIES.items())
            if cfg.get("platform") == "greenhouse"}


def render(boards: dict) -> str:
    return json.dumps(boards, indent=2, ensure_ascii=False) + "\n"


if "--write" in sys.argv[1:]:
    boards = expected_boards()
    JSON_PATH.write_text(render(boards), encoding="utf-8")
    print(f"wrote {len(boards)} Greenhouse boards to {JSON_PATH.relative_to(JSON_PATH.parents[2])}")
    sys.exit(0)

print("-- web/lib/greenhouse_boards.json mirrors the Greenhouse boards in ats_config.py --")

want = expected_boards()
check("ats_config has Greenhouse boards", len(want) > 0)
check("Airbnb, the posting that motivated the backup link, is one of them", want.get("Airbnb") == "airbnb")

check("the JSON file exists", JSON_PATH.exists(), f"missing: {REGEN}")
if JSON_PATH.exists():
    raw = JSON_PATH.read_text(encoding="utf-8")
    got = json.loads(raw)
    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    changed = sorted(k for k in set(want) & set(got) if want[k] != got[k])
    check("every Greenhouse board in ats_config is in the JSON", not missing,
          f"missing {missing}: {REGEN}")
    check("the JSON names no board ats_config lacks", not extra,
          f"extra {extra}: {REGEN}")
    check("every token matches", not changed,
          f"tokens differ for {changed}: {REGEN}")
    check("the file is the exact text --write produces (sorted keys, two-space indent)",
          raw == render(want), REGEN)

print(f"\n{_ran - _fails} passed, {_fails} failed")
sys.exit(1 if _fails else 0)
