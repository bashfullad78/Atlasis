"""Tests for meta.json load/merge persistence (no network required)."""
import json
import os

import pytest

import backend.data.worldpop as wp
from backend.config import (city_config, load_meta, newest_data_time,
                            persist_meta)


def _city_in(tmp_path, monkeypatch, slug, meta=None):
    monkeypatch.setattr("backend.config.DATA_DIR", str(tmp_path))
    raw = tmp_path / "raw" / slug
    raw.mkdir(parents=True)
    if meta is not None:
        (raw / "meta.json").write_text(json.dumps(meta))
    return city_config(slug)


def test_load_meta_empty_when_absent(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "nocity")
    assert load_meta(cfg) == {}


def test_persist_meta_creates_and_merges(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "newcity",
                   meta={"slug": "newcity", "utm_epsg": 32646})
    persist_meta(cfg, iso_country="IND", utm_epsg=32643)
    meta = load_meta(cfg)
    # existing keys survive, new keys land, value updates apply
    assert meta["slug"] == "newcity"
    assert meta["iso_country"] == "IND"
    assert meta["utm_epsg"] == 32643


def test_persist_meta_no_write_when_no_change(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "stablecity", meta={"iso_country": "IND"})
    before = os.path.getmtime(cfg.meta_path)
    persist_meta(cfg, iso_country="IND")
    assert os.path.getmtime(cfg.meta_path) == before


def test_persist_meta_creates_dir_and_file(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config.DATA_DIR", str(tmp_path))
    cfg = city_config("brandnew")
    persist_meta(cfg, iso_country="UGA")
    assert load_meta(cfg) == {"iso_country": "UGA"}


def test_country_for_config_persists_geocoded_iso3(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "geocodecity")
    monkeypatch.setattr(wp, "iso3_from_place", lambda place: "UGA")
    assert wp.country_for_config(cfg) == "UGA"
    # written back: a second call must not re-resolve
    calls = []
    monkeypatch.setattr(wp, "iso3_from_place", lambda place: calls.append(place))
    assert wp.country_for_config(cfg) == "UGA"
    assert calls == []


def test_country_for_config_explicit_wins_and_not_persisted(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "explicitcity",
                   meta={"iso_country": "IND"})
    assert wp.country_for_config(city_config("explicitcity", country="uga")) == "UGA"
    assert load_meta(cfg)["iso_country"] == "IND"


def test_utm_epsg_persists_boundary_derived_zone(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "crscity")
    import geopandas as gpd
    from shapely.geometry import Point
    # A boundary near Mysuru (76.6E, 12.3N) -> UTM 43N -> EPSG 32643.
    b = gpd.GeoDataFrame(geometry=[Point(76.6, 12.3).buffer(0.1)], crs="EPSG:4326")
    b.to_file(cfg.boundary_path, driver="GPKG")
    assert cfg.utm_epsg() == 32643
    assert load_meta(cfg)["utm_epsg"] == 32643


def test_newest_data_time_reads_fetched_stamps(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "stampcity")
    assert newest_data_time(cfg) is None  # pre-v0.4 cache without stamps
    persist_meta(cfg, fetched_hospitals="2026-05-01T00:00:00+00:00",
                 fetched_roads="2026-05-03T00:00:00+00:00")
    newest = newest_data_time(cfg)
    assert newest.isoformat() == "2026-05-03T00:00:00+00:00"


def test_doc_staleness_warning_on_newer_data(tmp_path, monkeypatch, capsys):
    import backend.main as m
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "rendered_at": "2026-05-10T00:00:00+00:00",
        "cities": {"kampala": {"data_as_of": "2026-05-01T00:00:00+00:00"}},
    }))
    monkeypatch.setattr(m, "FIGURE_MANIFEST_PATH", manifest)
    cfg = _city_in(tmp_path, monkeypatch, "kampala",
                   meta={"fetched_hospitals": "2026-05-20T00:00:00+00:00"})
    m.warn_if_doc_figures_stale(cfg)
    out = capsys.readouterr().out
    assert "10 day(s) newer" in out and "kampala" in out

    # data not newer than the render -> silent
    persist_meta(cfg, fetched_hospitals="2026-05-09T00:00:00+00:00")
    capsys.readouterr()
    m.warn_if_doc_figures_stale(cfg)
    assert capsys.readouterr().out == ""


def test_doc_staleness_warning_silent_without_manifest_or_stamps(
        tmp_path, monkeypatch, capsys):
    import backend.main as m
    cfg = _city_in(tmp_path, monkeypatch, "kampala")  # no stamps, no manifest
    m.warn_if_doc_figures_stale(cfg)
    assert capsys.readouterr().out == ""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"rendered_at": "2026-05-10T00:00:00+00:00",
                                    "cities": {"freiburg": {}}}))
    monkeypatch.setattr(m, "FIGURE_MANIFEST_PATH", manifest)
    m.warn_if_doc_figures_stale(cfg)
    assert capsys.readouterr().out == ""
