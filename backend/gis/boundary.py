import geopandas as gpd
import os

from backend.config import CityConfig


def load_boundary(cfg: CityConfig):
    """City boundary, projected to the city's UTM zone."""
    if not os.path.exists(cfg.boundary_path):
        raise FileNotFoundError("Boundary not found. Run download first.")
    return gpd.read_file(cfg.boundary_path).to_crs(cfg.crs)


if __name__ == "__main__":
    from backend.config import city_config

    b = load_boundary(city_config())
    print(b.crs)
    print(b.geometry)
