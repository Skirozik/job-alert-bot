-- Change every stored source row for one dashboard group in a single
-- transaction. A missing member raises before the UPDATE, so the client can
-- never receive a failure after only part of a duplicate group was changed.
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
  if p_status not in ('new', 'saved', 'applied', 'dismissed') then
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

-- The dashboard calls this only from its authenticated server route with the
-- service-role key. Do not expose a bulk status write to browser-side roles.
revoke all on function public.set_job_group_status(text[], text) from public;
revoke all on function public.set_job_group_status(text[], text) from anon;
revoke all on function public.set_job_group_status(text[], text) from authenticated;
grant execute on function public.set_job_group_status(text[], text) to service_role;
