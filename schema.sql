-- Base tables for the main pipeline. In a new Supabase project, run this in the
-- SQL Editor first, then every file in migrations/ in name order (they add the
-- functions and columns the bot uses now).

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
  tier             text,                    -- APPLY | APPLY_CAVEAT | INELIGIBLE
                                            -- plus PENDING, a queue state (not a
                                            -- verdict) for jobs awaiting retry
                                            -- after a classifier outage
  reason           text,
  suggested_resume text,
  status           text default 'new',      -- new | saved | applied | dismissed
  posted_at        timestamptz,
  found_at         timestamptz default now(),
  apply_url        text,
  is_easy_apply    boolean default false,
  salary           text,
  notified_at      timestamptz,             -- set once, by claim_job_notification.
                                            -- The ledger that makes a push
                                            -- at-most-once across the three
                                            -- concurrent workflows.
  target_key       text                     -- cross-source identity of the
                                            -- APPLICATION (not the row): the
                                            -- same Workday req reached via
                                            -- LinkedIn, the ATS and a GitHub
                                            -- tracker shares one target_key.
                                            -- NULL when it cannot be proven,
                                            -- which makes no claim at all.
);

create index jobs_norm_key_idx on jobs (norm_key);
create index jobs_tier_idx on jobs (tier);
create index jobs_found_at_idx on jobs (found_at desc);
create table scrape_runs (
  id           bigint generated always as identity primary key,
  source       text not null default 'linkedin',
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  total_raw    int,
  new_jobs     int,
  notified     int,
  rate_limited int
);

create index scrape_runs_active_source_idx
  on scrape_runs (source, started_at desc)
  where finished_at is null;

create table bot_state (
  key   text primary key,
  value text
);
