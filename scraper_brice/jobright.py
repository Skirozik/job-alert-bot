"""jobright pass for the Brice pipeline -- PLACEHOLDER until the jobright pass lands (SPEC section 9).

main.collect_jobright() (the integration point) calls these four functions for
each config.JOBRIGHT_LISTS entry; the real module must keep their names and
signatures, and must never request jobright.ai (robots.txt):

  fetch_readme(url, etag=None) -> (status, text, response_etag)
      GET with If-None-Match when etag is given; 304 -> (304, "", etag).
      Refuses (returns (0, "", None) without a request) any host other than
      raw.githubusercontent.com; exceptions -> (0, "", None), logged by type.

  parse_readme(text, list_name, today) -> (rows, unparsed_link_rows)
      Row = {id: "jr:<hexid>", title, company, location, work_model,
             posted: "YYYY-MM-DD" | None, url: "https://jobright.ai/jobs/info/<hexid>",
             list: list_name}

  rows_to_jobs(rows, list_name, *, support_only, today) -> (jobs, dropped_by)
      SPEC 9.3 steps 1-3: drop rows older than config.JOBRIGHT_MAX_AGE_DAYS;
      title_gate.source_gate(title, company, location, "",
      support_list=support_only, program_passthrough=False); job dicts with
      id, title, company, location (+ " (Remote)" / " (Hybrid)"), url,
      apply_url None, posted_at, description None, is_easy_apply False,
      logo_url None, search_term=f"jobright:{list_name}", source="jobright",
      family=<label>, norm_key=db.make_norm_key(company, title).
      dropped_by: Counter(rule).

  canary_problems(status, parsed_rows, unparsed_link_rows) -> [problem, ...]
      SPEC 9.3 step 4: [] when healthy; otherwise short descriptions (HTTP
      status not 200/304; 200 with 0 parsed rows; unparsed link rows > 5% of
      link rows). main alerts the owner per list, throttled 24 h.

main owns the ETag round trip (bot_state "jobright_etag:<name>", saved only
when a list left no candidates behind) and the 1-2 s pacing between lists.
Until then this module requests nothing and reports every list as not built.
"""

import logging
from collections import Counter
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)

NOT_BUILT = "jobright pass not built yet (placeholder jobright.py)"


def fetch_readme(url: str, etag: Optional[str] = None) -> tuple[int, str, Optional[str]]:
    log.warning("%s — nothing fetched", NOT_BUILT)
    return 0, "", None


def parse_readme(text: str, list_name: str, today: date) -> tuple[list[dict], int]:
    return [], 0


def rows_to_jobs(rows: list[dict], list_name: str, *, support_only: bool, today: date) -> tuple[list[dict], Counter]:
    return [], Counter()


def canary_problems(status: int, parsed_rows: int, unparsed_link_rows: int) -> list[str]:
    return [NOT_BUILT]
