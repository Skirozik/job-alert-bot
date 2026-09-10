-- resume_builds: one tailoring plan per starred job.
--
-- WHY A TABLE AND NOT A COLUMN. gold_star is derived-not-stored and that is
-- right for it -- it is a pure function of a row. A build is not: it is an
-- artifact with a lifecycle (claimed -> planned -> rendered, or failed), it
-- carries a plan and a lint verdict, and it needs timestamps. That needs rows.
--
-- KEYED ON job_id, NOT target_key. Resume_Tailor_BuildSpec.md proposes
-- `target_key text not null` with `unique (target_key)`, arguing six IBM
-- variants across six cities collapse into one resume. Measured against live
-- data, both halves are wrong:
--
--   * 28 of the 176 currently-eligible starred rows (15.9%) have a NULL
--     target_key -- by design, target_key.py:88 calls None "the honest answer
--     for a URL we cannot positively identify". Postgres permits unlimited
--     NULLs in a unique index, so those rows would get NO idempotency at all,
--     and they are precisely the rows a re-run would rebuild every time.
--   * The collapse does not happen. Those 176 rows hold 174 distinct
--     target_key-or-norm_key values: exactly 2 rows share a unit. The saving
--     is ~1%, not six-for-one. IBM in particular returns None, because
--     careers.ibm.com is not one of the 8 hosts definitive_target_key knows.
--
-- So job_id is the key, and target_key is kept only as a nullable hint for a
-- future "you already tailored a sibling of this" notice.
--
-- NO ADVISORY LOCKS, unlike claim_job_notification. That function locks over
-- target_key and norm_key because it must serialise SIBLINGS -- different
-- primary keys that share an identity, which a row-level lock cannot cover.
-- Here the claim is per job_id, so the primary key is the whole race, and a
-- single INSERT ... ON CONFLICT is atomic on its own. Copying the lock
-- machinery would import complexity that solves a problem this table does not
-- have.

create table if not exists public.resume_builds (
  job_id      text primary key references public.jobs(id) on delete cascade,

  -- claimed  : a worker took it, no plan yet
  -- planned  : a validated plan exists; no PDF has been written
  -- rendered : a PDF was produced and passed lint
  -- failed   : the tailor errored, or the plan/PDF failed a gate
  status      text not null default 'claimed',

  plan        jsonb,
  lint        jsonb,
  pdf_path    text,
  final_y     numeric,

  -- The spec's design had no way back: `unique (target_key)` plus a poll of
  -- "no row yet" means a build that fails lint writes a failed row, which
  -- satisfies "has a row", which excludes that job forever. Since lint is
  -- specified to FAIL rather than warn, failures are the expected early case.
  attempts    integer not null default 0,
  last_error  text,

  target_key  text,

  planned_at  timestamptz,
  rendered_at timestamptz,          -- the only usage signal this system gets
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index if not exists resume_builds_status_idx on public.resume_builds (status);
create index if not exists resume_builds_target_key_idx
  on public.resume_builds (target_key) where target_key is not null;


-- Atomic claim. Returns exactly one row: (should_build, reason).
--
-- Retry policy lives here rather than in the caller so two workers cannot both
-- decide a failed build is retryable. Three attempts, then it stays failed and
-- needs a human -- an unbounded retry against a plan that cannot validate would
-- burn a tailor call per poll, forever.
create or replace function public.claim_job_resume_build(
  p_id         text,
  p_target_key text default null,
  p_max_tries  integer default 3
)
returns table (should_build boolean, reason text)
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_exists boolean;
  v_claimed boolean := false;
begin
  select true into v_exists from public.jobs where jobs.id = p_id;
  if not found then
    -- Same guard as claim_job_notification: no row means the write never
    -- landed, and a build for a job that is not in the table is meaningless.
    return query select false, 'row-missing'::text;
    return;
  end if;

  -- One statement, so the race is resolved by the primary key. The WHERE on
  -- the DO UPDATE branch is what makes this a claim rather than a clobber:
  -- it fires only for a failed row under the retry ceiling, so a claimed,
  -- planned or rendered row updates nothing and returns nothing.
  insert into public.resume_builds as rb (job_id, status, attempts, target_key, updated_at)
  values (p_id, 'claimed', 1, p_target_key, now())
  on conflict (job_id) do update
     set status     = 'claimed',
         attempts   = rb.attempts + 1,
         last_error = null,
         updated_at = now()
   where rb.status = 'failed'
     and rb.attempts < p_max_tries
  returning true into v_claimed;

  if v_claimed then
    return query select true, 'claimed'::text;
    return;
  end if;

  return query
    select false,
           case
             when rb.status = 'failed' then 'failed-retries-exhausted'
             else 'already-' || rb.status
           end
      from public.resume_builds rb where rb.job_id = p_id;
end;
$$;

-- RLS with no policies: deny by default for every browser-facing role.
--
-- This does NOT affect the two callers. Both the local builder and the
-- dashboard's server route authenticate as service_role, which bypasses RLS,
-- so there is nothing to write a policy FOR -- an empty policy set is the
-- correct end state here, not an unfinished one.
--
-- It overlaps with the revokes below on purpose. Grants and RLS are
-- independent defences: the day someone adds a convenience `grant select` to
-- authenticated, the revokes stop protecting this table and RLS still does.
-- Supabase's linter flags a table created without it, and the linter is right.
alter table public.resume_builds enable row level security;

-- Called only from the local builder and the dashboard's server route, both of
-- which authenticate with the persona service key. Never expose a write to a
-- browser-side role.
revoke all on function public.claim_job_resume_build(text, text, integer) from public;
revoke all on function public.claim_job_resume_build(text, text, integer) from anon;
revoke all on function public.claim_job_resume_build(text, text, integer) from authenticated;
grant execute on function public.claim_job_resume_build(text, text, integer) to service_role;

revoke all on table public.resume_builds from public;
revoke all on table public.resume_builds from anon;
revoke all on table public.resume_builds from authenticated;
grant select, insert, update on table public.resume_builds to service_role;


-- Verification, before trusting it:
--
--   -- claims once, refuses twice
--   select * from claim_job_resume_build('<some starred job id>');   -- true, claimed
--   select * from claim_job_resume_build('<same id>');               -- false, already-claimed
--
--   -- a failed row is retryable up to the ceiling
--   update resume_builds set status='failed' where job_id='<same id>';
--   select * from claim_job_resume_build('<same id>');               -- true, claimed
--
--   -- a job that does not exist is refused, not inserted
--   select * from claim_job_resume_build('no-such-job');             -- false, row-missing
--
--   -- and a NULL target_key must still claim, since 15.9% of starred rows have one
--   select * from claim_job_resume_build('<a gh: starred job>', null);
