"""
Download the latest Minecraft Player Pulse dataset from GitHub Releases.

Usage:
    py download_data.py

Downloads minecraft_sentiment.parquet from the latest release tagged 'data-*'
and writes it to data/processed/minecraft_sentiment.parquet.

This is the deployment-time data-fetch step: code lives in the repo, data
lives in a Release. Anyone who clones this repo runs this once to populate
the dataset, then runs the dashboard.

Override the source by setting the DATA_RELEASE_URL environment variable.
"""

import os
import sys
from pathlib import Path

import requests

# Default release asset URL — update this after you upload the first release
DEFAULT_URL = os.environ.get(
    "DATA_RELEASE_URL",
    "https://github.com/MatthewDSmock/minecraft-player-pulse/releases/latest/download/minecraft_sentiment.parquet",
)

OUTPUT_PATH = Path("data/processed/minecraft_sentiment.parquet")


def download(url: str = DEFAULT_URL, output: Path = OUTPUT_PATH) -> bool:
    """Download the parquet file. Returns True on success."""
    output.parent.mkdir(parents=True, exist_ok=True)

    print(f"Downloading from: {url}")
    try:
        with requests.get(url, stream=True, timeout=120) as r:
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
            print("  - You haven't created a release yet")
            print("  - The release exists but doesn't have a 'minecraft_sentiment.parquet' asset")
            print("  - The repo URL is wrong (current default targets MatthewDSmock/minecraft-player-pulse)")
            print("\nTo override, set DATA_RELEASE_URL environment variable to your release asset URL.")
        return False

    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return False


def main():
    if OUTPUT_PATH.exists() and "--force" not in sys.argv:
        size_mb = OUTPUT_PATH.stat().st_size / 1024 / 1024
        print(f"Data already exists at {OUTPUT_PATH} ({size_mb:.1f} MB)")
        print("Pass --force to re-download.")
        return

    success = download()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
