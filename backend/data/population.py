"""Population demand layer.

Downloads the whole-India WorldPop 1 km raster at most once (shared by all
cities, ~18 MB), crops it to a city boundary, and caches the result as a tiny
per-city CSV (a few KB). The runtime pipeline only reads the CSV, so the raster
never needs to be kept around once each city's cache exists.
"""
import argparse
import os

import geopandas as gpd
import pandas as pd
import rasterio
import requests
from rasterio.mask import mask as rio_mask

from backend.config import CityConfig, city_config

INDIA_RASTER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "../../data/raw/india/ind_pop_1km.tif"
)
WORLDPOP_URL = "https://data.worldpop.org/GIS/Population_Density/Global_2000_2020_1km/2020/IND/ind_pd_2020_1km.tif"


def download_population_raster(path=INDIA_RASTER):
    """Download the shared whole-India raster once (~18 MB on disk)."""
    if os.path.exists(path):
        print(f"Population raster already cached: {path}")
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    print("Downloading WorldPop India raster...")
    r = requests.get(WORLDPOP_URL, stream=True)
    total = int(r.headers.get("content-length", 0))
    done = 0
    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / 1e6:.1f} / {total / 1e6:.1f} MB", end="")
    print(f"\n✓ Saved to {path}")
    return path


def _write_cache(cfg: CityConfig):
    """Crop the India raster to the city boundary and cache population cells as CSV."""
    boundary = gpd.read_file(cfg.boundary_path).to_crs(epsg=4326)
    geom = [boundary.union_all().__geo_interface__]
    with rasterio.open(INDIA_RASTER) as src:
        cropped, transform = rio_mask(src, geom, crop=True, nodata=-99999)
    data = cropped[0]

    rows = []
    for r in range(data.shape[0]):
        for c in range(data.shape[1]):
            val = data[r, c]
            if val > 0:
                lon, lat = rasterio.transform.xy(transform, r, c)
                rows.append((lon, lat, float(val)))
    df = pd.DataFrame(rows, columns=["lon", "lat", "population"])
    os.makedirs(cfg.raw_dir, exist_ok=True)
    df.to_csv(cfg.pop_cache_path, index=False)
    print(
        f"✓ Population cache saved: {cfg.pop_cache_path} "
        f"({len(df)} cells, ~{df['population'].sum():,.0f} people)"
    )


def load_population_grid(cfg: CityConfig):
    """Population cells (point centroids) in the city's UTM zone, from cache."""
    if not os.path.exists(cfg.pop_cache_path):
        print("No population cache — building it from the India raster...")
        download_population_raster()
        _write_cache(cfg)

    df = pd.read_csv(cfg.pop_cache_path)
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
    p.add_argument("--force", action="store_true", help="rebuild cache even if present")
    a = p.parse_args()
    cfg = city_config(a.city, a.place)
    if a.force and os.path.exists(cfg.pop_cache_path):
        os.remove(cfg.pop_cache_path)
    pop = load_population_grid(cfg)
    print(pop.head())
