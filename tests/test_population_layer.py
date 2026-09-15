"""Tests for the country-agnostic population layer (no network required)."""
import json

import numpy as np
import pandas as pd
import pytest
import rasterio.transform
import requests

import backend.data.worldpop as wp
from backend.config import city_config
from backend.data.population import _window_to_cells, load_population_grid
from backend.data.worldpop import (_country_by_name, density_url,
                                   iso3_from_place, to_iso3)


# --- ISO3 resolution --------------------------------------------------------

def test_to_iso3_normalizes_alpha2():
    assert to_iso3("ug") == "UGA"
    assert to_iso3("DE") == "DEU"
    assert to_iso3("IND") == "IND"
    assert to_iso3(None) is None
    assert to_iso3("XX") is None  # not a valid code


def test_iso3_from_address_details(monkeypatch):
    def fake_request(params, **kwargs):
        return [{"address": {"country_code": "ug"},
                 "display_name": "Kampala, Uganda"}]
    monkeypatch.setattr("osmnx._nominatim._nominatim_request", fake_request)
    assert iso3_from_place("Kampala, Uganda") == "UGA"


def test_iso3_from_display_name_fallback(monkeypatch):
    def fake_request(params, **kwargs):
        return [{"display_name": "Mysuru, Karnataka, India"}]
    monkeypatch.setattr("osmnx._nominatim._nominatim_request", fake_request)
    assert iso3_from_place("Mysuru") == "IND"


def test_iso3_no_result(monkeypatch):
    monkeypatch.setattr("osmnx._nominatim._nominatim_request",
                        lambda params, **kwargs: [])
    assert iso3_from_place("Nowhere") is None


def test_country_by_name_variants():
    assert _country_by_name("India").alpha_3 == "IND"
    assert _country_by_name("Czech Republic").alpha_3 == "CZE"
    assert _country_by_name("Côte d'Ivoire").alpha_3 == "CIV"
    assert _country_by_name("") is None


# --- WorldPop URLs and raster download --------------------------------------

def test_density_url_pattern():
    assert density_url("IND") == (
        "https://data.worldpop.org/GIS/Population_Density/Global_2000_2020_1km/"
        "2020/IND/ind_pd_2020_1km.tif"
    )
    assert density_url("uga").endswith("/UGA/uga_pd_2020_1km.tif")


def test_ensure_density_raster_downloads_once(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "DATA_DIR", str(tmp_path))
    payload = b"fake-tif-bytes"

    class FakeResponse:
        status_code = 200
        headers = {"content-length": str(len(payload))}
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def iter_content(self, chunk_size):
            yield payload

    calls = []
    monkeypatch.setattr(wp.requests, "get",
                        lambda url, **kw: calls.append(url) or FakeResponse())

    path = wp.ensure_density_raster("UGA")
    assert calls == [wp.density_url("UGA")]
    assert open(path, "rb").read() == payload

    wp.ensure_density_raster("UGA")  # cached: no second download
    assert len(calls) == 1


# --- Density -> count conversion --------------------------------------------

def test_ensure_density_raster_retries_and_never_keeps_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "DATA_DIR", str(tmp_path))

    class Boom(requests.RequestException):
        pass

    attempts = []

    def flaky_get(url, **kw):
        attempts.append(url)
        if len(attempts) < 3:
            raise Boom("connection reset")

        class FakeResponse:
            status_code = 200
            headers = {"content-length": "4"}
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def iter_content(self, chunk_size):
                yield b"data"
        return FakeResponse()

    monkeypatch.setattr(wp.requests, "get", flaky_get)
    path = wp.ensure_density_raster("ZZZ", retries=3)
    assert len(attempts) == 3
    assert open(path, "rb").read() == b"data"
    assert not __import__("os").path.exists(path + ".part")


