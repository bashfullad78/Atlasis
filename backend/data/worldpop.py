"""WorldPop country-agnostic population layer.

Resolves a city to its ISO 3166-1 alpha-3 country code and manages the shared
national WorldPop 1 km population-DENSITY raster for that country.

Why the density product (and not the 100 m count raster)? Two server facts,
verified 2026-09:

* data.worldpop.org advertises ``Accept-Ranges: bytes`` but ignores Range
  headers on GET (returns 200 with the full body, never 206), so GDAL
  /vsicurl windowed reads are impossible. The 100 m count rasters would have
  to be downloaded whole (India 1.8 GB, Brazil 4.4 GB).
* The 1 km density rasters are small (India 18 MB, Uganda 1.1 MB,
  Germany 3 MB, Brazil 43 MB) and are shared by every city in a country.

The v0.1 bug (density values treated as people counts) is fixed exactly: the
grids are WGS84 with constant degree steps, so a cell's geodesic area is a
pure function of its latitude, and ``count = density * area_km2`` (see
``population._window_to_cells``).

Dataset: WorldPop Global 2000-2020 population density, 1 km.
  https://www.worldpop.org/datacatalog/
"""
import os
import unicodedata

import pycountry
import requests

from backend.config import DATA_DIR, load_meta, persist_meta

WP_BASE = "https://data.worldpop.org"
WP_DENSITY_DATASET = "GIS/Population_Density/Global_2000_2020_1km/2020"
WP_POYEAR = "2020"

# Nominatim addressdetails=1, format jsonv2, single result.
_NOMINATIM_PARAMS = {"format": "jsonv2", "addressdetails": 1, "limit": 1}


def to_iso3(code):
    """Normalize a 2- or 3-letter country code to ISO 3166-1 alpha-3."""
    if not code:
        return None
    code = str(code).strip().upper()
    c = pycountry.countries.get(alpha_2=code) or pycountry.countries.get(alpha_3=code)
    return c.alpha_3 if c else None


def iso3_from_place(place):
    """ISO 3166-1 alpha-3 code for a geocoded place, or None.

    Nominatim returns an 'address' dict per result only when queried with
    addressdetails=1, which osmnx.geocode() does. Falls back to searching the
    display_name's trailing country name via pycountry.
    """
    from osmnx._nominatim import _nominatim_request

    results = _nominatim_request(params={"q": place, **_NOMINATIM_PARAMS})
    if not results:
        return None
    r = results[0]
    code = to_iso3((r.get("address") or {}).get("country_code"))
    if code:
        return code

    # Fallback: match a country name in the display_name, preferring the
    # trailing token ("Street X, City, Country" -> Country).
    display = r.get("display_name") or ""
    for part in reversed([p.strip() for p in display.split(",")]):
        hit = _country_by_name(part)
        if hit:
            return hit.alpha_3
    return None


def _country_by_name(name):
    """pycountry lookup tolerant of common official-name variants."""
    if not name:
        return None
    variants = [name, unicodedata.normalize("NFKD", name).encode(
        "ascii", "ignore").decode()]
    for v in variants:
        for query in (v, v.replace("&", "and"), v.replace("St.", "Saint")):
            try:
                return pycountry.countries.lookup(query)
            except LookupError:
                continue
    return None


def density_url(iso3):
    """WorldPop 1 km population-density raster URL for an ISO3 country.

    The server layout is stable:
      {base}/GIS/Population_Density/Global_2000_2020_1km/2020/{ISO3}/{iso3}_pd_2020_1km.tif
    """
    iso3 = iso3.upper()
    return f"{WP_BASE}/{WP_DENSITY_DATASET}/{iso3}/{iso3.lower()}_pd_{WP_POYEAR}_1km.tif"


def density_raster_path(iso3):
    """Shared on-disk path of the national density raster (one per country)."""
    iso3 = iso3.upper()
    return os.path.join(DATA_DIR, "raw", "worldpop", iso3,
                        f"{iso3.lower()}_pd_{WP_POYEAR}_1km.tif")


def ensure_density_raster(iso3, retries=3):
    """Download the national density raster once; return its local path.

    Streams to ``<path>.part`` and renames only on success, so an interrupted
    download can never be mistaken for a complete raster.
    """
    path = density_raster_path(iso3)
    if os.path.exists(path):
        return path
    url = density_url(iso3)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part_path = path + ".part"

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            print(f"Downloading WorldPop {iso3} population-density raster..."
                  + (f" (attempt {attempt}/{retries})" if attempt > 1 else ""))
            with requests.get(url, stream=True, timeout=120) as r:
                if r.status_code != 200:
                    raise RuntimeError(
                        f"WorldPop density raster for '{iso3}' not available "
                        f"({r.status_code}): {url}. Check "
                        f"https://www.worldpop.org/datacatalog/ or pass --country <ISO3>."
                    )
                total = int(r.headers.get("content-length", 0))
                done = 0
                with open(part_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            print(f"\r  {done / 1e6:.1f} / {total / 1e6:.1f} MB", end="")
            os.replace(part_path, path)  # atomic on success
            print(f"\n✓ Saved to {path}")
            return path
        except (requests.RequestException, RuntimeError) as e:
            last_error = e
            if os.path.exists(part_path):
                os.remove(part_path)
    raise last_error


def country_for_config(cfg):
    """Best-effort ISO3 for a CityConfig: explicit -> meta.json -> geocoding.

    A country resolved by geocoding is written back to meta.json, so the
    lookup happens at most once per city and later runs stay offline.
    """
    if getattr(cfg, "country", None):
        return cfg.country.upper()
    code = load_meta(cfg).get("iso_country")
    if code:
        return str(code).upper()
    print("Resolving country via geocoding (one-time)...")
    code = iso3_from_place(cfg.place)
    if not code:
        raise ValueError(
            f"Could not determine the country of '{cfg.place}'. "
            f"Pass --country <ISO3> explicitly."
        )
    persist_meta(cfg, iso_country=code)
    return code
