import modal

# Read dependencies from requirements.txt directly instead of a separately
# maintained list here — a hardcoded duplicate silently drifted out of sync
# with requirements.txt (anthropic>=0.25.0 vs >=0.40.0, needed for the
# tool_choice/cache_control classifier features), and Modal bakes
# dependencies into the image at deploy time, so a stale pin here means an
# already-deployed Modal app keeps running an old anthropic SDK indefinitely
# even after the source code itself is redeployed.
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("scraper/requirements.txt")
    .add_local_dir("scraper", remote_path="/app/scraper")
    .add_local_file(
        "Candidate_Profile_and_Filters.md",
        remote_path="/app/Candidate_Profile_and_Filters.md",
    )
)

app = modal.App("job-alert-scraper", image=image)


@app.function(
    schedule=modal.Period(minutes=20),
    secrets=[modal.Secret.from_name("job-alert-secrets")],
)
def scrape():
    import sys
    import os
    sys.path.insert(0, "/app/scraper")
    os.chdir("/app/scraper")
    from main import run
    run()


@app.local_entrypoint()
def main():
    """Run one scrape immediately: modal run modal_app.py"""
    scrape.remote()