def test_ensure_density_raster_404_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "DATA_DIR", str(tmp_path))

    class FakeResponse:
        status_code = 404
        headers = {}
        def iter_content(self, chunk_size):
            return iter(())
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(wp.requests, "get", lambda url, **kw: FakeResponse())
    with pytest.raises(RuntimeError, match="not available"):
        wp.ensure_density_raster("ZZZ", retries=1)


def test_window_to_cells_converts_density_to_counts():
    # 0.01-degree cells (~1 km at the equator-ish), origin at (76E, 13N).
    transform = rasterio.transform.from_origin(76.0, 13.0, 0.01, 0.01)
    density = np.array([[5.0, -99999.0, 0.0],
                        [np.nan, 2.5, 7.5]], dtype="float32")
    df = _window_to_cells(density, transform)

    assert len(df) == 3
    assert str(df["population"].dtype) == "float32"

    # Geodesic area of a 0.01 x 0.01 degree cell near lat ~13N: ~1.205 km^2,
    # so counts are slightly BELOW the density values.
    areas = df["population"] / np.array([5.0, 2.5, 7.5])
    assert areas.min() > 1.19
    assert areas.max() < 1.22

    first = df.iloc[0]  # np.nonzero is row-major, so cell (0, 0) comes first
    assert first["lon"] == pytest.approx(76.005)
    assert first["lat"] == pytest.approx(12.995)
    assert first["population"] == pytest.approx(6.02, rel=1e-2)


def test_window_to_cells_area_scale_and_latitude_invariant():
    from backend.data.population import EARTH_RADIUS_KM

    # Spherical area of a 0.01 x 0.01 degree cell at the equator:
    # (pi*R/180 * 0.01)^2 km^2 (~1.2364 km^2), centered at lat 0.005 deg.
    transform = rasterio.transform.from_origin(0.0, 0.01, 0.01, 0.01)
    df = _window_to_cells(np.array([[1.0]], dtype="float32"), transform)
    expected = (np.pi * EARTH_RADIUS_KM / 180 * 0.01) ** 2 \
        * np.cos(np.deg2rad(0.005))
    assert df["population"].iloc[0] == pytest.approx(expected, rel=1e-4)

    # Invariant: an identical cell at 60N has exactly half the area (cos 60).
    transform_60 = rasterio.transform.from_origin(0.0, 60.01, 0.01, 0.01)
    df60 = _window_to_cells(np.array([[1.0]], dtype="float32"), transform_60)
    ratio = df60["population"].iloc[0] / df["population"].iloc[0]
    assert ratio == pytest.approx(np.cos(np.deg2rad(60.005)) / np.cos(np.deg2rad(0.005)),
                                  rel=1e-6)


def test_window_to_cells_all_nodata():
    transform = rasterio.transform.from_origin(0.0, 0.0, 0.01, 0.01)
    df = _window_to_cells(np.array([[-99999.0, 0.0]], dtype="float32"), transform)
    assert df.empty


# --- Cache handling ---------------------------------------------------------

def _city_in(tmp_path, monkeypatch, slug):
    monkeypatch.setattr("backend.config.DATA_DIR", str(tmp_path))
    raw = tmp_path / "raw" / slug
    raw.mkdir(parents=True)
    (raw / "meta.json").write_text(
        json.dumps({"slug": slug, "utm_epsg": 32643}))
    return city_config(slug)


def test_load_population_grid_roundtrip(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "testcity")
    df = pd.DataFrame({"lon": [76.65, 76.66], "lat": [12.30, 12.31],
                       "population": [100.0, 200.0]})
    df.to_parquet(cfg.pop_cache_path, index=False)

    gdf = load_population_grid(cfg)
    assert len(gdf) == 2
    assert gdf.crs.to_epsg() == 32643
    assert gdf["population"].sum() == pytest.approx(300.0)


def test_legacy_density_csv_rejected(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "legacycity")
    pd.DataFrame(columns=["lon", "lat", "population"]).to_csv(
        cfg.pop_cache_path.replace(".parquet", ".csv"), index=False)

    with pytest.raises(RuntimeError, match="density"):
        load_population_grid(cfg)
