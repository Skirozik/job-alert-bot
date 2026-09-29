"""Recompute norm_key for every stored job with the CURRENT make_norm_key and
rewrite the ones that are stale.

Why this exists: norm_key is computed once, at insert time, and never
revisited. When the key logic changes, every earlier row keeps its old key
forever, and dedup compares new listings against those stale keys. It was
first needed for commit eaf58de (a norm_role rework); it is needed now for the
internship/full-time split in make_norm_key, which gives every stored
full-time row a new "|ft" key. Until that rewrite runs, those full-time rows
keep the old key and keep swallowing same-titled internships.

Only ever touches the norm_key column — never tier/status/reason — so it is
safe at any time: it cannot revert a status or reclassify a job. Idempotent: it
only queues rows whose stored key differs from the computed one, so a partial
or interrupted run resumes where it stopped. Re-run the dry run afterwards; it
must report zero stale keys.

EGRESS, which this project has run out of once (2026-09-12): the read is four
narrow columns (~150 bytes a row, ~15 MB for ~96k rows); every write asks for
returning=minimal. The previous version of this script left postgrest's default
(returning=representation), so each update downloaded the whole row back,
description included — hundreds of MB for a rewrite this size.

Also targets the persona databases, whose key code is identical (enforced by
test_norm_key.py): pass the env file that holds their Supabase credentials.

Run from the scraper directory:
    python backfill_norm_keys.py                         # dry run, main database
    python backfill_norm_keys.py --apply                 # write, main database
    python backfill_norm_keys.py --env ../.env.beyonce   # dry run, Beyonce's
    python backfill_norm_keys.py --env ../.env.hassan --apply
"""

import argparse
import logging
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from postgrest import ReturnMethod
from supabase import create_client

from db import FULL_TIME_KEY_SUFFIX, QuotaExceeded, _norm_role_parts, _raise_if_quota, make_norm_key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

PAGE = 1000


def client_for(env_path: Path):
    """A client for whichever database the env file names. Its values are never
    printed; only the file name is, so the log says which database was touched."""
    env = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    url, key = env.get("SUPABASE_URL"), env.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise SystemExit(f"{env_path.name} has no SUPABASE_URL / SUPABASE_SERVICE_KEY")
    return create_client(url, key)


def load_all_jobs(client) -> list[dict]:
    """Narrow paginated read. .order("id") is required, not cosmetic: a .range()
    walk with no ORDER BY is not a stable enumeration while three workflows are
    inserting, and can return a row in two pages or in none."""
    rows: list[dict] = []
    offset = 0
    while True:
        try:
            result = (
                client.table("jobs")
                .select("id,title,company,norm_key")
                .order("id")
                .range(offset, offset + PAGE - 1)
                .execute()
            )
        except Exception as exc:
            _raise_if_quota(exc)
            raise
        page = result.data or []
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE
        if offset % 20000 == 0:
            log.info("  loaded %d rows...", offset)


def report(jobs: list[dict], stale: list[tuple[dict, str]]) -> None:
    """What the rewrite will do, in the terms that matter.

    "gains |ft" is the intended change: a full-time row moving into its own key
    space. Anything else is drift from an older key rule and is listed so a
    surprise cannot hide inside a large number.
    """
    kinds = Counter()
    other = []
    for job, new_key in stale:
        if new_key == f"{job.get('norm_key')}{FULL_TIME_KEY_SUFFIX}":
            kinds["gains |ft (full-time row separated from internships)"] += 1
        else:
            kinds["other drift"] += 1
            other.append((job, new_key))
    for label, n in kinds.most_common():
        log.info("  %6d  %s", n, label)
    for job, new_key in other[:10]:
        log.info("         %s | %r -> %r", job["id"], job.get("norm_key"), new_key)

    # The bug, measured: base keys held today by BOTH an internship-titled row
    # and a full-time row. Each is a collision the rewrite dissolves.
    by_key = defaultdict(lambda: [0, 0])
    for job in jobs:
        stripped = _norm_role_parts(job.get("title"))[1]
        by_key[job.get("norm_key")][0 if stripped else 1] += 1
    mixed = [k for k, (interns, fulltime) in by_key.items() if interns and fulltime]
    log.info("  %6d  keys shared today by an internship AND a full-time row (collisions dissolved)",
             len(mixed))
    for k in sorted(mixed)[:10]:
        log.info("         e.g. %r", k)


def write_all(client, stale: list[tuple[dict, str]], workers: int) -> tuple[int, int]:
    """One request per row: PostgREST has no bulk update-to-different-values
    short of an RPC. Threaded, like backfill_target_keys.py, so ~60k rows take
    minutes rather than hours; a modest pool, because these are writes."""
    written = failed = 0

    def _write(item):
        job, new_key = item
        (client.table("jobs")
         .update({"norm_key": new_key}, returning=ReturnMethod.minimal)
         .eq("id", job["id"])
         .execute())

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_write, item): item for item in stale}
        for done in as_completed(futures):
            job, _ = futures[done]
            try:
                done.result()
                written += 1
            except Exception as exc:
                try:
                    _raise_if_quota(exc)
                except QuotaExceeded:
                    # A spent quota never clears itself; stop rather than fail
                    # 60,000 times. Everything unwritten is re-queued next run.
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
                failed += 1
                if failed <= 20:
                    log.error("  failed for %s: %s", job["id"], exc)
            if written and written % 5000 == 0:
                log.info("  written %d/%d", written, len(stale))
    return written, failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--env", default=str(Path(__file__).parent.parent / ".env"),
                    help="env file naming the database (default: the main one)")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    env_path = Path(args.env)
    client = client_for(env_path)
    log.info("Database: the one named in %s", env_path.name)

    jobs = load_all_jobs(client)
    log.info("Loaded %d jobs", len(jobs))

    stale = []
    for job in jobs:
        current = make_norm_key(job.get("company") or "", job.get("title") or "")
        if current != job.get("norm_key"):
            stale.append((job, current))
    log.info("%d/%d jobs have a stale norm_key", len(stale), len(jobs))
    report(jobs, stale)

    if not args.apply:
        log.info("DRY RUN — nothing written. Examples:")
        for job, new_key in stale[:20]:
            log.info("  %s | %r -> %r", job["id"], job.get("norm_key"), new_key)
        log.info("Re-run with --apply to write these %d corrections.", len(stale))
        return 0

    written, failed = write_all(client, stale, args.workers)
    log.info("=== Done: %d updated, %d failed ===", written, failed)
    if failed:
        log.warning("Re-run to retry the %d that failed; only stale rows are queued.", failed)
    log.info("Now re-run WITHOUT --apply: it must report 0 stale keys.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
