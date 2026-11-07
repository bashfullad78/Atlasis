"""Forbidden zones (water, parks, cemeteries...) that candidate sites must avoid."""
import argparse
import os

import geopandas as gpd
import osmnx as ox

from backend.config import (CityConfig, atomic_replace, city_config,
                            persist_meta, utc_now_iso)
from backend.data.overpass import overpass_call

# One combined Overpass query: osmnx unions tag groups within a single
# request (list values group under one key). One request instead of five
# sequential ones removes four independent 180 s hang risks under Overpass
# instability (v0.2 lost three working windows to exactly this step).
FORBIDDEN_TAGS = {
    "natural": "water",
    "waterway": True,
    "landuse": ["reservoir", "cemetery"],
    "leisure": "park",
}


def download_forbidden(cfg: CityConfig, boundary=None):
    """Fetch all forbidden layers in one Overpass request, saved atomically.

    No per-layer try/except: a partially fetched forbidden cache silently
    weakens the filter (candidates near an unfetched layer would pass), so
    the fetch is all-or-nothing and retried/failover as a unit.
    """
    print(f"Downloading forbidden zones in one combined query: {FORBIDDEN_TAGS}")
    os.makedirs(cfg.raw_dir, exist_ok=True)
    if boundary is None:
        if not os.path.exists(cfg.boundary_path):
            raise FileNotFoundError(
                f"No boundary for '{cfg.slug}'. Run the download step (or the "
                f"bootstrap) first: python -m backend.main --city {cfg.slug} --place ..."
            )
        boundary = gpd.read_file(cfg.boundary_path)
    polygon = boundary.union_all()

    features = overpass_call(ox.features_from_polygon, polygon, tags=FORBIDDEN_TAGS)
    if features.empty:
        # Refuse to write an empty cache: an empty union here would silently
        # disable the forbidden filter for every future run.
        raise ValueError(
            "No forbidden geometry found for this city; the forbidden filter "
            "would be a no-op. Run without forbidden zones by removing "
            "data/raw/<city>/forbidden.gpkg if that is intentional."
        )
    part = f"{cfg.forbidden_path}.part.gpkg"  # .gpkg keeps driver dispatch happy
    features[["geometry"]].to_file(part, driver="GPKG")
    atomic_replace(part, cfg.forbidden_path)
    persist_meta(cfg, fetched_forbidden=utc_now_iso())
    print(f"Saved {len(features)} forbidden zones")


def load_forbidden(cfg: CityConfig):
    if not os.path.exists(cfg.forbidden_path):
        raise FileNotFoundError("Forbidden zones not found. Run download_forbidden first.")
    return gpd.read_file(cfg.forbidden_path).to_crs(cfg.crs)


def filter_forbidden(candidates, cfg: CityConfig):
    forbidden_union = load_forbidden(cfg).union_all()
    mask = candidates.geometry.apply(lambda p: not p.within(forbidden_union))
    filtered = candidates.loc[mask].copy()
    print(f"After forbidden geometry filter: {len(filtered)} / {len(candidates)} candidates remain")
    return filtered


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Download forbidden zones for a city.")
    p.add_argument("--city", default="mysuru")
    p.add_argument("--place")
    a = p.parse_args()
    download_forbidden(city_config(a.city, a.place))
