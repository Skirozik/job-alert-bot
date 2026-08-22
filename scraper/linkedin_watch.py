"""Shallow, high-frequency LinkedIn fast path.

The full main.py sweep remains the completeness path: it can paginate ten
pages for every term/location and recover jobs that LinkedIn ranks strangely.
This watcher checks page zero for all searches much more often and immediately
processes anything new. Both paths use the same ``linkedin`` source lock and
the same scan/process functions, so they cannot overlap or drift apart.
"""

import logging
import sys

from db import start_run, finish_run
from main import scan_linkedin, _PARKED_THIS_RUN, _maybe_alert_classifier_down


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def run() -> None:
    run_id = start_run("linkedin")
    if run_id is None:
        log.info("A full or fast LinkedIn scan is already running — skipping this pass.")
        return

    total_raw = new_jobs = rate_limited = notified = 0
    try:
        total_raw, new_jobs, rate_limited, notified = scan_linkedin(max_pages_per_search=1)
        log.info(
            "=== LinkedIn fast watch complete: %d raw, %d new, %d notified, %d rate-limited ===",
            total_raw, new_jobs, notified, rate_limited,
        )
    finally:
        finish_run(
            run_id,
            total_raw=total_raw,
            new_jobs=new_jobs,
            notified=notified,
            rate_limited=rate_limited,
        )
        if _PARKED_THIS_RUN:
            _maybe_alert_classifier_down()


if __name__ == "__main__":
    run()
