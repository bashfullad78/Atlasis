import geopandas as gpd
import numpy as np
from shapely.geometry import Point

from backend.config import CityConfig
from backend.gis.boundary import load_boundary


def generate_candidates(cfg: CityConfig, spacing_m=None):
    """Regular grid of candidate sites inside the city boundary."""
    boundary = load_boundary(cfg)
    spacing = spacing_m or cfg.spacing_m
    minx, miny, maxx, maxy = boundary.total_bounds
    polygon = boundary.union_all()

    points = [
        Point(x, y)
        for x in np.arange(minx, maxx, spacing)
        for y in np.arange(miny, maxy, spacing)
        if polygon.contains(Point(x, y))
    ]
    candidates = gpd.GeoDataFrame(geometry=points, crs=cfg.crs)
    print(f"Generated {len(candidates)} candidate points")
    return candidates


if __name__ == "__main__":
    from backend.config import city_config

    c = generate_candidates(city_config())
    print(c.head())
