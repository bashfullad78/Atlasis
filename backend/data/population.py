"""Population demand layer.

Country-agnostic: crops the city out of the shared national WorldPop 1 km
population-DENSITY raster, converts density to people-per-cell exactly
(count = density x geodesic cell area), and caches the result as a small
per-city Parquet file. The runtime pipeline only reads the Parquet cache, so
the national raster can be deleted once all target cities of a country have
caches.
"""
import argparse
import os

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask as rio_mask

from backend.config import (CityConfig, city_config, persist_meta,
                            utc_now_iso)
from backend.data.worldpop import country_for_config, density_url, ensure_density_raster

NODATA = -99999.0
EARTH_RADIUS_KM = 6371.0088


def _window_to_cells(data, transform):
    """Density window -> DataFrame of populated cell centroids.

    Each raster cell is the WGS84 lat/lon box between its outer corners
    (constant degree steps), so its geodesic area in km^2 is
    R^2 * dlat_rad * dlon_rad * cos(lat_center). Multiplying density
    (people/km^2) by cell area gives people per cell.
    """
    data = np.asarray(data, dtype="float32")
    valid = np.isfinite(data) & (data > 0)
    if not valid.any():
        return pd.DataFrame(columns=["lon", "lat", "population"])

    rows, cols = np.nonzero(valid)
    dlon, dlat = abs(transform.a), abs(transform.e)
    lonc, latc = rasterio.transform.xy(transform, rows, cols)
    latc_rad = np.deg2rad(np.asarray(latc))
    area_km2 = (EARTH_RADIUS_KM ** 2) * np.deg2rad(dlat) * np.deg2rad(dlon) \
        * np.cos(latc_rad)

    counts = data[rows, cols] * area_km2.astype("float32")
    return pd.DataFrame({
        "lon": np.asarray(lonc),
        "lat": np.asarray(latc),
        "population": counts.astype("float32"),
    })


def _write_cache(cfg: CityConfig, iso3):
    """Crop the country raster to the boundary and cache cells as Parquet."""
    raster_path = ensure_density_raster(iso3)
    boundary = gpd.read_file(cfg.boundary_path).to_crs(epsg=4326)
    geom = [boundary.union_all().__geo_interface__]
    with rasterio.open(raster_path) as src:
        cropped, transform = rio_mask(src, geom, crop=True, filled=True,
                                      nodata=NODATA)
    df = _window_to_cells(cropped[0], transform)

    os.makedirs(cfg.raw_dir, exist_ok=True)
    df.to_parquet(cfg.pop_cache_path, index=False)
    # Record exactly which product produced these counts (provenance for
    # reproducibility; also persists iso_country on first build) and when,
    # so docs figures and the UI can report data-freshness.
    persist_meta(cfg, iso_country=iso3,
                 pop_product="worldpop_density_1km_2020",
                 pop_source=density_url(iso3),
                 fetched_population=utc_now_iso())
    print(
        f"✓ Population cache saved: {cfg.pop_cache_path} "
        f"({len(df)} cells, ~{df['population'].sum():,.0f} people, "
        f"{os.path.getsize(cfg.pop_cache_path) / 1024:.0f} KB)"
    )


def load_population_grid(cfg: CityConfig, force=False):
    """Population cells (point centroids, people per cell) in the city's UTM zone."""
    if force and os.path.exists(cfg.pop_cache_path):
        os.remove(cfg.pop_cache_path)

    legacy_csv = cfg.pop_cache_path.replace(".parquet", ".csv")
    if not os.path.exists(cfg.pop_cache_path) and os.path.exists(legacy_csv):
        raise RuntimeError(
            f"Found a v0.1 population cache: {legacy_csv}. It was built from a "
            f"population-DENSITY raster, so its values are not people counts. "
            f"Delete it to rebuild with the corrected conversion:\n"
            f"  rm {legacy_csv}"
        )

    if os.path.exists(cfg.pop_cache_path):
        df = pd.read_parquet(cfg.pop_cache_path)
    else:
        print("No population cache, building it from the country density raster...")
        iso3 = country_for_config(cfg)
        _write_cache(cfg, iso3)
        df = pd.read_parquet(cfg.pop_cache_path)

    gdf = gpd.GeoDataFrame(
        {"population": df["population"]},
        geometry=gpd.points_from_xy(df["lon"], df["lat"]),
        crs="EPSG:4326",
    ).to_crs(cfg.crs)
    print(f"✓ {len(gdf)} population cells loaded, total ~{df['population'].sum():,.0f} people")
    return gdf


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Build the population cache for a city.")
    p.add_argument("--city", default="mysuru")
    p.add_argument("--place")
    p.add_argument("--country", help="ISO 3166-1 alpha-3 code, e.g. IND, UGA, DEU")
    p.add_argument("--force", action="store_true", help="rebuild cache even if present")
    a = p.parse_args()

    cfg = city_config(a.city, a.place, country=a.country)
    load_population_grid(cfg, force=a.force)
