"""Regression tests for the existing repo fixes"""
import json
import os

import geopandas as gpd
import networkx as nx
import pytest
from shapely.geometry import Point

import backend.data.cleanup as cleanup
import backend.data.prepare as prep
import backend.pipeline as pl
from backend.config import city_config, load_meta, persist_meta
from backend.constraints.filters import filter_far_from_roads
from backend.gis.hospitals import _curate
from backend.objectives.baseline import (accessibility_stats,
                                         _weighted_time_stats)
from backend.optimizers.mclp import select_sites
from backend.optimizers.ranker import get_top_candidates
from conftest import line_world, snap_patch


# Fix 3: fewer candidates than top_n

def test_top_candidates_fewer_than_top_n():
    scored = gpd.GeoDataFrame(
        {"score": [10.0, 5.0], "score_total": [20.0, 20.0]},
        geometry=gpd.points_from_xy([0.0, 1.0], [0.0, 0.0]), crs="EPSG:32632")
    top = get_top_candidates(scored, top_n=5)
    assert list(top["rank"]) == [1, 2]


# Fix 4: hospital cache without a `name` column

def test_curate_survives_nameless_cache():
    h = gpd.GeoDataFrame({"amenity": ["hospital"]},
                         geometry=[Point(0.0, 0.0)], crs="EPSG:32632")
    kept = _curate(h)
    assert len(kept) == 1


# Fix 7: no reachable population at all

def test_weighted_time_stats_empty_is_zeroed():
    stats = _weighted_time_stats([])
    assert stats["mean_s"] == 0.0 and stats["max_s"] == 0.0


def test_accessibility_zero_coverage(monkeypatch):
    """Hospital in one road component, population in another -> 0% + note."""
    snap_patch(monkeypatch)
    g = nx.DiGraph()
    g.graph["crs"] = "EPSG:32632"
    g.add_nodes_from(range(4))
    g.add_edge(2, 3, length=1000.0, maxspeed=3600.0)  # pop component only
    pop = gpd.GeoDataFrame(
        {"population": [0.0, 0.0, 10.0, 10.0]},
        geometry=gpd.points_from_xy([0., 1., 2., 3.], [0., 0., 0., 0.]),
        crs="EPSG:32632")
    hospitals = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy([0.0], [0.0]), crs="EPSG:32632")
    stats = accessibility_stats(hospitals.geometry, g, pop, threshold=300)
    assert stats["coverage_pct"] == 0.0
    assert stats["unreachable_population"] == 20.0


# Fix 5 + exact mode honesty

def test_exact_time_limited_is_not_reported_optimal(monkeypatch):
    import numpy as np
    import backend.optimizers.mclp as mclp_mod
    from backend.optimizers.mclp import CoverageInstance, exact_mclp
    A = np.array([[1, 1, 0], [1, 0, 1], [0, 1, 1]], dtype=bool)
    w = np.array([10.0, 10.0, 10.0])
    inst = CoverageInstance(A, w, w, candidates=None,
                            pop_node_ids=[0, 1, 2], threshold=300)
    res = exact_mclp(inst, k=1, time_limit_s=0)
    if res["status"] == "Optimal":
        pytest.skip("CBC proved optimality despite the 0 s limit on this run")
    assert res["solved_optimally"] is False

    # select_sites default limit solves tiny instances; force a time-limited
    # outcome to pin the contract: no gap is certified without a proof.
    def limited(_inst, k, time_limit_s=60):
        base = exact_mclp(_inst, k, time_limit_s=5)
        base.update(status="Not Solved", solved_optimally=False)
        return base

    monkeypatch.setattr(mclp_mod, "exact_mclp", limited)
    both = select_sites(inst, k=1, mode="both")
    assert both["gap_pct"] is None


def test_gap_zero_when_proved_optimal():
    import numpy as np
    from backend.optimizers.mclp import CoverageInstance
    A = np.array([[1, 1, 0], [1, 0, 1], [0, 1, 1]], dtype=bool)
    w = np.array([10.0, 10.0, 10.0])
    inst = CoverageInstance(A, w, w, candidates=None,
                            pop_node_ids=[0, 1, 2], threshold=300)
    both = select_sites(inst, k=2, mode="both")
    assert both["gap_pct"] == 0.0


