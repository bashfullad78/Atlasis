"""Atlasis entry point. Run from the repository root:

    python main.py --city mysuru
    python main.py --city kampala --place "Kampala, Uganda" --threshold 300

A new city bootstraps automatically: missing raw data (boundary, roads,
hospitals, forbidden zones, population cache) is fetched before the pipeline
runs. Warm-cache runs touch no network. Use --probe to preview what would be
downloaded, or --no-download to forbid any fetch.
"""
import argparse
import sys

from backend.config import city_config
from backend.data.prepare import ensure_data, missing_artifacts, probe_data
from backend.pipeline import run_pipeline


def main():
    p = argparse.ArgumentParser(description="Recommend new facility sites for a city.")
    p.add_argument("--city", default="mysuru", help="city slug (data dir under data/raw/)")
    p.add_argument("--place", help="geocodable place name, any country")
    p.add_argument("--country", help="ISO 3166-1 alpha-3 code, e.g. IND, UGA, DEU "
                                     "(auto-resolved from geocoding when omitted)")
    p.add_argument("--spacing", type=int, help="candidate grid spacing in meters")
    p.add_argument("--top-n", type=int, help="number of sites to report")
    p.add_argument("--threshold", type=int, help="drive-time guarantee in seconds (default 900)")
    p.add_argument("--probe", action="store_true",
                   help="report which data exists / would be downloaded, then exit")
    p.add_argument("--no-download", action="store_true",
                   help="never fetch data; fail if anything is missing")
    a = p.parse_args()

    cfg = city_config(a.city, a.place, country=a.country)

    if a.probe:
        sys.exit(1 if probe_data(cfg) else 0)

    if a.no_download:
        missing = missing_artifacts(cfg)
        if missing:
            sys.exit(
                f"Missing {', '.join(missing)} for '{cfg.slug}' and --no-download "
                f"is set. Rerun without it (or with --probe) to fetch them."
            )
    else:
        ensure_data(cfg)

    run_pipeline(cfg, spacing_m=a.spacing, top_n=a.top_n, threshold=a.threshold)


if __name__ == "__main__":
    main()
