-- Schema for Brice's job-alert Supabase project (scraper_brice/).
-- Run ONCE in the NEW project's SQL editor. The statements below deliberately carry no
-- existence guard: an error saying a relation already exists means this is the wrong
-- project -- stop there.
--
-- The jobs table has the same 17 columns as scraper_hassan/schema.sql, which covers every
-- column the dashboard selects (web/app/page.tsx COLS_BASE). There is no suggested_resume and
-- no target_key; the dashboard falls back without them.
--
-- tier is plain text on purpose. APPLY / APPLY_CAVEAT / INELIGIBLE are verdicts; PENDING is the
-- retry-queue state of a job whose classification failed during a Claude outage. The dashboard
-- lists only APPLY and APPLY_CAVEAT, so a PENDING row stays out of sight until it is promoted.

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
-- The dashboard's hot filters (SITE_SPEED_PROMPT.md, step 4).
create index jobs_status_found_idx on jobs (status, found_at desc);
create index jobs_new_tier_found_idx on jobs (tier, found_at desc) where status = 'new';

create table scrape_runs (
  id                  bigint generated always as identity primary key,
  started_at          timestamptz not null default now(),
  finished_at         timestamptz,
  total_raw           int,
  new_jobs            int,
  notified            int,
  rate_limited        int,
  ats_candidates      int,
  jobright_candidates int,
  classified          int,
  failed              int,
  leftover            int
);

-- Owner-alert throttle markers and the jobright README ETags.
create table bot_state (
  key   text primary key,
  value text
);

-- RLS on, no policies: the scraper and the dashboard use the secret key, which bypasses RLS,
-- while the publishable key reads nothing.
alter table jobs enable row level security;
alter table scrape_runs enable row level security;
alter table bot_state enable row level security;
