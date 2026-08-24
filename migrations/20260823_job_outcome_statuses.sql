-- Allow the four outcome statuses through the atomic group-status function.
--
-- 20260822_atomic_job_status_updates.sql hard-codes the accepted list in SQL:
--
--     if p_status not in ('new', 'saved', 'applied', 'dismissed') then
--       raise exception 'invalid job status' using errcode = '22023';
--
-- so 'heard_back' / 'interview' / 'offer' / 'rejected' were rejected by the
-- database even though `jobs.status` is a plain text column that would accept
-- them. This replaces the function with the widened list and changes nothing
-- else -- the locking, the cardinality guard, the partial-write protection and
-- the grants are all carried over verbatim.
--
-- WHY THESE STATES: 560 applications had been sent with no record of what came
-- back, so "does direct-to-ATS beat LinkedIn Easy Apply" and "does the AI
-- resume convert better than General" were unanswerable. The lifecycle is
-- ordered and a later state implies the earlier one, so "applied" means "sent,
-- nothing heard yet" and the existing rows stay correct with no backfill.
create or replace function public.set_job_group_status(
  p_ids text[],
  p_status text
)
returns table(id text, status text)
language plpgsql
security invoker
set search_path = public
as $$
declare
  matched integer;
begin
  if p_status not in (
    'new', 'saved', 'applied', 'dismissed',
    'heard_back', 'interview', 'offer', 'rejected'
  ) then
    raise exception 'invalid job status' using errcode = '22023';
  end if;

  if coalesce(cardinality(p_ids), 0) = 0 or cardinality(p_ids) > 50 then
    raise exception 'invalid job id count' using errcode = '22023';
  end if;

  -- Lock the validated rows until the UPDATE completes. Without this, a
  -- concurrent delete between the count and update could still produce a
  -- partial write even though both statements live inside one function call.
  select count(*) into matched
  from (
    select jobs.id
    from public.jobs
    where jobs.id = any(p_ids)
    for update
  ) locked;

  if matched <> cardinality(p_ids) then
    raise exception 'one or more jobs were not found' using errcode = 'P0002';
  end if;

  return query
  update public.jobs
  set status = p_status
  where jobs.id = any(p_ids)
  returning jobs.id, jobs.status;
end;
$$;

-- create or replace preserves existing grants, but restate them so this file
-- stands alone if it is ever applied to a fresh database.
revoke all on function public.set_job_group_status(text[], text) from public;
revoke all on function public.set_job_group_status(text[], text) from anon;
revoke all on function public.set_job_group_status(text[], text) from authenticated;
grant execute on function public.set_job_group_status(text[], text) to service_role;
