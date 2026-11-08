"""Tests for Overpass hardening: mirror failover, fast-fail timeouts, and the
combined forbidden-zones fetch. All network calls are stubbed; only osmnx
settings are touched, and a fixture restores them afterwards."""
import geopandas as gpd
import pytest
from shapely.geometry import Point

import backend.constraints.forbidden as forb
import backend.data.overpass as op
import backend.data.prepare as prep
from backend.config import city_config

POLYGON = Point(8.69, 49.40).buffer(0.05)  # near Heidelberg; never fetched offline


@pytest.fixture
def clean_settings():
    """Snapshot osmnx settings the hardening touches; restore afterwards."""
    saved = {
        "overpass_url": op.ox.settings.overpass_url,
        "requests_timeout": op.ox.settings.requests_timeout,
        "overpass_settings": op.ox.settings.overpass_settings,
    }
    op._last_good = None
    op._overpass_mirrors_cache = None
    yield
    for key, val in saved.items():
        setattr(op.ox.settings, key, val)
    op._last_good = None
    op._overpass_mirrors_cache = None


def _boundary_gdf():
    return gpd.GeoDataFrame(geometry=[POLYGON], crs="EPSG:4326")


def _city_in(tmp_path, monkeypatch, slug):
    monkeypatch.setattr("backend.config.DATA_DIR", str(tmp_path))
    return city_config(slug)


# ---------------------------------------------------------------- failover

def test_mirrors_env_override(monkeypatch, clean_settings):
    monkeypatch.setenv("ATLASIS_OVERPASS_MIRRORS",
                       "https://a.example/api, https://b.example/api")
    op._overpass_mirrors_cache = None
    assert op.overpass_mirrors() == ["https://a.example/api", "https://b.example/api"]


def test_overpass_call_first_mirror_probed_then_used(monkeypatch, clean_settings):
    """Healthy primary: probed once, settings point at it, result returned."""
    probed, calls = [], []

    def fn(polygon, tags=None):
        calls.append(op.ox.settings.overpass_url)
        return "ok"

    monkeypatch.setattr(op, "probe_mirror",
                        lambda url: probed.append(url) or True)
    assert op.overpass_call(fn, POLYGON) == "ok"
    assert probed == [op.PRIMARY_MIRROR]
    assert calls == [op.PRIMARY_MIRROR]
    assert op._last_good == op.PRIMARY_MIRROR


def test_overpass_call_fails_over_to_next_mirror(monkeypatch, clean_settings):
    urls_probed, calls = [], []

    def flaky(polygon, tags=None):
        calls.append(op.ox.settings.overpass_url)
        if len(calls) == 1:
            raise IOError("connection reset")
        return "ok"

    monkeypatch.setattr(op, "probe_mirror", lambda url: urls_probed.append(url) or True)
    assert op.overpass_call(flaky, POLYGON) == "ok"
    assert len(calls) == 2
    assert calls[0] != calls[1]              # second attempt used another mirror
    assert op._last_good == calls[1]


def test_overpass_call_remembered_mirror_tried_first(monkeypatch, clean_settings):
    order = []

    def fn():
        order.append(op.ox.settings.overpass_url)
        return 1

    monkeypatch.setattr(op, "probe_mirror", lambda url: True)
    op.overpass_call(fn)
    op._last_good = "https://overpass.kumi.systems/api"  # simulate fallback win
    op.overpass_call(fn)
    assert order[1] == "https://overpass.kumi.systems/api"


def test_overpass_call_trusted_mirror_skips_probes(monkeypatch, clean_settings):
    """Once a mirror served data, the next call goes straight to it: probing
    is request traffic too, and a dead trusted mirror costs one failed call,
    not a probe storm."""
    probed = []

    def fn():
        return op.ox.settings.overpass_url

    monkeypatch.setattr(op, "probe_mirror",
                        lambda url: probed.append(url) or pytest.fail("no probe"))
    op._last_good = op.PRIMARY_MIRROR
    assert op.overpass_call(fn) == op.PRIMARY_MIRROR
    assert probed == []


def test_overpass_call_trusted_mirror_failure_falls_over_probed(monkeypatch, clean_settings):
    """If the trusted mirror errors out, the probed loop still runs."""
    calls, probed = [], []

    def flaky():
        calls.append(op.ox.settings.overpass_url)
        if len(calls) == 1:
            raise IOError("reset")
        return "ok"

    monkeypatch.setattr(op, "probe_mirror",
                        lambda url: probed.append(url) or True)
    op._last_good = op.PRIMARY_MIRROR
    assert op.overpass_call(flaky) == "ok"
    assert calls[0] == op.PRIMARY_MIRROR  # trusted, unprobed
    assert len(calls) == 2                # ...then failed over
    assert op._last_good == calls[1]


