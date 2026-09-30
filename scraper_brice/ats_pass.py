"""ATS pass for the Brice pipeline -- PLACEHOLDER until the ATS pass lands (SPEC section 8).

main.py reaches the ATS pass only through the two functions below; the real
module must keep their names and signatures (main.collect_ats and
main._describe are the integration points):

  collect_ats_candidates() -> (candidates, stats)
      Fetch the 26 boards of ats_boards.py through the vendored
      ats_sources.fetch_all_listings and keep only rows that pass
      title_gate.source_gate(title, company, location, url,
      program_passthrough=True). No DB, no Claude, no per-job fetch.
      Each candidate: the listing fields (id "ats:<sha1(url)[:16]>", title,
      company, location, url, apply_url, posted_at, description -- None on
      Workday), plus source="ats", family=<source_gate label>,
      search_term=f"ats:{company}", norm_key=db.make_norm_key(company, title).
      stats = {"boards": 26, "boards_with_listings": n, "listings": n,
               "kept": n, "dropped_by": Counter(rule)}
      and optionally "dropped_samples": {rule: ["company | title", ...]}
      for the dry-run report. main alerts the owner when boards > 0 and
      boards_with_listings == 0.

  fetch_workday_description(url) -> Optional[str]
      One GET to Workday's CXS job endpoint. Returns None without a request
      unless the host ends with ".myworkdayjobs.com", and None on any
      exception. main calls it only after the already-stored guard, for an
      ATS row without a description, paced 0.5-1.0 s apart.

Until then this module sweeps nothing and requests nothing.
"""

import logging
from collections import Counter
from typing import Optional

log = logging.getLogger(__name__)


def collect_ats_candidates() -> tuple[list[dict], dict]:
    log.warning("ATS pass not built yet (placeholder ats_pass.py) — no boards swept")
    return [], {"boards": 0, "boards_with_listings": 0, "listings": 0, "kept": 0, "dropped_by": Counter()}


def fetch_workday_description(url: str) -> Optional[str]:
    return None
