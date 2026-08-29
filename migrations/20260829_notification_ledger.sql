-- A durable ledger for "has this person already been told about this posting?"
--
-- THE BUG THIS CLOSES: ntfy pushed several times for the same company, and
-- pushed jobs that had already been applied to. Both are one defect. The push
-- decision was made from process-local, best-effort state -- an in-memory
-- dedup index plus a boolean from insert_job() that reports "the write did not
-- raise", not "this row is new" -- while the question it is really answering
-- is global, durable, and shared by three workflows running concurrently
-- (scrape/watch_linkedin, watch_ats, watch_github).
--
-- The principle this migration encodes:
--
--     "Should I do work?" may fail open. "Should I notify?" must fail closed,
--     and must be decided by the database.
--
-- Everything upstream of the push may keep its fail-open posture: a Supabase
-- blip that makes load_dedup_index return empty sets costs a re-classification,
-- which is merely expensive. It must not cost a second notification.
--
-- WHY A SECOND KEY. Three sources produce three primary keys for one posting
-- (LinkedIn a bare numeric id, ats:sha1(url), gh:sha1(apply_url)), so
-- on_conflict="id" never links them. norm_key is the only existing link and is
-- too brittle to carry it alone: norm_role deliberately keeps season and year,
-- and norm_company does not know "Regions" and "Regions Bank" are one
-- employer. target_key answers the narrower, provable question -- "do these
-- rows point at the same application?" -- from the apply URL alone, and is
-- NULL whenever that cannot be proven. See scraper/target_key.py, which is
-- pinned to web/lib/dupes.ts by fixtures/canonical_target_keys.json.
--
-- WHAT THIS DOES NOT DO: it never hides a job. A suppressed push still leaves
-- the row inserted, still status='new', still in To apply. The worst case is
-- seeing a posting without having been pinged about it -- which is why the
-- suppression is allowed to be more aggressive than the dashboard's display
-- grouping, per the trade recorded in scraper/db.py's norm_role comment.

alter table jobs add column if not exists notified_at timestamptz;
alter table jobs add column if not exists target_key  text;

-- Partial: the sibling lookup only ever probes non-null keys, and a large
-- share of rows (unrecognised careers hosts) legitimately have none.
create index if not exists jobs_target_key_idx
  on jobs (target_key) where target_key is not null;

-- BACKFILL, and it is not optional. Every APPLY/APPLY_CAVEAT row that exists
-- today WAS pushed at insert time -- that is precisely what main.py did. Left
-- null, the first run after this migration sees a table full of rows that have
-- "never been notified" and is free to notify them all.
update jobs
   set notified_at = found_at
 where notified_at is null
   and tier in ('APPLY', 'APPLY_CAVEAT');

-- Claim the exclusive right to notify about one row.
--
-- Idempotent: a retry after a successful claim returns already-notified. Safe
-- under any number of concurrent workers, independent of the GitHub Actions
-- concurrency groups and of scrape_runs' 20-minute staleness window -- both of
-- which stop being correctness-critical once this exists and become mere
-- cost/latency controls.
create or replace function public.claim_job_notification(p_id text)
returns table (should_notify boolean, reason text)
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_target_key  text;
  v_norm_key    text;
  v_notified_at timestamptz;
  v_status      text;
  v_lock        text;
  v_sibling     text;
begin
  select jobs.target_key, jobs.norm_key, jobs.notified_at, jobs.status
    into v_target_key, v_norm_key, v_notified_at, v_status
    from public.jobs where jobs.id = p_id;

  -- No row means the write did not land. This is what replaces insert_job()'s
  -- lying boolean: no row, no claim, no push. Previously ON CONFLICT DO NOTHING
  -- returned success for a no-op and the caller pushed anyway.
  if not found then
    return query select false, 'row-missing'::text;
    return;
  end if;

  -- make_norm_key("", "") is the literal string '|', so without this guard one
  -- blank-company row would match every other blank-company row and suppress
  -- genuinely unrelated postings. Empty string likewise.
  v_norm_key := nullif(nullif(v_norm_key, ''), '|');

  -- Serialise every identity this row participates in, ACQUIRED IN SORTED
  -- ORDER. Sorted acquisition is what makes deadlock impossible when two
  -- workers hold different rows that share one identity but not the other --
  -- which is the normal case here, since the racing rows have different
  -- primary keys and a row-level FOR UPDATE could not cover them.
  for v_lock in
    select k from (
      select 'target:' || v_target_key as k where v_target_key is not null
      union all
      select 'norm:' || v_norm_key      as k where v_norm_key is not null
    ) keys
    order by hashtext(k)
  loop
    perform pg_advisory_xact_lock(hashtext(v_lock));
  end loop;

  -- Re-read under the lock. Between the first select and here, a concurrent
  -- worker may have claimed this very row.
  select jobs.notified_at, jobs.status
    into v_notified_at, v_status
    from public.jobs where jobs.id = p_id;

  if v_notified_at is not null then
    return query select false, 'already-notified'::text;
    return;
  end if;

  if v_status is distinct from 'new' then
    return query select false, ('row-not-new:' || coalesce(v_status, 'null'))::text;
    return;
  end if;

  -- The sibling check. This single clause closes BOTH reported symptoms:
  -- a duplicate row from another source that was already pushed (dedupes the
  -- notification without merging the rows), and a duplicate whose twin the
  -- user has already applied to, saved, or dismissed (any status other than
  -- 'new' means they have seen it).
  --
  -- v_target_key NULL never matches, which is exactly the intent: a row whose
  -- application target could not be proven makes no cross-source claim.
  select case when jobs.notified_at is not null then 'notified' else 'status:' || jobs.status end
    into v_sibling
    from public.jobs
   where jobs.id <> p_id
     and (
          (v_target_key is not null and jobs.target_key = v_target_key)
       or (v_norm_key  is not null and jobs.norm_key  = v_norm_key)
     )
     and (jobs.notified_at is not null or jobs.status is distinct from 'new')
   limit 1;

  if v_sibling is not null then
    return query select false, ('sibling:' || v_sibling)::text;
    return;
  end if;

  update public.jobs set notified_at = now() where jobs.id = p_id;
  return query select true, 'claimed'::text;
end;
$$;

revoke all on function public.claim_job_notification(text) from public;
revoke all on function public.claim_job_notification(text) from anon;
revoke all on function public.claim_job_notification(text) from authenticated;
grant execute on function public.claim_job_notification(text) to service_role;

-- VERIFY BEFORE ENABLING THE GATE.
--
-- 1. Blast radius -- how many pushes this would have suppressed historically:
--
--    select count(*) from jobs a
--     where a.tier in ('APPLY','APPLY_CAVEAT')
--       and exists (select 1 from jobs b
--                    where b.id <> a.id
--                      and (b.target_key = a.target_key or b.norm_key = a.norm_key)
--                      and (b.notified_at is not null or b.status <> 'new'));
--
-- 2. The three-process case, in two SQL editor sessions, on two ids that share
--    a target_key. Session 2 must BLOCK, then return false:
--
--    -- session 1
--    begin; select * from claim_job_notification('<id A>');   -- leave open
--    -- session 2
--    select * from claim_job_notification('<id B>');          -- blocks
--    -- session 1
--    commit;                                                  -- session 2 -> false
