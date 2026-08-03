import modal

# Standalone Modal app for the Beyonce persona's job search — deliberately
# NOT added as a second @app.function inside modal_app.py. A shared app
# means every future `modal deploy modal_app.py` redeploys both personas'
# functions from one command; a bad import or syntax error in this pipeline
# would then risk breaking deploys of the original, live SWE-internship
# pipeline. Standalone means `modal deploy modal_app_beyonce.py` only ever
# touches this app.
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("scraper_beyonce/requirements.txt")
    .add_local_dir("scraper_beyonce", remote_path="/app/scraper_beyonce")
    .add_local_file(
        "Beyonce_Candidate_Profile_and_Filters.md",
        remote_path="/app/Beyonce_Candidate_Profile_and_Filters.md",
    )
)

app = modal.App("job-alert-scraper-beyonce", image=image)


@app.function(
    # 2-hour cadence, not 20 minutes — this search isn't a race. Hourly
    # admin/hospitality postings run ~20 days median time-to-fill and are
    # frequently evergreen/reposted, unlike competitive SWE internships that
    # can close within days of posting.
    schedule=modal.Period(hours=2),
    # Modal's default is 300s. A measured cold-start run (empty dedup index,
    # live LinkedIn, real Haiku calls) landed at 219-262s — only 15-25% of
    # headroom, and the tail is fat: job volume swings run to run and a single
    # rate-limited detail fetch burns ~25s. Blowing the ceiling degrades
    # SILENTLY (the container is killed mid-loop, the canary only fires when
    # all 11 searches return zero, and the loss is biased toward the last
    # search terms), so buy the headroom. Still far under the 2h cron period.
    timeout=1500,
    secrets=[modal.Secret.from_name("job-alert-secrets-beyonce")],
)
def scrape():
    import sys
    import os
    sys.path.insert(0, "/app/scraper_beyonce")
    os.chdir("/app/scraper_beyonce")
    from main import run
    run()


@app.local_entrypoint()
def main():
    """Run one scrape immediately: modal run modal_app_beyonce.py"""
    scrape.remote()
