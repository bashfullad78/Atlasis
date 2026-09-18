"""Per-city downloads: boundary, road graph, existing facilities, CRS metadata.

All steps take an optional pre-geocoded boundary GeoDataFrame so an
orchestrator can geocode once and reuse the result (Nominatim is rate
limited). Files are written atomically (``<name>.part`` + rename) so an
interrupted download can never be mistaken for a complete artifact.
"""
import argparse
import os

import geopandas as gpd
import osmnx as ox

from backend.config import CityConfig, city_config, persist_meta, utm_epsg_from_lonlat
from backend.data.overpass import overpass_call
from backend.data.worldpop import iso3_from_place, to_iso3
from backend.routing.graph import save_graph_slim


def _resolve_iso3(boundary, place):
    """ISO 3166-1 alpha-3 of the boundary from geocoding metadata."""
    addr = boundary.iloc[0].get("address") or {}
    code = to_iso3(addr.get("country_code"))
    return code or iso3_from_place(place)


def _atomic_gdf_save(gdf, path):
    """Write a GeoDataFrame to GPKG via a temp file + atomic rename."""
    part = f"{path}.part.gpkg"  # .gpkg keeps driver dispatch happy
    gdf.to_file(part, driver="GPKG")
    os.replace(part, path)


def geocode_boundary(cfg: CityConfig):
    """Geocode the place once; returns the boundary GeoDataFrame.

    Also persists slug/place/UTM EPSG/iso_country into meta.json, so later
    stages and future runs need no network for these facts.
    """
    print(f"Geocoding boundary for {cfg.place}...")
    boundary = ox.geocode_to_gdf(cfg.place)
    if boundary.empty:
        raise ValueError(f"Could not geocode '{cfg.place}'. Check the place string.")

    c = boundary.union_all().centroid
    epsg = utm_epsg_from_lonlat(c.x, c.y)
    iso3 = _resolve_iso3(boundary, cfg.place)
    persist_meta(cfg, slug=cfg.slug, place=cfg.place, utm_epsg=epsg,
                 iso_country=iso3, network_type=cfg.network_type)
    print(f"✓ Boundary + meta saved (UTM EPSG:{epsg}, country {iso3 or 'unresolved'})")
    return boundary


def download_roads(cfg: CityConfig, boundary=None):
    """Overpass road network for the boundary polygon, saved in slim format."""
    if boundary is None:
        boundary = gpd.read_file(cfg.boundary_path)
    print(f"Downloading road network ({cfg.network_type})...")
    graph = overpass_call(
        ox.graph_from_polygon, boundary.union_all(), network_type=cfg.network_type
    )
    save_graph_slim(graph, cfg)  # ~10x smaller than GraphML; graph-level only tags kept


def download_hospitals(cfg: CityConfig, boundary=None):
    """Overpass facility features for the boundary polygon, saved atomically."""
    if boundary is None:
        boundary = gpd.read_file(cfg.boundary_path)
    print(f"Downloading existing facilities ({cfg.facility_tags})...")
    facilities = overpass_call(
        ox.features_from_polygon, boundary.union_all(), tags=cfg.facility_tags
    )
    # Keep the curation-relevant tags, not just names: structural signals
    # (healthcare/beds/building) are language-independent evidence used by
    # gis.hospitals._curate; amenity/operator help per-city review.
    cols = [c for c in ["name", "amenity", "healthcare", "beds", "building",
                        "operator", "geometry"] if c in facilities.columns]
    if facilities.empty:
        raise ValueError(
            f"No facilities tagged {cfg.facility_tags} found for '{cfg.place}'. "
            f"The pipeline needs at least one existing facility."
        )
    _atomic_gdf_save(facilities[cols], cfg.hospitals_path)
    print(f"✓ {len(facilities)} facilities saved")


def download_all(cfg: CityConfig):
    """Boundary + roads + hospitals (kept for backward compatibility)."""
    os.makedirs(cfg.raw_dir, exist_ok=True)
    boundary = geocode_boundary(cfg)
    _atomic_gdf_save(boundary, cfg.boundary_path)
    download_roads(cfg, boundary=boundary)
    download_hospitals(cfg, boundary=boundary)
    print("All data downloaded.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Download boundary, roads and facilities for a city.")
    p.add_argument("--city", default="mysuru")
    p.add_argument("--place", help="geocodable place name (overrides registry)")
    p.add_argument("--country", help="ISO 3166-1 alpha-3 code, e.g. IND, UGA, DEU")
    a = p.parse_args()
    download_all(city_config(a.city, a.place, country=a.country))
