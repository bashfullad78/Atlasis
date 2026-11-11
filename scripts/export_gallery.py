"""Precompute the demo gallery: one jobs-API-shaped result payload per
cached city, written to ``web/gallery/<slug>.json`` (+ ``index.json``).

The web UI lists these as "Saved results" Warm-cache only (never fetches): a city is skipped unless every artifact
is present. 
Usage: ``venv/bin/python scripts/export_gallery.py [--city freiburg] ...``
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GALLERY_DIR = os.path.join(ROOT, "web", "gallery")
THRESHOLD = 300   
NUM_SITES = 5     
LABELS = {
    "freiburg": "Freiburg im Breisgau",
    "heidelberg": "Heidelberg",
    "krakow": "Kraków",
    "mysuru": "Mysuru",
    "kolkata": "Kolkata",
    "patna": "Patna",
    "guwahati": "Guwahati",
    "kampala": "Kampala",
    "testville": "Testville (synthetic)",
}


def export_city(slug: str) -> None:
    from backend.api import runner
    from backend.config import city_config

    runner._bind_loaders()
    cfg = city_config(slug)
    params = {"place": slug, "city": slug, "country": None,
              "threshold": THRESHOLD, "num_sites": NUM_SITES}
    t0 = time.perf_counter()
    res = runner._RUN_PIPELINE(cfg, threshold=THRESHOLD, num_sites=NUM_SITES,
                               mode="greedy", skip_forbidden=False)
    payload = runner.result_payload(cfg, params, res)
    out = os.path.join(GALLERY_DIR, f"{slug}.json")
    with open(out, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    mb = os.path.getsize(out) / 1e6
    print(f"  {slug}: baseline {payload['baseline_pct']}% -> joint "
          f"{payload['joint_pct']}%, payload {mb:.1f} MB "
          f"({time.perf_counter() - t0:.0f}s)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", action="append", default=[],
                    help="export only these slugs (default: every cached city)")
    a = ap.parse_args()

    proc_dir = os.path.join(ROOT, "data", "processed")
    slugs = a.city or sorted(
        d for d in (os.listdir(proc_dir) if os.path.isdir(proc_dir) else [])
        if os.path.isfile(os.path.join(ROOT, "data", "raw", d, "boundary.gpkg"))
        and os.path.isfile(os.path.join(proc_dir, d, "top_sites.gpkg"))
    )
    os.makedirs(GALLERY_DIR, exist_ok=True)

    entries = []
    for slug in slugs:
        try:
            export_city(slug)
            entries.append({"city": slug, "label": LABELS.get(slug, slug.title()),
                            "threshold": THRESHOLD, "num_sites": NUM_SITES})
        except Exception as e:  # noqa: BLE001 - skip gaps, keep the gallery honest
            print(f"  {slug}: SKIPPED ({type(e).__name__}: {e})")

    with open(os.path.join(GALLERY_DIR, "index.json"), "w") as f:
        json.dump({"threshold": THRESHOLD, "num_sites": NUM_SITES,
                   "cities": entries}, f, indent=1)
    print(f"gallery manifest: {len(entries)} cities")


if __name__ == "__main__":
    main()