def test_overpass_call_raises_when_all_mirrors_fail(monkeypatch, clean_settings):
    monkeypatch.setattr(op, "probe_mirror", lambda url: False)
    with pytest.raises(ConnectionError):
        op.overpass_call(lambda: 1)


def test_apply_timeouts_decouples_client_and_server(clean_settings):
    op.apply_timeouts()
    s = op.ox.settings
    assert isinstance(s.requests_timeout, tuple)   # fast-fail client timeouts
    assert str(op.SERVER_TIMEOUT_S) in s.overpass_settings  # server budget intact
    assert "180" not in str(s.requests_timeout)


# ------------------------------------------------- combined forbidden fetch

def test_forbidden_tags_are_a_single_dict(tmp_path, monkeypatch, clean_settings):
    """The combined query goes out as ONE osmnx features call."""
    seen = {}

    def fake_call(fn, *args, **kwargs):
        seen["tags"] = kwargs.get("tags")
        return gpd.GeoDataFrame(geometry=[Point(8.69, 49.40)], crs="EPSG:4326")

    monkeypatch.setattr(forb, "overpass_call", fake_call)
    cfg = _city_in(tmp_path, monkeypatch, "forbtest")
    forb.download_forbidden(cfg, boundary=_boundary_gdf())
    # All five v0.2 tag groups survive in one dict (list groups per key).
    assert set(seen["tags"]) == {"natural", "waterway", "landuse", "leisure"}
    assert set(seen["tags"]["landuse"]) == {"reservoir", "cemetery"}
    assert seen["tags"]["natural"] == "water"
    assert seen["tags"]["waterway"] is True
    assert seen["tags"]["leisure"] == "park"
    import os
    assert os.path.exists(cfg.forbidden_path)  # atomic write completed


def test_download_forbidden_requires_boundary(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "noboundary")
    with pytest.raises(FileNotFoundError):
        forb.download_forbidden(cfg)


def test_download_forbidden_refuses_empty(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "emptyforb")
    monkeypatch.setattr(
        forb, "overpass_call",
        lambda fn, *a, **k: gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"))
    with pytest.raises(ValueError):
        forb.download_forbidden(cfg, boundary=_boundary_gdf())
    import os
    assert not os.path.exists(cfg.forbidden_path)  # no empty cache written


# --------------------------------------------------------- skip-forbidden

def test_ensure_data_skip_forbidden_defers_step(tmp_path, monkeypatch):
    cfg = _city_in(tmp_path, monkeypatch, "skipforb")
    calls = []
    monkeypatch.setattr(prep, "geocode_boundary", lambda cfg: _boundary_gdf())
    monkeypatch.setattr(prep, "download_roads", lambda cfg, boundary=None: calls.append("roads"))
    monkeypatch.setattr(prep, "download_hospitals",
                        lambda cfg, boundary=None: calls.append("hospitals"))
    monkeypatch.setattr(prep, "download_forbidden",
                        lambda cfg, boundary=None: pytest.fail("must not be called"))
    monkeypatch.setattr(prep, "load_population_grid",
                        lambda cfg: calls.append("population"))
    built = prep.ensure_data(cfg, skip_forbidden=True)
    assert "forbidden" not in built
    assert calls == ["roads", "hospitals", "population"]


def test_apply_all_filters_skip_forbidden(tmp_path, monkeypatch, capsys):
    """skip_forbidden drops the forbidden filter but keeps the others."""
    import os
    from backend.constraints.filters import apply_all_filters

    cfg = _city_in(tmp_path, monkeypatch, "filtercity")
    candidates = gpd.GeoDataFrame(geometry=[Point(0, 0), Point(1, 1)], crs="EPSG:4326")
    hospitals = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs="EPSG:4326")

    calls = []

    def fake_too_close(cands, hosp, cfg):
        calls.append("proximity")
        return cands

    def fake_roads(cands, graph, cfg):
        calls.append("roads")
        return cands

    def fake_forbidden(cands, cfg):
        calls.append("forbidden")
        return cands

    monkeypatch.setattr("backend.constraints.filters.filter_too_close_to_existing",
                        fake_too_close)
    monkeypatch.setattr("backend.constraints.filters.filter_far_from_roads",
                        fake_roads)
    monkeypatch.setattr("backend.constraints.filters.filter_forbidden",
                        fake_forbidden)

    out = apply_all_filters(candidates, hospitals, object(), cfg, skip_forbidden=True)
    assert calls == ["proximity", "roads"]
    assert len(out) == 2
    assert "NOT comparable" in capsys.readouterr().out

    out2 = apply_all_filters(candidates, hospitals, object(), cfg)
    assert calls == ["proximity", "roads", "proximity", "roads", "forbidden"]
    assert len(out2) == 2
