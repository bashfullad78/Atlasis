"""Fetched-at provenance stamps land in meta.json for every builder (offline)."""
import numpy as np
import geopandas as gpd
import networkx as nx
from shapely.geometry import Point, box

from backend.config import city_config, load_meta, persist_meta


def _city_in(tmp_path, monkeypatch, slug):
    monkeypatch.setattr("backend.config.DATA_DIR", str(tmp_path))
    raw = tmp_path / "raw" / slug
    raw.mkdir(parents=True)
    cfg = city_config(slug)
    b = gpd.GeoDataFrame(
        geometry=[box(8.68, 49.39, 8.71, 49.42)], crs="EPSG:4326")
    b.to_file(cfg.boundary_path, driver="GPKG")
    return cfg, b


def test_geocode_boundary_stamps(tmp_path, monkeypatch):
    import backend.data.download as dl
    cfg, b = _city_in(tmp_path, monkeypatch, "stamps1")
    fake = b.copy()
    fake["address"] = [{"country_code": "DE"}]
    monkeypatch.setattr(dl.ox, "geocode_to_gdf", lambda place: fake)
    dl.geocode_boundary(cfg)
    meta = load_meta(cfg)
    assert meta["iso_country"] == "DEU"
    assert meta["fetched_boundary"] is not None


def test_download_roads_stamps(tmp_path, monkeypatch):
    import backend.data.download as dl
    from backend.routing.graph import has_slim
    cfg, b = _city_in(tmp_path, monkeypatch, "stamps2")
    g = nx.MultiDiGraph()
    g.add_node(1, x=8.69, y=49.40)
    g.add_node(2, x=8.70, y=49.41)
    g.add_edge(1, 2, length=1000.0, maxspeed=50)
    g.add_edge(2, 1, length=1000.0, maxspeed=50)
    monkeypatch.setattr(dl, "overpass_call",
                        lambda fn, poly, **kw: g)
    dl.download_roads(cfg, boundary=b)
    assert has_slim(cfg)
    assert "fetched_roads" in load_meta(cfg)


def test_download_hospitals_stamps(tmp_path, monkeypatch):
    import backend.data.download as dl
    cfg, b = _city_in(tmp_path, monkeypatch, "stamps3")
    fake = gpd.GeoDataFrame(
        {"name": ["H"], "amenity": ["hospital"]},
        geometry=[Point(8.69, 49.40)], crs="EPSG:4326")
    monkeypatch.setattr(dl, "overpass_call",
                        lambda fn, poly, **kw: fake)
    dl.download_hospitals(cfg, boundary=b)
    assert cfg.hospitals_path.endswith("hospitals.gpkg")
    assert load_meta(cfg).get("fetched_hospitals") is not None


def test_download_forbidden_stamps(tmp_path, monkeypatch):
    import backend.constraints.forbidden as fb
    cfg, b = _city_in(tmp_path, monkeypatch, "stamps4")
    fake = gpd.GeoDataFrame(geometry=[box(8.69, 49.395, 8.70, 49.405)],
                            crs="EPSG:4326")
    monkeypatch.setattr(fb, "overpass_call",
                        lambda fn, poly, **kw: fake)
    fb.download_forbidden(cfg, boundary=b)
    assert "fetched_forbidden" in load_meta(cfg)


def test_population_cache_stamps(tmp_path, monkeypatch):
    import backend.data.population as pop
    cfg, b = _city_in(tmp_path, monkeypatch, "stamps5")
    data = np.array([[[1.0]]])  # (band, row, col): one cell, 1 people/km^2
    transform = __import__("affine").Affine(0.01, 0, 8.69, 0.01, 0, 49.40)

    class FakeSrc:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __init__(self):
            self.test = None

    monkeypatch.setattr(pop, "ensure_density_raster", lambda iso3: "fake.tif")
    monkeypatch.setattr(pop.rasterio, "open",
                        lambda path: FakeSrc())
    fake_mask = (data, transform)
    monkeypatch.setattr(pop, "rio_mask",
                        lambda src, geom, **kw: (fake_mask[0], fake_mask[1]))
    pop._write_cache(cfg, "DEU")
    assert "fetched_population" in load_meta(cfg)


def test_persist_meta_stamps_never_clobber(tmp_path, monkeypatch):
    cfg, _ = _city_in(tmp_path, monkeypatch, "stampcity")
    persist_meta(cfg, fetched_roads="2026-10-21T10:00:00+00:00")
    persist_meta(cfg, fetched_hospitals="2026-10-21T10:05:00+00:00")
    meta = load_meta(cfg)
    assert meta["fetched_roads"] == "2026-10-21T10:00:00+00:00"
    assert meta["fetched_hospitals"] == "2026-10-21T10:05:00+00:00"
