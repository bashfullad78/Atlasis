"""Existing-facility loading + curation (v0.3 hardened semantics).

OSM's ``amenity=hospital`` tag is polluted with clinics, dental/diagnostic
facilities and shops. v0.1/v0.2 curated with an English name blocklist tuned
on Indian cities; the v0.3 review (analysis-v0.3.md, "Facility curation
review") found that blocklist inert outside English: German "Klinik" does not
match "clinic", and Japanese/Korean/Thai/Cyrillic names cannot match at all.
High keep-rates are therefore *correct* where tagging culture is clean (e.g.
Polish-language hospital names in Krakow); the real risk is silent
over-keeping, not over-dropping.

Shipped semantics (language-independent):

* A polygon footprint (as in v0.1) or a mapped ``beds > 0`` (a deliberate,
  checkable claim of inpatient care) overrides any name-blocklist hit, in
  any language.
* Generic tags (``healthcare``, ``building``) are preserved and reported as
  review aids, never overrides: ``healthcare=hospital`` is applied broadly by
  mappers (also on dental/day-care clinics), so rescuing on it reproducibly
  changed Mysuru's documented counts (the reverted first fix attempt).
* Explicit per-city exclude verdicts (``HOSPITAL_EXCLUDE_NAMES``) stay
  absolute: they beat structural evidence.
* Caches without structural columns (every pre-v0.3 fetch) print an explicit
  "name-based only, not reviewed for this city's language" note instead of
  the misleading ``dropped 0``.
* ``backend.data.download`` preserves ``amenity/healthcare/beds/building/
  operator`` so future fetches carry the evidence. Existing caches keep
  their documented counts exactly (85 / 32 / 9 / 27).

Keep this module ASCII-only: every name heuristic must live in the English
substring lists below, and no per-language name may be hardcoded here. The
Kyoto test (tests/test_hospital_curation_kyoto.py) enforces it.
"""
import os

import geopandas as gpd
import pandas as pd

from backend.config import CityConfig

# OSM's amenity=hospital tag is polluted with clinics, dental/diagnostic. Keep polygon-mapped facilities plus point
# facilities that pass these lists (or carry structural evidence). Edit per city as needed.
HOSPITAL_NAME_BLOCKLIST = [
    "clinic", "dental", "diagnostics", "medical shop", "medical store",
    "medicals", "pharmacy", "optical", "beauty", "dispensary", "lab",
    "cinema", "theatre", "store", "shop", "nursing home", "sanitorium",
    "sanatorium", "day care", "polyclinic",
]
# Mistagged facilties
HOSPITAL_EXCLUDE_NAMES = [
    "vikram jyoti", "dr s kalyana sundaram", "clearmedi radiant",
    "all india institute of speech and hearing", "rheumacare",
    "dr. jyothis health care", "health center",
]

# Generic tags are context, not inpatient evidence (see module docstring).
STRUCTURAL_REVIEW_COLS = ("healthcare", "beds", "building")
DUPLICATE_DIST_M = 200  # same-name centroids closer than this are duplicates


def _has_structural_evidence(hospitals, is_poly):
    """Language-independent inpatient evidence per row.

    Returns ``(rescue, review)``:

    * ``rescue`` -- boolean mask of rows whose physical evidence overrides a
      name-blocklist hit: a polygon footprint, or a mapped ``beds > 0``
      (``beds=0`` is an explicit negative; missing/NaN is no evidence).
    * ``review`` -- the structural tag columns present in the cache, reported
      as review aids; empty for pre-v0.3 name-only caches.
    """
    if isinstance(is_poly, pd.Series):
        rescue = is_poly.astype(bool).copy()
    else:
        rescue = pd.Series([bool(is_poly)] * len(hospitals), index=hospitals.index)
    review = [c for c in STRUCTURAL_REVIEW_COLS if c in hospitals.columns]
    if "beds" in review:
        beds = pd.to_numeric(hospitals["beds"], errors="coerce")
        rescue = rescue | (beds > 0)
    return rescue, review


def _curate(hospitals):
    """Drop mistagged non-hospitals and near duplicates (hardened semantics).

    Name heuristics only fire where they can (English substrings); physical
    evidence (polygon / beds > 0) overrides them in any language; explicit
    excludes stay absolute. Counts printed are the rows actually dropped.
    """
    names = (hospitals.get("name", pd.Series("", index=hospitals.index))
             .fillna("").astype(str))
    lower = names.str.lower()
    is_poly = hospitals.geometry.apply(lambda g: g.geom_type in ("Polygon", "MultiPolygon"))

    rescue, review = _has_structural_evidence(hospitals, is_poly)
    blocked = lower.apply(lambda s: any(t in s for t in HOSPITAL_NAME_BLOCKLIST)) & ~is_poly
    excluded = lower.apply(lambda s: any(t in s for t in HOSPITAL_EXCLUDE_NAMES))

    n_input = len(hospitals)
    hospitals = hospitals.loc[~excluded & (is_poly | rescue | ~blocked)].copy()
    n_dropped_mistagged = n_input - len(hospitals)

    # Near duplicates: same name, centroids within 200 m (frame CRS units).
    seen, drop_idx = {}, []
    for idx, row in hospitals.iterrows():
        name = lower.loc[idx].strip()
        centroid = row.geometry.centroid
        if name in seen and centroid.distance(seen[name]) < DUPLICATE_DIST_M:
            drop_idx.append(idx)
        else:
            seen[name] = centroid
    n_after_tags = len(hospitals)
    hospitals = hospitals.drop(index=drop_idx)

    print(
        f"      Hospital curation: kept {len(hospitals)} "
        f"(dropped {n_dropped_mistagged} mis-tagged "
        f"+ {n_after_tags - len(hospitals)} duplicates)"
    )
    if not review:
        print(
            "      (cache has no beds/structural tags: name-based only, "
            "not reviewed for this city's language)"
        )
    return hospitals


def beds_capacities(hospitals, fallback=1.0):
    """Per-facility capacity for 2SFCA: numeric ``beds > 0`` where the cache
    has them, ``fallback`` (uniform 1 = presence-only) elsewhere. With no
    beds column at all this returns all-fallback, i.e. exactly the previous
    presence-only semantics.

    v0.4 wiring (README limitation "capacity not modeled"): ``download``
    preserves OSM ``beds``, so cached facilities carry real capacity when
    the city has it.
    """
    if "beds" in hospitals.columns:
        beds = pd.to_numeric(hospitals["beds"], errors="coerce")
        return beds.where(beds > 0).fillna(fallback).to_numpy(dtype=float)
    import numpy as np
    return np.full(len(hospitals), float(fallback))


def load_hospitals(cfg: CityConfig):
    """Existing hospitals as point centroids in the city's UTM zone."""
    if not os.path.exists(cfg.hospitals_path):
        raise FileNotFoundError("Hospitals not found. Run download first.")
    hospitals = gpd.read_file(cfg.hospitals_path).to_crs(cfg.crs)
    hospitals = hospitals[hospitals.geometry.notnull()]
    hospitals = _curate(hospitals)
    hospitals["capacity"] = beds_capacities(hospitals)
    if any(hospitals["capacity"] > 1):
        n_beds = int((hospitals["capacity"] > 1).sum())
        print(f"      Bed capacity modeled: {n_beds}/{len(hospitals)} "
              f"facilities weighted by OSM beds (rest presence-only)")
    hospitals["geometry"] = hospitals.geometry.centroid
    return hospitals


if __name__ == "__main__":
    from backend.config import city_config

    h = load_hospitals(city_config())
    print(f"Found {len(h)} hospitals")
    print(h[["name", "geometry"]].head())
