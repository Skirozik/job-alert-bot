-- Move the dedup comparison server-side, because downloading the answer costs
-- more than the whole free tier.
--
-- MEASURED, not estimated. One ATS Fast-Path run on 2026-08-29 (run
-- 33270821066) issued 73 paginated GETs of jobs(id, norm_key) -- the entire
-- table, ~73,000 rows, roughly 4-7 MB depending on norm_key length. The ATS
-- watcher runs every 5 minutes:
--
--     288 runs/day x ~5 MB  =  ~1.4 GB/day  =  ~42 GB/month
--
-- against a 5 GB quota. That one function is ~8x the entire monthly allowance
-- on its own, and it is why the project went over. The dashboard's 14.09 MB
-- refetch was found and cut earlier (POLL_MS in web/components/JobList.tsx);
-- this is the same class of bug on the scraper side, an order of magnitude
-- larger, and it had never been measured.
--
-- THE INVERSION: the scraper does not need the table, it needs an ANSWER --
-- "which of these ~30,700 listings have I not seen?" Sending the candidates up
-- is ingress, which is not billed; sending 73,000 rows down is egress, which
-- is. So send the question, not fetch the data to answer it locally. The
-- response is only the genuinely-new ids, which is typically single digits.
--
--     before:  ~5 MB down per run
--     after:   a few hundred bytes down per run
--
-- Deleting load_dedup_index() also retires its unstable-pagination bug (a
-- .range() walk with no ORDER BY can return a row in two pages or in none)
-- rather than leaving a patched version around to be reused.
--
-- SEMANTICS, matching what the Python it replaces did exactly: a candidate is
-- "known" if its id matches OR its norm_key matches. Callers must still dedup
-- WITHIN the batch themselves -- this answers about stored rows only, and two
-- listings in the same sweep can share a norm_key without either being stored
-- yet.

create or replace function public.unknown_candidates(
  p_ids       text[],
  p_norm_keys text[]
)
returns setof text
language sql
stable
security invoker
set search_path = public
as $$
  -- unnest of two arrays pairs them positionally, so p_ids[i] must correspond
  -- to p_norm_keys[i]. The caller builds both from the same list in one pass.
  select c.id
    from unnest(p_ids, p_norm_keys) as c(id, norm_key)
   where not exists (
           select 1 from public.jobs j where j.id = c.id
         )
     and not exists (
           -- make_norm_key("", "") is the literal '|', and a blank company
           -- yields ''. Either would otherwise match every other blank row and
           -- make genuinely-new listings look known -- a MISSED job, which is
           -- the expensive direction. NULL here makes the comparison never
           -- true, so the candidate correctly falls through as unknown.
           select 1 from public.jobs j
            where j.norm_key = nullif(nullif(c.norm_key, ''), '|')
         )
$$;

revoke all on function public.unknown_candidates(text[], text[]) from public;
revoke all on function public.unknown_candidates(text[], text[]) from anon;
revoke all on function public.unknown_candidates(text[], text[]) from authenticated;
grant execute on function public.unknown_candidates(text[], text[]) to service_role;

-- jobs.norm_key already carries a plain index (jobs_norm_key_idx, README
-- schema); jobs.id is the primary key. Both anti-joins are index probes, so
-- this stays proportional to the batch rather than to the table.

-- VERIFY:
--
--   -- a stored id and a stored norm_key are both filtered out; a made-up one
--   -- comes back. Expect exactly one row: 'definitely-not-a-real-id'.
--   select * from unknown_candidates(
--     array[(select id from jobs limit 1), 'definitely-not-a-real-id'],
--     array[(select norm_key from jobs where norm_key is not null limit 1), 'nobody|nothing']
--   );
