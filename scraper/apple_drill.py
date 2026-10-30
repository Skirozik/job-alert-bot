"""The weekly Apple fire drill (apple_watch.drill), run by .github/workflows/apple_drill.yml.

Treats one real posting on Apple's live internships page as new and sends what a new one would
send, marked DRILL: the 🍎 push, and the usual push when the verdict is APPLY. Writes nothing to the
tracker. Exits 1, after a canary naming the failed step, when any step does not work.

  python apple_drill.py
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import apple_watch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

if __name__ == "__main__":
    sys.exit(apple_watch.drill())
