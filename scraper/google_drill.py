"""The weekly Google fire drill (google_watch.drill), run by .github/workflows/apple_drill.yml after the
Apple one.

Treats one real role on Google's live U.S. internships list as new and sends what a new one would
send, marked DRILL: the 🔎 push, and the usual push when the verdict is APPLY. Writes nothing to the
tracker. Exits 1, after a canary naming the failed step, when any step does not work.

  python google_drill.py
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import google_watch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

if __name__ == "__main__":
    sys.exit(google_watch.drill())
