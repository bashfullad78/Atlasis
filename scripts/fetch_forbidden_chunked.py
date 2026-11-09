"""Chunked forbidden-zone fetch for cities whose combined Overpass query
exceeds a mirror's response cap (overpass-api.de answers 413 to
Kraków-sized combined queries). Splits the city polygon into an ``n x n``
grid of disjoint clips, fetches each, and writes the same
``forbidden.gpkg`` that ``download_forbidden`` writes (same columns, same
atomic replace, same meta stamp) so the pipeline treats it as normal.

Usage: ``venv/bin/python scripts/fetch_forbidden_chunked.py --city krakow
--n 2`` (grid of 4; raise ``--n`` if a chunk still 413s).
"""
from __future__ import annotations

import argparse
import os
import sys

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.constraints.forbidden import (  # noqa: E402
    FORBIDDEN_TAGS, download_forbidden)
from backend.config import city_config  # noqa: E402


def fetch_forbidden_chunked(cfg, grid_n: int) -> None:
    from backend.data.overpass import overpass_call
    import osmnx as ox

    boundary = gpd.read_file(cfg.boundary_path)
    polygon = boundary.union_all()
    minx, miny, maxx, maxy = polygon.bounds

    chunks, empty = [], []
    for i in range(grid_n):
        for j in range(grid_n):
            part = box(
                minx + (maxx - minx) * i / grid_n,
                miny + (maxy - miny) * j / grid_n,
                minx + (maxx - minx) * (i + 1) / grid_n,
                miny + (maxy - miny) * (j + 1) / grid_n,
            ).intersection(polygon)
            if part.is_empty or part.area == 0:
                continue
            chunks.append(part)

    print(f"{cfg.slug}: fetching {FORBIDDEN_TAGS} over {len(chunks)} "
          f"disjoint clips (grid {grid_n}x{grid_n})")
    frames = []
    for k, part in enumerate(chunks, 1):
        feats = None
        for attempt, wait in enumerate((0, 60, 180), 1):  # Overpass "weather":
            if wait:                       # capacity windows open and close
                import time
                time.sleep(wait)
            try:
                feats = overpass_call(ox.features_from_polygon, part,
                                      tags=FORBIDDEN_TAGS)
                break
            except (ConnectionError, OSError) as e:
                print(f"  chunk {k} attempt {attempt}/3 failed: {str(e)[:160]}")
        if feats is None:
            raise ConnectionError(f"chunk {k} failed all 3 attempts")
        print(f"  chunk {k}/{len(chunks)}: {len(feats)} features")
        if len(feats):
            frames.append(feats)
        else:
            empty.append(k)
    if empty:
        print(f"  note: empty clips {empty} (allowed: clipped chunks with "
              "no tagged geometry)")

    if not frames:
        raise ValueError("No forbidden geometry found across all clips")
    features = pd.concat(frames, ignore_index=True)  # chunks are disjoint,
    # so no cross-chunk duplicates; dropping the (element, osmid) index is
    # harmless because download_forbidden persists geometry only.
    print(f"total: {len(features)} forbidden features "
          "(combined original query would carry the same set)")
    part_gpkg = f"{cfg.forbidden_path}.part.gpkg"
    features[["geometry"]].to_file(part_gpkg, driver="GPKG")

    from backend.config import atomic_replace, persist_meta, utc_now_iso
    atomic_replace(part_gpkg, cfg.forbidden_path)
    persist_meta(cfg, fetched_forbidden=utc_now_iso())
    print(f"Saved {cfg.forbidden_path} — rerun the city pipeline normally")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True)
    ap.add_argument("--place")
    ap.add_argument("--n", type=int, default=2, help="grid size per axis")
    a = ap.parse_args()
    fetch_forbidden_chunked(city_config(a.city, a.place), a.n)


if __name__ == "__main__":
    main()
