
import time

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Point

import backend.api.runner as runner
from backend.api.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset the module-global job store between tests."""
    with runner._LOCK:
        runner._JOBS.clear()
        runner._QUEUE.clear()
        runner._IP_SUBMITS.clear()
    yield


def _skill_world_result():
    """Minimal run_pipeline-shaped return dict on a 8-node line world."""
    from conftest import line_world

    _g, candidates, pop, hospitals = line_world()
    scored = candidates.copy()
    scored["score"] = [40.0, 30.0, 20.0, 10.0, 0.0, 0.0, 0.0, 0.0]
    scored["score_type"] = "population_new_within_5min"
    top = scored.head(3).copy()
    top["rank"] = [1, 2, 3]

    class _Frame(dict):
        def get(self, k, default=None):
            return (dict(self).get(k, default))

    return {
        "baseline": _Frame(coverage_pct=66.4, total_population=80.0,
                           covered_population=30.0),
        "optimized": _Frame(coverage_pct=70.0),
        "optimized_set": _Frame(coverage_pct=92.7),
        "comparison": _Frame(newly_covered_population=40.0),
        "top_sites": top,
        "selection": {"selected": [0], "candidates": scored,
                      "mode": "greedy", "k": 1,
                      "solver_results": {"greedy": {"selected": [0],
                                                    "gains": [40.0],
                                                    "objective": 40.0}}},
        "twosfca": {}, "uncertainty": None,
    }, candidates, pop, hospitals


def _slow_result():
    time.sleep(1.0)
    return _skill_world_result()[0]


@pytest.fixture()
def fake_world(monkeypatch, tmp_path):
    """Faked acquisition + pipeline + graph loaders: a job runs fully offline."""
    res, candidates, pop, hospitals = _skill_world_result()
    from conftest import line_world, snap_patch

    graph, *_rest = line_world()
    monkeypatch.setattr("backend.data.prepare.ensure_data",
                        lambda cfg, skip_forbidden=False: None)
    # No downloads happen, so no meta.json/utm_epsg exists: hand jobs a
    # config with a fixed UTM zone (line world coords need any valid CRS).
    from backend.config import CityConfig
    import backend.config as _cfgmod
    monkeypatch.setattr(_cfgmod, "city_config",
                        lambda slug, place=None, country=None: CityConfig(
                            slug=slug, place=place, country=country, _epsg=32632))
    monkeypatch.setattr(runner, "_RUN_PIPELINE",
                        lambda cfg, **kw: res)
    monkeypatch.setattr(runner, "_HOSPITAL_LOADER",
                        lambda cfg: hospitals)
    monkeypatch.setattr(runner, "_GRAPH_LOADER",
                        lambda cfg: (graph, graph.reverse(copy=True)))
    snap_patch(monkeypatch)
    return res


def _wait(job_id, statuses=("done", "failed"), timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = runner.snapshot(job_id)
        if snap and snap["status"] in statuses:
            return snap
        time.sleep(0.05)
    raise AssertionError(f"job never reached {statuses}: {snap}")


def test_submit_and_run_full_lifecycle(fake_world):
    r = client.post("/api/jobs", json={"place": "Freiburg, Germany"})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    first = client.get(f"/api/jobs/{job_id}").json()
    assert first["status"] in ("queued", "running", "done")
    snap = _wait(job_id)
    assert snap["status"] == "done"
    payload = snap["result"]
    assert payload["baseline_pct"] == pytest.approx(66.4)
    assert payload["joint_pct"] == pytest.approx(92.7)
    assert payload["comparable"] is True
    assert payload["top_sites"][0]["gain_people"] == 40
    assert payload["sites"][0]["gain_people"] == 40
    assert payload["isochrones"]["baseline"]["type"] == "FeatureCollection"
    assert payload["data_as_of"] is None  # no stamps in the faked city


def test_city_slug_uses_existing_data_dir(fake_world, monkeypatch):
    """{city: slug} must pass the exact slug into city_config — place-derived
    slugs can never match an existing cache dir."""
    seen = {}

    import backend.config as _cfgmod
    orig = _cfgmod.city_config

    def fake_city_config(slug, place=None, country=None):
        seen["slug"], seen["place"] = slug, place
        return orig(slug, place=place, country=country)

    monkeypatch.setattr(_cfgmod, "city_config", fake_city_config)
    job_id = client.post("/api/jobs", json={"city": "testville"}).json()["job_id"]
    _wait(job_id)
    assert seen["slug"] == "testville"
    assert seen["place"] == "testville"  # place omitted; display name = slug


def test_invalid_city_slug_rejected():
    r = client.post("/api/jobs", json={"city": "../etc"})
    assert r.status_code == 400
    r = client.post("/api/jobs", json={})
    assert r.status_code == 400


def test_poll_unknown_job_id():
    r = client.get("/api/jobs/nope")
    assert r.status_code == 404


def test_validation_caps():
    assert client.post("/api/jobs", json={}).status_code == 400
    assert client.post("/api/jobs",
                       json={"place": "X", "threshold": 400}).status_code == 400
    assert client.post("/api/jobs",
                       json={"place": "X", "num_sites": 6}).status_code == 400
    assert client.post("/api/jobs",
                       json={"place": "X", "country": "dach"}).status_code == 400
    assert client.post("/api/jobs",
                       json={"place": "a" * 200}).status_code == 400


def test_queue_cap_one_then_rate_limit(fake_world, monkeypatch):
    monkeypatch.setattr(runner, "_RUN_PIPELINE", lambda cfg, **kw: _slow_result())
    ids = client.post("/api/jobs", json={"place": "City One"}).json()["job_id"]
    r2 = client.post("/api/jobs", json={"place": "City Two"})
    assert r2.status_code == 202          # queued behind the running one
    r3 = client.post("/api/jobs", json={"place": "City Three"})
    assert r3.status_code == 429          # queue full
    _wait(ids)
    _wait(r2.json()["job_id"])
    # window counts successful submits only: One + Two -> Four ok, Five over
    assert client.post("/api/jobs", json={"place": "City Four"}).status_code == 202
    assert client.post("/api/jobs", json={"place": "City Five"}).status_code == 429


def test_failure_becomes_job_state(monkeypatch):
    monkeypatch.setattr("backend.data.prepare.ensure_data",
                        lambda cfg, skip_forbidden=False: None)

    def boom(cfg, **kw):
        raise RuntimeError("no road graph cache")
    monkeypatch.setattr(runner, "_RUN_PIPELINE", boom)
    job_id = client.post("/api/jobs", json={"place": "Broken City"}).json()["job_id"]
    snap = _wait(job_id)
    assert snap["status"] == "failed"
    assert "RuntimeError" in snap["error"]
    assert client.get("/api/health").json() == {"ok": True}


def test_acquisition_fallback_passes_skip_forbidden(fake_world, monkeypatch):
    """240s-gate fallback (ConnectionError) must hand skip_forbidden=True to the
    pipeline: without it the pipeline's forbidden filter crashed on the missing
    cache (live bug, heidelberg job 1c0d3b1bf630)."""
    monkeypatch.setattr(runner, "ACQUISITION_FALLBACK_S", 0)

    def flaky_acquire(cfg, skip_forbidden=False):
        if not skip_forbidden:
            raise ConnectionError("all mirrors down")
    monkeypatch.setattr("backend.data.prepare.ensure_data", flaky_acquire)

    seen = {}

    def pipeline(cfg, **kw):
        seen.update(kw)
        return _skill_world_result()[0]
    monkeypatch.setattr(runner, "_RUN_PIPELINE", pipeline)

    job_id = client.post("/api/jobs", json={"place": "Flaky City"}).json()["job_id"]
    snap = _wait(job_id)
    assert snap["status"] == "done", snap["error"]
    assert seen["skip_forbidden"] is True
    assert snap["result"]["comparable"] is False


def test_static_ui_served_and_api_routes_win():
    """Part 3: web/ is mounted at '/', and /api routes still match first."""
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "ATLASIS" in r.text
    r = client.get("/app.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert client.get("/api/health").json() == {"ok": True}
