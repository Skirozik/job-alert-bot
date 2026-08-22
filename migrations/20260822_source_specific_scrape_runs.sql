-- Let independent source watchers overlap without making two LinkedIn passes
-- overlap. Existing rows are historical main/legacy runs, so "linkedin" is the
-- safe default during the migration.
alter table scrape_runs
  add column if not exists source text not null default 'linkedin';

create index if not exists scrape_runs_active_source_idx
  on scrape_runs (source, started_at desc)
  where finished_at is null;
