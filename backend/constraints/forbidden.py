"""Forbidden zones (water, parks, cemeteries...) that candidate sites must avoid."""
import argparse
import os

import geopandas as gpd
import osmnx as ox
import pandas as pd

from backend.config import CityConfig, city_config

FORBIDDEN_TAGS = [
    {"natural": "water"},
    {"waterway": True},
    {"landuse": "reservoir"},
    {"leisure": "park"},
    {"landuse": "cemetery"},
]


def download_forbidden(cfg: CityConfig):
    print("Downloading forbidden geometry...")
    os.makedirs(cfg.raw_dir, exist_ok=True)
    layers = []
    for tags in FORBIDDEN_TAGS:
        try:
            layer = ox.features_from_place(cfg.place, tags=tags)
            if not layer.empty:
                layers.append(layer[["geometry"]])
                print(f"  ✓ {tags}")
        except Exception as e:
            print(f"  ✗ {tags} — {e}")
    if layers:
        combined = pd.concat(layers, ignore_index=True)
        gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326") \
            .to_file(cfg.forbidden_path, driver="GPKG")
        print(f"Saved {len(combined)} forbidden zones")
    else:
        print("No forbidden geometry found")


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
