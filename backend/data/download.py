"""Per-city downloads: boundary, road graph, existing facilities, CRS metadata."""
import argparse
import json
import os

import geopandas as gpd
import osmnx as ox

from backend.config import CityConfig, city_config, utm_epsg_from_lonlat


def download_all(cfg: CityConfig):
    os.makedirs(cfg.raw_dir, exist_ok=True)

    print(f"Downloading boundary for {cfg.place}...")
    boundary = ox.geocode_to_gdf(cfg.place)
    boundary.to_file(cfg.boundary_path, driver="GPKG")

    c = boundary.union_all().centroid
    epsg = utm_epsg_from_lonlat(c.x, c.y)
    with open(cfg.meta_path, "w") as f:
        json.dump({"slug": cfg.slug, "place": cfg.place, "utm_epsg": epsg,
                   "network_type": cfg.network_type}, f, indent=2)
    print(f"✓ Boundary + meta saved (UTM EPSG:{epsg})")

    print(f"Downloading road network ({cfg.network_type})...")
    graph = ox.graph_from_place(cfg.place, network_type=cfg.network_type)
    ox.save_graphml(graph, filepath=cfg.roads_path)
    print("✓ Roads saved")

    print(f"Downloading existing facilities ({cfg.facility_tags})...")
    facilities = ox.features_from_place(cfg.place, tags=cfg.facility_tags)
    cols = [c for c in ["name", "geometry"] if c in facilities.columns]
    facilities[cols].to_file(cfg.hospitals_path, driver="GPKG")
    print(f"✓ {len(facilities)} facilities saved")

    print("All data downloaded.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Download boundary, roads and facilities for a city.")
    p.add_argument("--city", default="mysuru")
    p.add_argument("--place", help="geocodable place name (overrides registry)")
    a = p.parse_args()
    download_all(city_config(a.city, a.place))
