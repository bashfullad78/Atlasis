"""Reclaim disk space: identify and remove safely re-derivable artifacts.

Everything here is reproducible from committed commands (README documents
them), so deleting these never loses information:

* ``data/processed/<city>/top_sites.gpkg`` -> rerun the pipeline
* orphaned ``*.part`` / ``*.part.gpkg``    -> interrupted downloads, dead weight
* ``data/raw/worldpop/<ISO3>/*.tif``       -> once EVERY city of that country
  has a population.parquet cache, the shared national raster is spent: each
  cache is self-contained. Re-downloading costs one command (the density
  rasters are small: 1-45 MB per country).

The command reports what it would do first; pass --apply to delete.
"""
import argparse
import glob
import json
import os

from backend.config import DATA_DIR, city_config
from backend.routing.graph import has_slim

PROCESSED_DIR = os.path.join(DATA_DIR, "processed")
RAW_DIR = os.path.join(DATA_DIR, "raw")


def find_processed_outputs():
    """All generated per-city results (re-derivable via one pipeline run)."""
    return sorted(glob.glob(os.path.join(PROCESSED_DIR, "*", "*.gpkg")))


def find_orphan_parts():
    """Interrupted-download leftovers anywhere under data/."""
    hits = []
    for pattern in ("*.part", "*.part.gpkg"):
        hits.extend(glob.glob(os.path.join(DATA_DIR, "**", pattern), recursive=True))
    return sorted(hits)


def find_superseded_graphml():
    """roads.graphml files already converted to the slim Parquet format."""
    hits = []
    for d in sorted(glob.glob(os.path.join(RAW_DIR, "*"))):
        if not os.path.isdir(d) or os.path.basename(d) == "worldpop":
            continue
        gm = os.path.join(d, "roads.graphml")
        if not os.path.exists(gm):
            continue
        if has_slim(city_config(os.path.basename(d))):
            hits.append(gm)
    return hits


def find_spent_rasters():
    """National density rasters whose every known city already has a cache.

    A raster is spent when no raw city directory of that country is missing
    its population.parquet. Note this is conservative: a new city of the same
    country added later re-downloads the raster (small, one command).
    """
    worldpop = os.path.join(RAW_DIR, "worldpop")
    if not os.path.isdir(worldpop):
        return []
    spent = []
    for iso_dir in sorted(glob.glob(os.path.join(worldpop, "*"))):
        iso3 = os.path.basename(iso_dir)
        tifs = glob.glob(os.path.join(iso_dir, "*.tif"))
        if not tifs:
            continue
        city_dirs = [d for d in glob.glob(os.path.join(RAW_DIR, "*"))
                     if os.path.isdir(d) and os.path.basename(d) != "worldpop"]
        missing_cache = []
        for d in city_dirs:
            meta_path = os.path.join(d, "meta.json")
            if not os.path.exists(meta_path):
                continue  # half-built city (interrupted bootstrap): no raster owed yet
            try:
                with open(meta_path) as f:
                    iso_country = json.load(f).get("iso_country")
            except ValueError:
                print(f"  (skipping {d}: unreadable meta.json)")
                continue
            if iso_country == iso3 \
                    and not os.path.exists(os.path.join(d, "population.parquet")):
                missing_cache.append(d)
        if not missing_cache:
            spent.extend(tifs)
    return spent


def _size(path):
    return os.path.getsize(path) / 1e6


def plan():
    """Everything safe to delete, with reasons."""
    plan = [("processed output", p, _size(p)) for p in find_processed_outputs()]
    plan += [("orphaned .part", p, _size(p)) for p in find_orphan_parts()]
    plan += [("superseded graphml", p, _size(p))
             for p in find_superseded_graphml()]
    plan += [("spent national raster", p, _size(p))
             for p in find_spent_rasters()]
    return plan


def main():
    p = argparse.ArgumentParser(
        description="Report (and optionally delete) re-derivable data artifacts.")
    p.add_argument("--apply", action="store_true",
                   help="actually delete (default: report only)")
    p.add_argument("--only", metavar="KIND",
                   help="restrict to one kind: 'processed output', 'orphaned .part', "
                        "'superseded graphml', or 'spent national raster'")
    a = p.parse_args()

    items = plan()
    if a.only:
        skipped = [i for i in items if i[0] != a.only]
        items = [i for i in items if i[0] == a.only]
        if skipped:
            mb = sum(s for _, _, s in skipped)
            print(f"Skipping {len(skipped)} non-matching artifacts ({mb:.1f} MB) "
                  f"because --only '{a.only}' is set.")
    if not items:
        print("Nothing to clean: no re-derivable artifacts found.")
        return

    total = 0.0
    for kind, path, mb in items:
        print(f"  [{kind}] {path} ({mb:.1f} MB)")
        total += mb
    print(f"Total reclaimable: {total:.1f} MB")

    if not a.apply:
        print("Dry run only. Rerun with --apply to delete.")
        return

    for _, path, _ in items:
        os.remove(path)
    # Tidy now-empty processed/<city>/ dirs.
    for d in glob.glob(os.path.join(PROCESSED_DIR, "*")):
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
    print(f"✓ Deleted {len(items)} artifacts, reclaimed {total:.1f} MB")


if __name__ == "__main__":
    main()
