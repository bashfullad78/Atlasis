"""One command city bootstrap: ensure every raw artifact exists.

``ensure_data(cfg)`` walks the artifact checklist and runs only the builders
whose outputs are missing, so a fresh city is fetched end to end while warm
caches trigger zero network calls. Each step is idempotent and resumable:
if a step fails (say, an Overpass timeout), rerunning continues where it left
off instead of redownloading what is already on disk.

Artifacts, in dependency order:

1. ``meta.json`` + ``boundary.gpkg``  -- geocode_boundary (Nominatim, once)
2. ``roads.graphml``                  -- download_roads   (Overpass)
3. ``hospitals.gpkg``                 -- download_hospitals (Overpass)
4. ``forbidden.gpkg``                 -- download_forbidden (Overpass)
5. ``population.parquet``             -- load_population_grid (WorldPop)

Overpass backed steps retry with exponential backoff. ``probe_data`` reports
what exists and the expected download sizes before any bytes are fetched.
"""
import os
import time

import requests

from backend.config import CityConfig, atomic_replace
from backend.constraints.forbidden import download_forbidden
from backend.data.download import (download_hospitals, download_roads,
                                   geocode_boundary)
from backend.data.population import load_population_grid
from backend.data.worldpop import country_for_config, density_raster_path
from backend.routing.graph import has_slim

OVERPASS_RETRIES = 3
OVERPASS_BACKOFF_S = 20  # server side rate limits; be conservative

_STEPS = [
    ("boundary.gpkg", "boundary"),
    ("roads graph", "roads"),
    ("hospitals.gpkg", "hospitals"),
    ("forbidden.gpkg", "forbidden"),
    ("population.parquet", "population"),
]


def _have(cfg: CityConfig, kind: str) -> bool:
    if kind == "roads":
        return has_slim(cfg) or os.path.exists(cfg.roads_path)
    paths = {
        "boundary": cfg.boundary_path,
        "hospitals": cfg.hospitals_path,
        "forbidden": cfg.forbidden_path,
        "population": cfg.pop_cache_path,
    }
    return os.path.exists(paths[kind])


def missing_artifacts(cfg: CityConfig):
    """Artifact kinds that still need building, in dependency order."""
    return [kind for _, kind in _STEPS if not _have(cfg, kind)]


def _retry(fn, *args, retries=OVERPASS_RETRIES, backoff_s=OVERPASS_BACKOFF_S):
    """Retry a network step with linear backoff; reraise after the last try.

    Only network failures are retried (requests errors plus socket-level
    OSError, which osmnx/Overpass timeouts surface as). Deliberate
    ValueErrors ("No facilities tagged...", "No forbidden geometry found")
    are permanent answers from the server: retrying them wastes a minute
    and hammers the free shared service.
    """
    for attempt in range(1, retries + 1):
        try:
            return fn(*args)
        except (requests.RequestException, OSError) as e:
            if attempt == retries:
                raise
            wait = backoff_s * attempt
            print(f"  ✗ attempt {attempt}/{retries} failed ({e}); "
                  f"retrying in {wait}s...")
            time.sleep(wait)


def ensure_data(cfg: CityConfig, probe=False, skip_forbidden=False):
    """Fetch every missing raw artifact for the city; print a plan first.

    Warm-cache runs make zero network calls. Returns the list of artifact
    kinds that were built by this call. ``skip_forbidden`` defers the
    forbidden-zones step (Overpass instability workaround); without it the
    pipeline later refuses to run its forbidden filter.
    """
    needed = missing_artifacts(cfg)
    if skip_forbidden and "forbidden" in needed:
        needed.remove("forbidden")
        print("--skip-forbidden set: deferring forbidden zones; the pipeline "
              "will refuse its forbidden filter until they are fetched.")
    if not needed:
        print("All data cached, nothing to download.")
        return []
    print(f"Bootstrapping '{cfg.slug}': building {', '.join(needed)}")

    built = []
    boundary = None

    if "boundary" in needed:
        boundary = geocode_boundary(cfg)  # persists meta.json facts
        os.makedirs(cfg.raw_dir, exist_ok=True)
        part = f"{cfg.boundary_path}.part.gpkg"  # .gpkg keeps driver dispatch happy
        boundary.to_file(part, driver="GPKG")
        atomic_replace(part, cfg.boundary_path)
        built.append("boundary")
        needed = missing_artifacts(cfg)  # re-check: meta/boundary now exist
        if skip_forbidden and "forbidden" in needed:
            needed.remove("forbidden")

    # Geocode once, reuse the polygon for every Overpass step.
    if boundary is None and any(k in needed for k in ("roads", "hospitals", "forbidden")):
        boundary = gpd_read_boundary(cfg)

    for kind, step in (("roads", download_roads),
                       ("hospitals", download_hospitals),
                       ("forbidden", download_forbidden)):
        if kind in needed:
            _retry(step, cfg, boundary)
            built.append(kind)

    if "population" in needed:
        load_population_grid(cfg)  # country resolution + download + crop
        built.append("population")

    return built


def gpd_read_boundary(cfg):
    import geopandas as gpd
    return gpd.read_file(cfg.boundary_path)


def probe_data(cfg: CityConfig):
    """Print what exists and expected download sizes; fetch nothing."""
    needed = missing_artifacts(cfg)
    print(f"Probe for '{cfg.slug}' ({cfg.place}):")
    for label, kind in _STEPS:
        status = "cached" if _have(cfg, kind) else "MISSING"
        print(f"  {label:<20} {status}")
    if "population" in needed:
        try:
            path = density_raster_path(country_for_config(cfg))
            size = os.path.getsize(path) / 1e6 if os.path.exists(path) else None
            if size is not None:
                print(f"  population: national raster already cached ({size:.1f} MB)")
            else:
                print("  population: national density raster will be downloaded "
                      "(~1-45 MB depending on country; shared across its cities)")
        except (ValueError, OSError) as e:
            # Country unresolved (e.g. offline, ambiguous place). The probe
            # must still work: size reporting is best effort.
            print(f"  population: density raster will be downloaded "
                  f"(size unknown: {e})")
    if not needed:
        print("Nothing to download: pipeline can run fully offline.")
    return needed
