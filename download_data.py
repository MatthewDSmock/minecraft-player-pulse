"""
Download the latest Minecraft Player Pulse dataset from GitHub Releases.

Usage:
    py download_data.py                    # downloads fresh copy (always)
    py download_data.py --skip-if-exists   # only download if file is missing

Downloads minecraft_sentiment.parquet from the data-v1.0 release tag and
writes it to data/processed/minecraft_sentiment.parquet.

This is the deployment-time data-fetch step: code lives in the repo, data
lives in a Release. Anyone who clones this repo runs this once to populate
the dataset, then runs the dashboard.

Override the source by setting the DATA_RELEASE_URL environment variable.

DEPLOYMENT NOTE: this script is called by Streamlit Cloud on every container
rebuild. To pick up new data on the deployed app, re-upload the parquet to
the GitHub release and trigger a redeploy (push any commit, or use the
Streamlit Cloud "Reboot app" button). The script will always re-fetch
unless --skip-if-exists is passed.
"""
import os
import sys
from pathlib import Path

import requests


# Pin to the data-v1.0 tag explicitly rather than 'latest', because 'latest'
# resolves to the most recent release regardless of type — if a code release
# is published, 'latest' starts pointing to it instead of the data release.
DEFAULT_URL = os.environ.get(
    "DATA_RELEASE_URL",
    "https://github.com/MatthewDSmock/minecraft-player-pulse/releases/download/data-v1.0/minecraft_sentiment.parquet",
)
OUTPUT_PATH = Path("data/processed/minecraft_sentiment.parquet")


def download(url: str = DEFAULT_URL, output: Path = OUTPUT_PATH) -> bool:
    """Download the parquet file. Returns True on success."""
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading from: {url}")
    try:
        # Cache-busting headers to bypass any CDN caching at the GitHub edge
        headers = {"Cache-Control": "no-cache", "Pragma": "no-cache"}
        with requests.get(url, stream=True, timeout=120, headers=headers) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            written = 0
            with open(output, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
                        if total:
                            pct = written * 100 / total
                            print(f"  {written / 1024 / 1024:.1f} MB / {total / 1024 / 1024:.1f} MB  ({pct:.0f}%)", end="\r")
        print()
        print(f"Downloaded {written / 1024 / 1024:.1f} MB to {output}")
        return True
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e}")
        if e.response.status_code == 404:
            print("\nThe release asset wasn't found. Common causes:")
            print("  - You haven't created a release with the 'data-v1.0' tag")
            print("  - The release exists but doesn't have a 'minecraft_sentiment.parquet' asset")
            print("  - The repo URL is wrong (current default targets MatthewDSmock/minecraft-player-pulse)")
            print("\nTo override, set DATA_RELEASE_URL environment variable to your release asset URL.")
        return False
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return False


def main():
    # Default behavior: always re-download. This guarantees that on every
    # Streamlit Cloud rebuild we pull fresh data from the GitHub release.
    # Pass --skip-if-exists to preserve the previous behavior (skip download
    # if a local file is already present — useful for local development).
    if "--skip-if-exists" in sys.argv and OUTPUT_PATH.exists():
        size_mb = OUTPUT_PATH.stat().st_size / 1024 / 1024
        print(f"Data already exists at {OUTPUT_PATH} ({size_mb:.1f} MB), skipping download.")
        return

    success = download()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
