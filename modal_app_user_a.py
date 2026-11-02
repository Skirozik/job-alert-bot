import os

import modal

# Standalone Modal app for the User A persona's job search — deliberately
# NOT added as a second @app.function inside modal_app.py. A shared app
# means every future `modal deploy modal_app.py` redeploys both personas'
# functions from one command; a bad import or syntax error in this pipeline
# would then risk breaking deploys of the original, live SWE-internship
# pipeline. Standalone means `modal deploy modal_app_user_a.py` only ever
# touches this app.
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("scraper_user_a/requirements.txt")
    .add_local_dir("scraper_user_a", remote_path="/app/scraper_user_a")
    .add_local_file(
        "User_A_Candidate_Profile_and_Filters.md",
        remote_path="/app/User_A_Candidate_Profile_and_Filters.md",
    )
)

# The live app and its Modal secret keep the names they were created under,
# which stay out of this public repo: set MODAL_APP_NAME and
# MODAL_SECRET_NAME to them before `modal deploy`.
app = modal.App(os.environ.get("MODAL_APP_NAME", "job-alert-scraper-user_a"), image=image)


@app.function(
    # 2-hour cadence, not 20 minutes — this search isn't a race. Hourly
    # admin/hospitality postings run ~20 days median time-to-fill and are
    # frequently evergreen/reposted, unlike competitive SWE internships that
    # can close within days of posting.
    schedule=modal.Period(hours=2),
    # Modal's default is 300s. A measured cold-start run (empty dedup index,
    # live LinkedIn, real model calls) landed at 219-262s — only 15-25% of
    # headroom, and the tail is fat: job volume swings run to run and a single
    # rate-limited detail fetch burns ~25s. Blowing the ceiling degrades
    # SILENTLY (the container is killed mid-loop, the canary only fires when
    # all 11 searches return zero, and the loss is biased toward the last
    # search terms), so buy the headroom. Still far under the 2h cron period.
    timeout=1500,
    secrets=[modal.Secret.from_name(os.environ.get("MODAL_SECRET_NAME", "job-alert-secrets-user_a"))],
)
def scrape():
    import sys
    import os
    sys.path.insert(0, "/app/scraper_user_a")
    os.chdir("/app/scraper_user_a")
    from main import run
    run()


@app.local_entrypoint()
def main():
    """Run one scrape immediately: modal run modal_app_user_a.py"""
    scrape.remote()
