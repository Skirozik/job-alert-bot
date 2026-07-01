import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.0",
        "lxml>=5.1.0",
        "anthropic>=0.25.0",
        "supabase>=2.4.0",
        "python-dotenv>=1.0.0",
    )
    .add_local_dir("scraper", remote_path="/app/scraper")
    .add_local_file(
        "Candidate_Profile_and_Filters.md",
        remote_path="/app/Candidate_Profile_and_Filters.md",
    )
)

app = modal.App("job-alert-scraper", image=image)


@app.function(
    schedule=modal.Period(minutes=30),
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
