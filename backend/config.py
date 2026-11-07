"""Per-city configuration. All city-specific logic flows through CityConfig."""
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import geopandas as gpd
import pyproj

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../data")

FIGURE_MANIFEST_PATH = os.path.join(
    DATA_DIR, "../docs/figures/manifest.json")


def utc_now_iso():
    """UTC timestamp for data-freshness stamps (traceable_meta)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def newest_data_time(cfg: CityConfig) -> "datetime | None":
    """Most recent artifact fetch recorded in meta.json, or None pre-stamps."""
    stamps = [v for k, v in load_meta(cfg).items() if k.startswith("fetched_")]
    if not stamps:
        return None
    return max(datetime.fromisoformat(s) for s in stamps)


def utm_epsg_from_lonlat(lon, lat):
    """UTM zone EPSG code for a lon/lat point (32xxx southern hemisphere)."""
    zone = int((lon + 180) / 6) + 1
    return 32600 + zone if lat >= 0 else 32700 + zone


def city_config(slug="mysuru", place=None, country=None):
    """Country-agnostic config: any geocodable place string works via --place.

    ``country`` is an optional ISO 3166-1 alpha-3 code (e.g. IND, UGA, DEU);
    it is auto-resolved from geocoding or meta.json when omitted.
    """
    # The slug is a filesystem path component (data/raw/<slug>); without this
    # check a CLI/API-supplied slug could escape the data directory.
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
        raise ValueError(
            f"Invalid city slug {slug!r}: must be lowercase letters/digits/"
            f"'-'/'_' (it names the data/raw/ directory).")
    return CityConfig(slug=slug, place=place or slug, country=country)


@dataclass
class CityConfig:
    slug: str
    place: str
    country: str = None                    # ISO3; auto-resolved if None
    network_type: str = "drive"
    spacing_m: int = 500
    threshold_sec: int = 900            # drive-time service guarantee (15 min)
    top_n: int = 5
    min_dist_existing_m: int = 800
    min_dist_road_m: int = 500
    facility_tags: dict = field(default_factory=lambda: {"amenity": "hospital"})

    _epsg: int = None
    _crs: pyproj.CRS = None

    @property
    def raw_dir(self):
        return os.path.join(DATA_DIR, "raw", self.slug)

    @property
    def processed_dir(self):
        return os.path.join(DATA_DIR, "processed", self.slug)

    @property
    def meta_path(self):
        return os.path.join(self.raw_dir, "meta.json")

    @property
    def boundary_path(self):
        return os.path.join(self.raw_dir, "boundary.gpkg")

    @property
    def roads_path(self):
        return os.path.join(self.raw_dir, "roads.graphml")

    @property
    def roads_nodes_path(self):
        """Slim-format node table (osmid, x, y) for the road graph."""
        return os.path.join(self.raw_dir, "roads_nodes.parquet")

    @property
    def roads_edges_path(self):
        """Slim-format edge table (u, v, k, length, ...) for the road graph."""
        return os.path.join(self.raw_dir, "roads_edges.parquet")

    @property
    def hospitals_path(self):
        return os.path.join(self.raw_dir, "hospitals.gpkg")

    @property
    def forbidden_path(self):
        return os.path.join(self.raw_dir, "forbidden.gpkg")

    @property
    def pop_cache_path(self):
        return os.path.join(self.raw_dir, "population.parquet")

    def utm_epsg(self):
        """EPSG from meta.json, else from boundary bounds (then persisted)."""
        if self._epsg is None:
            self._epsg = load_meta(self).get("utm_epsg")
            if self._epsg is None and os.path.exists(self.boundary_path):
                b = gpd.read_file(self.boundary_path).to_crs(epsg=4326)
                c = b.union_all().centroid
                self._epsg = utm_epsg_from_lonlat(c.x, c.y)
                # Persist so future runs (and other cities' tooling) never
                # need to recompute or re-read the boundary for this.
                persist_meta(self, utm_epsg=self._epsg)
            if self._epsg is None:
                raise FileNotFoundError(
                    f"No CRS metadata for '{self.slug}'. Run download first: "
                    f"python -m backend.data.download --city {self.slug}"
                )
        return self._epsg

    @property
    def crs(self):
        if self._crs is None:
            self._crs = pyproj.CRS.from_epsg(self.utm_epsg())
        return self._crs


def load_meta(cfg: CityConfig) -> dict:
    """Read a city's meta.json, or {} when absent."""
    if os.path.exists(cfg.meta_path):
        with open(cfg.meta_path) as f:
            return json.load(f)
    return {}


def atomic_replace(part_path, final_path):
    """Rename a fully written temp file onto its final path (atomic on POSIX)."""
    os.replace(part_path, final_path)


def persist_meta(cfg: CityConfig, **updates) -> None:
    """Merge ``updates`` into the city's meta.json (never clobbers other keys).

    Lets any component that resolves a city fact (UTM zone, country, ...)
    write it back once, so later runs are offline and reproducible. Also
    creates meta.json on first write for cities downloaded before the
    ``iso_country`` field existed. Written via a temp file + rename, so an
    interrupt can never leave a truncated meta.json.
    """
    meta = load_meta(cfg)
    changed = {k: v for k, v in updates.items() if meta.get(k) != v}
    if not changed:
        return
    meta.update(changed)
    os.makedirs(cfg.raw_dir, exist_ok=True)
    part = f"{cfg.meta_path}.part"
    with open(part, "w") as f:
        json.dump(meta, f, indent=2)
    atomic_replace(part, cfg.meta_path)
    print(f"✓ meta.json updated: {', '.join(sorted(changed))}")