# Fix 1 + 12: exact mode must evaluate the exact selection downstream

def test_run_pipeline_exact_mode_uses_exact_selection(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config.DATA_DIR", str(tmp_path))
    snap_patch(monkeypatch)
    graph, candidates, pop, hospitals = line_world()

    cfg = city_config("pytestpipe")
    persist_meta(cfg, utm_epsg=32632)  # run_pipeline needs cfg.crs, no geocode

    monkeypatch.setattr(pl, "load_boundary", lambda cfg: hospitals.iloc[:0])
    monkeypatch.setattr(pl, "load_hospitals", lambda cfg: hospitals)
    monkeypatch.setattr(pl, "load_graph", lambda cfg: graph)
    monkeypatch.setattr(pl, "generate_candidates",
                        lambda cfg, spacing_m=None: candidates)
    monkeypatch.setattr(
        pl, "apply_all_filters",
        lambda c, h, g, ccfg, skip_forbidden=False: c)
    monkeypatch.setattr(pl, "load_population_grid", lambda cfg: pop)

    def fake_select_sites(inst, k, mode="greedy"):
        return {
            "greedy": {"solver": "greedy", "selected": [1], "gains": [10.0],
                       "objective": 10.0, "seconds": 0.0},
            "exact": {"solver": "exact", "selected": [3], "gains": None,
                      "objective": 10.0, "seconds": 0.0,
                      "status": "Optimal", "solved_optimally": True},
        }

    monkeypatch.setattr(pl, "select_sites", fake_select_sites)

    real_optimized_set = pl.compute_optimized_set
    captured = {}

    def spy_optimized_set(selection, *args, **kwargs):
        captured["selected"] = list(selection["selected"])
        return real_optimized_set(selection, *args, **kwargs)

    monkeypatch.setattr(pl, "compute_optimized_set", spy_optimized_set)

    result = pl.run_pipeline(cfg, mode="exact", num_sites=2)
    assert result["selection"]["selected"] == [3]  # exact, not greedy [1]
    assert captured["selected"] == [3]             # accessibility set matches


def test_run_pipeline_greedy_mode_uses_greedy_selection(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config.DATA_DIR", str(tmp_path))
    snap_patch(monkeypatch)
    graph, candidates, pop, hospitals = line_world()

    cfg = city_config("pytestgreedy")
    persist_meta(cfg, utm_epsg=32632)

    monkeypatch.setattr(pl, "load_boundary", lambda cfg: hospitals.iloc[:0])
    monkeypatch.setattr(pl, "load_hospitals", lambda cfg: hospitals)
    monkeypatch.setattr(pl, "load_graph", lambda cfg: graph)
    monkeypatch.setattr(pl, "generate_candidates",
                        lambda cfg, spacing_m=None: candidates)
    monkeypatch.setattr(
        pl, "apply_all_filters",
        lambda c, h, g, ccfg, skip_forbidden=False: c)
    monkeypatch.setattr(pl, "load_population_grid", lambda cfg: pop)

    def fake_select_sites(inst, k, mode="greedy"):
        return {"greedy": {"solver": "greedy", "selected": [1],
                           "gains": [10.0], "objective": 10.0, "seconds": 0.0}}

    monkeypatch.setattr(pl, "select_sites", fake_select_sites)
    result = pl.run_pipeline(cfg, mode="greedy", num_sites=2)
    assert result["selection"]["selected"] == [1]


# Fix 16: input validation

def test_run_pipeline_rejects_bad_parameters(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config.DATA_DIR", str(tmp_path))
    cfg = city_config("pytestbad")
    persist_meta(cfg, utm_epsg=32632)
    with pytest.raises(ValueError):
        pl.run_pipeline(cfg, top_n=0)
    with pytest.raises(ValueError):
        pl.run_pipeline(cfg, threshold=-5)
    with pytest.raises(ValueError):
        pl.run_pipeline(cfg, num_sites=0)
    with pytest.raises(ValueError):
        pl.run_pipeline(cfg, spacing_m=1)


# Fix 6: road filter against segments, spatial-index join

def _two_node_line_graph():
    g = nx.MultiDiGraph()
    g.graph["crs"] = "EPSG:32632"
    g.add_node(0, x=0.0, y=0.0)
    g.add_node(1, x=10000.0, y=0.0)
    g.add_edge(0, 1, length=10000.0)
    g.add_edge(1, 0, length=10000.0)
    return g


def test_road_filter_keeps_candidate_mid_segment(tmp_path):
    from backend.config import CityConfig
    g = _two_node_line_graph()
    candidates = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy([5000.0, 5000.0], [0.0, 2000.0]),
        crs="EPSG:32632")
    cfg = CityConfig(slug="roadtest", place="roadtest")
    cfg._epsg = 32632
    cfg._crs = None
    filtered = filter_far_from_roads(candidates, g, cfg)
    # Candidate 0 sits ON the segment midpoint (0 m, but 5 km from both
    # nodes); candidate 1 is 2 km off the road. Only the midpoint stays.
    assert len(filtered) == 1
    assert filtered.geometry.iloc[0].y == 0.0


# Fix 8: atomic meta.json

def test_persist_meta_leaves_no_part_file(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config.DATA_DIR", str(tmp_path))
    cfg = city_config("atomictest")
    persist_meta(cfg, utm_epsg=32643, iso_country="IND")
    persist_meta(cfg, iso_country="IND")  # no change -> no second write
    import os
    leftovers = [f for f in os.listdir(cfg.raw_dir) if ".part" in f]
    assert leftovers == []
    assert load_meta(cfg)["utm_epsg"] == 32643


# Fix 10: non-network errors are not retried

def test_retry_reraises_value_error_immediately(monkeypatch):
    calls = []

    def permanent(*a):
        calls.append(1)
        raise ValueError("No facilities tagged ...")

    monkeypatch.setattr(prep.time, "sleep", lambda s: None)
    with pytest.raises(ValueError):
        prep._retry(permanent)
    assert len(calls) == 1


# Fix 11: slug cannot escape the data dir

def test_city_config_rejects_path_traversal():
    with pytest.raises(ValueError):
        city_config("../../tmp/evil")
    with pytest.raises(ValueError):
        city_config("Has Spaces")
    assert city_config("kampala").slug == "kampala"


# Fix 9: cleanup tolerates half-built city directories

def test_find_spent_rasters_skips_metaless_dirs(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    (raw / "worldpop" / "DEU").mkdir(parents=True)
    tif = raw / "worldpop" / "DEU" / "deu.tif"
    tif.write_bytes(b"x")
    # Half-built city: directory exists, no meta.json (interrupted bootstrap).
    (raw / "halfbuilt").mkdir()
    # Cached city: meta + parquet present.
    good = raw / "goodcity"
    good.mkdir()
    (good / "meta.json").write_text(json.dumps({"iso_country": "DEU"}))
    (good / "population.parquet").write_bytes(b"x")
    # Same-country city still missing its cache -> raster is NOT spent.
    pending = raw / "pending"
    pending.mkdir()
    (pending / "meta.json").write_text(json.dumps({"iso_country": "DEU"}))

    monkeypatch.setattr(cleanup, "RAW_DIR", str(raw))
    monkeypatch.setattr(cleanup, "DATA_DIR", str(tmp_path))
    assert cleanup.find_spent_rasters() == []

    # Once the pending city caches too, the raster becomes spent; the
    # meta-less directory never blocks it.
    (pending / "population.parquet").write_bytes(b"x")
    assert cleanup.find_spent_rasters() == [str(tif)]


def test_find_spent_rasters_ignores_unreadable_meta(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    (raw / "worldpop" / "IND").mkdir(parents=True)
    tif = raw / "worldpop" / "IND" / "ind.tif"
    tif.write_bytes(b"x")
    broken = raw / "brokencity"
    broken.mkdir()
    (broken / "meta.json").write_text("{truncated")
    monkeypatch.setattr(cleanup, "RAW_DIR", str(raw))
    monkeypatch.setattr(cleanup, "DATA_DIR", str(tmp_path))
    assert cleanup.find_spent_rasters() == [str(tif)]
