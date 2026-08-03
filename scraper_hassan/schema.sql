-- Schema for Hassan's job-alert Supabase project.
-- Run this in the NEW Supabase project's SQL editor (a project separate
-- from the original persona's — see the plan doc for why isolation matters).
--
-- Same shape as the original persona's `jobs`/`scrape_runs`/`bot_state`
-- tables, minus the `suggested_resume` column (this persona has one resume,
-- not variants to pick between — see scraper_hassan/classifier.py).
--
-- After running this, ENABLE RLS on all three tables with no policies. The
-- scrapers and the dashboard's server side both use the service_role key,
-- which bypasses RLS, so nothing breaks — but it keeps the anon key (which
-- ships in the dashboard's public JS bundle) from reading anything.

create table jobs (
  id               text primary key,
  title            text,
  company          text,
  location         text,
  url              text,
  search_term      text,
  description      text,
  logo_url         text,
  norm_key         text,
  tier             text,
  reason           text,
  status           text default 'new',
  posted_at        timestamptz,
  found_at         timestamptz default now(),
  apply_url        text,
  is_easy_apply    boolean default false,
  salary           text
);

create index jobs_norm_key_idx on jobs (norm_key);
create index jobs_tier_idx on jobs (tier);
create index jobs_found_at_idx on jobs (found_at desc);

create table scrape_runs (
  id           bigint generated always as identity primary key,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  total_raw    int,
  new_jobs     int,
  notified     int,
  rate_limited int
);

-- Not used by scraper_hassan/main.py yet (no GitHub-tracker fast-path
-- watcher for this persona), but costs nothing to create now — keeps a
-- future digest.py-equivalent or state-tracking need a zero-migration add.
create table bot_state (
  key   text primary key,
  value text
);
