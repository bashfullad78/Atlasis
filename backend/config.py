"""Per-city configuration. All city-specific logic flows through CityConfig."""
import json
import os
from dataclasses import dataclass, field

import geopandas as gpd
import pyproj

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../data")

# Indian cities useful for quick tests (any place string works via --place).
PLACES = {
    "mysuru": "Mysuru, Karnataka, India",
    "bengaluru": "Bengaluru, Karnataka, India",
    "hyderabad": "Hyderabad, Telangana, India",
    "pune": "Pune, Maharashtra, India",
    "chennai": "Chennai, Tamil Nadu, India",
    "kolkata": "Kolkata, West Bengal, India",
}


def utm_epsg_from_lonlat(lon, lat):
    """UTM zone EPSG code for a lon/lat point (32xxx southern hemisphere)."""
    zone = int((lon + 180) / 6) + 1
    return 32600 + zone if lat >= 0 else 32700 + zone


def city_config(slug="mysuru", place=None):
    return CityConfig(slug=slug, place=place or PLACES.get(slug, slug))


@dataclass
class CityConfig:
    slug: str
    place: str
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
    def hospitals_path(self):
        return os.path.join(self.raw_dir, "hospitals.gpkg")

    @property
    def forbidden_path(self):
        return os.path.join(self.raw_dir, "forbidden.gpkg")

    @property
    def pop_cache_path(self):
        return os.path.join(self.raw_dir, "population.csv")

    def utm_epsg(self):
        """EPSG from meta.json (written by download), else from boundary bounds."""
        if self._epsg is None:
            if os.path.exists(self.meta_path):
                with open(self.meta_path) as f:
                    self._epsg = json.load(f).get("utm_epsg")
            if self._epsg is None and os.path.exists(self.boundary_path):
                b = gpd.read_file(self.boundary_path).to_crs(epsg=4326)
                c = b.union_all().centroid
                self._epsg = utm_epsg_from_lonlat(c.x, c.y)
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
