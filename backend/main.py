"""Atlasis entry point. Run from the repository root:

    python main.py --city mysuru
    python main.py --city kampala --place "Kampala, Uganda" --threshold 300

A new city bootstraps automatically: missing raw data (boundary, roads,
hospitals, forbidden zones, population cache) is fetched before the pipeline
runs. Warm-cache runs touch no network. Use --probe to preview what would be
downloaded, or --no-download to forbid any fetch.
"""
import argparse
import json
import sys
from datetime import datetime

from backend.config import (city_config, newest_data_time,
                            FIGURE_MANIFEST_PATH)
from backend.data.prepare import ensure_data, missing_artifacts, probe_data
from backend.pipeline import run_pipeline


def warn_if_doc_figures_stale(cfg):
    """One-line note when refreshed city data is newer than the case-study
    figures (roads/hospitals change on OSM between the doc run and now)."""
    try:
        with open(FIGURE_MANIFEST_PATH) as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return
    rendered = manifest.get("rendered_at")
    if rendered is None or cfg.slug not in manifest.get("cities", {}):
        return
    newest = newest_data_time(cfg)
    if newest is None:
        return
    days = (newest - datetime.fromisoformat(rendered)).days
    if days >= 0:
        print(f"note: data for '{cfg.slug}' is {days} day(s) newer than the "
              f"docs/case-study figures ({rendered[:10]}); re-render them if "
              f"headline figures should be refreshed.")


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
    p.add_argument("--skip-forbidden", action="store_true",
                   help="run without the forbidden-zones filter and defer fetching "
                        "it (Overpass instability workaround; results not comparable "
                        "to filtered runs)")
    p.add_argument("--num-sites", type=int, default=None,
                   help="jointly select this many sites (MCLP; default 1, which "
                        "matches the independent ranking)")
    p.add_argument("--mode", choices=["greedy", "exact", "both"], default="greedy",
                   help="MCLP solver: greedy (>= 1-1/e guarantee), exact (PuLP/CBC "
                        "MILP benchmark), or both (reports the greedy gap)")
    p.add_argument("--uncertainty", action="store_true",
                   help="report jackknife + bootstrap sensitivity intervals for the "
                        "headline figures (total population, baseline coverage, "
                        "best gain, MCLP objective)")
    p.add_argument("--n-boot", type=int, default=200,
                   help="bootstrap replicates for --uncertainty (default 200)")
    p.add_argument("--seed", type=int, default=0,
                   help="RNG seed for --uncertainty bootstrap (default 0)")
    a = p.parse_args()

    # Fail fast on bad values instead of after minutes of fetching/scoring.
    for flag, value, lo in (("--top-n", a.top_n, 1), ("--threshold", a.threshold, 1),
                            ("--spacing", a.spacing, 50),
                            ("--num-sites", a.num_sites, 1),
                            ("--n-boot", a.n_boot, 1)):
        if value is not None and value < lo:
            p.error(f"{flag} must be >= {lo}, got {value}")

    cfg = city_config(a.city, a.place, country=a.country)

    if a.probe:
        sys.exit(1 if probe_data(cfg) else 0)

    if a.no_download:
        missing = missing_artifacts(cfg)
        if a.skip_forbidden:
            missing = [m for m in missing if m != "forbidden"]
        if missing:
            sys.exit(
                f"Missing {', '.join(missing)} for '{cfg.slug}' and --no-download "
                f"is set. Rerun without it (or with --probe) to fetch them."
            )
    else:
        ensure_data(cfg, skip_forbidden=a.skip_forbidden)
        warn_if_doc_figures_stale(cfg)

    run_pipeline(cfg, spacing_m=a.spacing, top_n=a.top_n, threshold=a.threshold,
                 skip_forbidden=a.skip_forbidden, num_sites=a.num_sites,
                 mode=a.mode, uncertainty=a.uncertainty, n_boot=a.n_boot,
                 seed=a.seed)


if __name__ == "__main__":
    main()
