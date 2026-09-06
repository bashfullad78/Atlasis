import geopandas as gpd
import os

from backend.config import CityConfig

# OSM's amenity=hospital tag is polluted with clinics, dental/diagnostic
# centres, medical shops etc. Keep polygon-mapped facilities plus point
# facilities that pass these lists; edit per city as needed.
HOSPITAL_NAME_BLOCKLIST = [
    "clinic", "dental", "diagnostics", "medical shop", "medical store",
    "medicals", "pharmacy", "optical", "beauty", "dispensary", "lab",
    "cinema", "theatre", "store", "shop", "nursing home", "sanitorium",
    "sanatorium", "day care", "polyclinic",
]
# Mis-tagged facilities whose names carry no blocklisted word (Mysuru examples).
HOSPITAL_EXCLUDE_NAMES = [
    "vikram jyoti", "dr s kalyana sundaram", "clearmedi radiant",
    "all india institute of speech and hearing", "rheumacare",
    "dr. jyothis health care", "health center",
]
HOSPITAL_KEEP_NAMES = []  # force-keep despite a blocklisted name


def _curate(hospitals):
    """Drop mis-tagged non-hospitals and near-duplicate rows."""
    names = hospitals["name"].fillna("").astype(str)
    lower = names.str.lower()
    is_poly = hospitals.geometry.apply(lambda g: g.geom_type in ("Polygon", "MultiPolygon"))

    blocked = lower.apply(lambda s: any(t in s for t in HOSPITAL_NAME_BLOCKLIST)) & ~is_poly
    forced = lower.apply(lambda s: any(t in s for t in HOSPITAL_KEEP_NAMES))
    excluded = lower.apply(lambda s: any(t in s for t in HOSPITAL_EXCLUDE_NAMES))
    hospitals = hospitals.loc[is_poly | forced | (~blocked & ~excluded)].copy()

    # Near-duplicates: same name, centroids within 200 m.
    seen, drop_idx = {}, []
    for idx, row in hospitals.iterrows():
        name = lower.loc[idx].strip()
        centroid = row.geometry.centroid
        if name in seen and centroid.distance(seen[name]) < 200:
            drop_idx.append(idx)
        else:
            seen[name] = centroid
    n_before = len(hospitals)
    hospitals = hospitals.drop(index=drop_idx)

    print(
        f"      Hospital curation: kept {len(hospitals)} "
        f"(dropped {int(blocked.sum()) + int(excluded.sum())} mis-tagged + {n_before - len(hospitals)} duplicates)"
    )
    return hospitals


def load_hospitals(cfg: CityConfig):
    """Existing hospitals as point centroids in the city's UTM zone."""
    if not os.path.exists(cfg.hospitals_path):
        raise FileNotFoundError("Hospitals not found. Run download first.")
    hospitals = gpd.read_file(cfg.hospitals_path).to_crs(cfg.crs)
    hospitals = hospitals[hospitals.geometry.notnull()]
    hospitals = _curate(hospitals)
    hospitals["geometry"] = hospitals.geometry.centroid
    return hospitals


if __name__ == "__main__":
    from backend.config import city_config

    h = load_hospitals(city_config())
    print(f"Found {len(h)} hospitals")
    print(h[["name", "geometry"]].head())
