"""Atlasis entry point. Run from the repository root:

    python main.py --city mysuru
    python main.py --city bengaluru --place "Bengaluru, Karnataka, India" --threshold 300
"""
import argparse

from backend.config import city_config
from backend.pipeline import run_pipeline


def main():
    p = argparse.ArgumentParser(description="Recommend new facility sites for a city.")
    p.add_argument("--city", default="mysuru", help="city slug (data dir under data/raw/)")
    p.add_argument("--place", help="geocodable place name (overrides built-in registry)")
    p.add_argument("--spacing", type=int, help="candidate grid spacing in meters")
    p.add_argument("--top-n", type=int, help="number of sites to report")
    p.add_argument("--threshold", type=int, help="drive-time guarantee in seconds (default 900)")
    a = p.parse_args()

    cfg = city_config(a.city, a.place)
    run_pipeline(cfg, spacing_m=a.spacing, top_n=a.top_n, threshold=a.threshold)


if __name__ == "__main__":
    main()
