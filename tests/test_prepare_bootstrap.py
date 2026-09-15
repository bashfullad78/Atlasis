"""Tests for the ensure_data bootstrap orchestrator (no network required)."""
import json

import geopandas as gpd
import pytest
from shapely.geometry import Point

import backend.data.prepare as prep
from backend.config import city_config

POLYGON = Point(76.6, 12.3).buffer(0.1)  # near Mysuru; never fetched offline


def _boundary_gdf():
    return gpd.GeoDataFrame(geometry=[POLYGON], crs="EPSG:4326")


def _city_in(tmp_path, monkeypatch, slug):
    monkeypatch.setattr("backend.config.DATA_DIR", str(tmp_path))
    return city_config(slug)


def _fake_network(monkeypatch, calls, fail=0):
    """Replace every network-backed step with a recorder."""

    def geocode(cfg):
        calls.append("geocode")
        return _boundary_gdf()

    def step(name, needs_boundary=True):
        def _step(cfg, boundary=None):
            calls.append(name)
            assert (boundary is not None) == needs_boundary
        return _step

    state = {"fails": 0}

    def roads(cfg, boundary=None):
        if state["fails"] < fail:
            state["fails"] += 1
            raise IOError("overpass timeout")
        calls.append("roads")
        assert boundary is not None

    monkeypatch.setattr(prep, "geocode_boundary", geocode)
    monkeypatch.setattr(prep, "download_roads", roads)
    monkeypatch.setattr(prep, "download_hospitals", step("hospitals"))
    monkeypatch.setattr(prep, "download_forbidden", step("forbidden"))
    monkeypatch.setattr(prep, "load_population_grid",
                        lambda cfg: calls.append("population"))
    monkeypatch.setattr(prep.time, "sleep", lambda s: None)
    return state


def _write_parquet(cfg):
    import pandas as pd
    pd.DataFrame({"lon": [], "lat": [], "population": []}).to_parquet(
        cfg.pop_cache_path)


def test_ensure_data_full_bootstrap_order(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "fresh")
    calls = []
    _fake_network(monkeypatch, calls)
    built = prep.ensure_data(cfg)
    assert built == ["boundary", "roads", "hospitals", "forbidden", "population"]
    assert calls == ["geocode", "roads", "hospitals", "forbidden", "population"]
    assert (tmp_path / "raw" / "fresh" / "boundary.gpkg").exists()


def test_ensure_data_skips_existing_artifacts(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "warm")
    calls = []
    _fake_network(monkeypatch, calls)
    raw = tmp_path / "raw" / "warm"
    raw.mkdir(parents=True)
    for f in ["boundary.gpkg", "roads.graphml", "hospitals.gpkg",
              "forbidden.gpkg"]:
        (raw / f).write_text("")
    _write_parquet(cfg)

    assert prep.missing_artifacts(cfg) == []
    assert prep.ensure_data(cfg) == []
    assert calls == []  # zero network calls on a warm cache


def test_ensure_data_resumes_after_failure(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "resume")
    calls = []

    # First call: boundary succeeds, then roads fails 3 times and gives up.
    _fake_network(monkeypatch, calls, fail=3)
    with pytest.raises(IOError):
        prep.ensure_data(cfg)
    assert calls == ["geocode"]  # boundary built before the failure
    assert (tmp_path / "raw" / "resume" / "boundary.gpkg").exists()

    # Second call: everything else builds; geocode is NOT called again.
    calls.clear()
    _fake_network(monkeypatch, calls)
    built = prep.ensure_data(cfg)
    assert built == ["roads", "hospitals", "forbidden", "population"]
    assert calls == ["roads", "hospitals", "forbidden", "population"]


def test_ensure_data_boundary_read_for_partial_caches(tmp_path, monkeypatch):
    """Steps after boundary must load the boundary from disk, not re-geocode."""
    cfg = _city_in(tmp_path, monkeypatch, "partial")
    calls = []
    _fake_network(monkeypatch, calls)
    raw = tmp_path / "raw" / "partial"
    raw.mkdir(parents=True)
    _boundary_gdf().to_file(raw / "boundary.gpkg", driver="GPKG")

    prep.ensure_data(cfg)
    assert calls == ["roads", "hospitals", "forbidden", "population"]


def test_retry_gives_up_after_max_attempts(tmp_path, monkeypatch):
    _city_in(tmp_path, monkeypatch, "retry")
    attempts = []

    def always_fails(*a):
        attempts.append(1)
        raise IOError("down")

    with pytest.raises(IOError):
        prep._retry(always_fails, retries=3, backoff_s=0)
    assert len(attempts) == 3


def test_probe_lists_missing_without_fetching(tmp_path, monkeypatch, capsys):
    cfg = _city_in(tmp_path, monkeypatch, "probe")
    calls = []
    _fake_network(monkeypatch, calls)
    assert prep.probe_data(cfg) == ["boundary", "roads", "hospitals",
                                    "forbidden", "population"]
    assert calls == []
    out = capsys.readouterr().out
    assert "MISSING" in out and "cached" not in out


def test_probe_all_cached(tmp_path, monkeypatch, capsys):
    cfg = _city_in(tmp_path, monkeypatch, "allcached")
    _fake_network(monkeypatch, [])
    raw = tmp_path / "raw" / "allcached"
    raw.mkdir(parents=True)
    for f in ["boundary.gpkg", "roads.graphml", "hospitals.gpkg",
              "forbidden.gpkg"]:
        (raw / f).write_text("")
    _write_parquet(cfg)
    assert prep.probe_data(cfg) == []
    out = capsys.readouterr().out
    assert "fully offline" in out
