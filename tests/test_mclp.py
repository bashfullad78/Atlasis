"""Tests for joint MCLP selection: solvers on synthetic instances with known
optima, the (1 - 1/e) greedy guarantee as a property test, and a k=1
regression against the independent scorer (same machinery, same numbers)."""
import networkx as nx
import numpy as np
import geopandas as gpd
import pytest

from backend.objectives.coverage import (score_all_candidates_population,
                                         score_from_matrix)
from backend.optimizers.mclp import (CoverageInstance, build_coverage_matrix,
                                     exact_mclp, greedy_mclp, select_sites)


# synthetic

def _inst(A, w_new, w_total=None):
    """Wrap a raw matrix as a CoverageInstance with no geographic baggage."""
    A = np.asarray(A, dtype=bool)
    w_new = np.asarray(w_new, dtype=float)
    if w_total is None:
        w_total = w_new.copy()
    return CoverageInstance(A, w_new, w_total, candidates=None,
                            pop_node_ids=np.arange(A.shape[1]), threshold=300)


def test_greedy_picks_two_complementary_sites():
    # Candidates 0 and 1 together cover all three nodes; adding candidate 2
    # (disjoint subset) can never reach the union.
    A = [[1, 1, 0],
         [1, 0, 1],
         [0, 0, 1]]
    res = greedy_mclp(_inst(A, [10.0, 10.0, 10.0]), k=2)
    assert res["objective"] == 30.0
    assert res["gains"] == [20.0, 10.0]
    assert 0 in res["selected"] and 1 in res["selected"]


def test_greedy_stops_when_budget_unspendable():
    res = greedy_mclp(_inst([[1, 0], [0, 0]], [5.0, 0.0]), k=5)
    assert res["selected"] == [0]
    assert res["objective"] == 5.0


def test_greedy_zero_weight_nodes_do_not_drive_selection():
    res = greedy_mclp(_inst([[1, 0], [0, 1]], [0.0, 5.0]), k=1)
    assert res["selected"] == [1]


def test_exact_matches_known_optimum_and_never_loses_to_greedy():
    # Overlapping triple: the optimum (any complementary pair) covers all 3.
    A = [[1, 1, 0],
         [1, 0, 1],
         [0, 1, 1]]
    inst = _inst(A, [10.0, 10.0, 10.0])
    greedy = greedy_mclp(inst, k=2)
    exact = exact_mclp(inst, k=2)
    assert exact["objective"] == 30.0
    assert exact["objective"] >= greedy["objective"]
    assert len(exact["selected"]) <= 2


def test_select_sites_both_reports_gap():
    A = [[1, 1, 0], [1, 0, 1], [0, 1, 1]]
    res = select_sites(_inst(A, [10.0, 10.0, 10.0]), k=2, mode="both")
    assert res["gap_pct"] >= 0.0
    assert res["exact"]["objective"] >= res["greedy"]["objective"]


def test_select_sites_validates_inputs():
    with pytest.raises(ValueError):
        select_sites(_inst([[1]], [1.0]), k=0)
    with pytest.raises(ValueError):
        select_sites(_inst(np.zeros((0, 1), dtype=bool), [1.0]), k=1)


def test_submodular_guarantee_holds_on_random_instances():
    """f(greedy) >= (1 - 1/e) * f(exact) on 30 random monotone instances."""
    rng = np.random.default_rng(42)
    floor = 1 - 1 / np.e
    checked = 0
    for _ in range(30):
        C, P = int(rng.integers(5, 20)), int(rng.integers(5, 25))
        A = rng.random((C, P)) < 0.3
        w = rng.random(P) * 100
        inst = _inst(A, w)
        exact = exact_mclp(inst, k=3, time_limit_s=10)
        greedy = greedy_mclp(inst, k=3)
        if exact["objective"] > 0:
            checked += 1
            assert greedy["objective"] >= floor * exact["objective"] - 1e-6
    assert checked > 0


# k=1 regression vs scorer

def _snap_int(x, y):
    """Stand-in for ox.distance.nearest_nodes on the synthetic line graph."""
    vals = np.asarray(x, dtype=int)
    return vals.item() if vals.ndim == 0 else vals


def _line_world(n=8):
    """Real line graph 0..n-1 with 1 s hops; candidates/pop snapped 1:1."""
    g = nx.DiGraph()
    g.graph["crs"] = "EPSG:32632"
    g.add_nodes_from(range(n))
    for i in range(n - 1):
        # length/maxspeed chosen so add_travel_times yields 1.0 s per hop:
        # 1000 m at 3600 km/h.
        g.add_edge(i, i + 1, length=1000.0, maxspeed=3600.0)
        g.add_edge(i + 1, i, length=1000.0, maxspeed=3600.0)
    xs = np.arange(n, dtype=float)
    candidates = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(xs, np.zeros(n)), crs="EPSG:32632")
    pop = gpd.GeoDataFrame(
        {"population": np.full(n, 10.0)},
        geometry=gpd.points_from_xy(xs, np.zeros(n)), crs="EPSG:32632")
    hospitals = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy([0.0], [0.0]), crs="EPSG:32632")
    return g, candidates, pop, hospitals


def test_k1_greedy_matches_independent_scorer(monkeypatch):
    """k=1 greedy must reproduce the v0.1 Rank-1 marginal score exactly."""
    monkeypatch.setattr(
        "osmnx.distance.nearest_nodes", lambda graph, x, y: _snap_int(x, y))
    graph, candidates, pop, hospitals = _line_world()

    scored = score_all_candidates_population(
        candidates, pop, graph, hospitals=hospitals, threshold=3)
    inst = build_coverage_matrix(candidates, pop, graph, hospitals=hospitals,
                                 threshold=3)
    res = greedy_mclp(inst, k=1)

    top_score = float(scored["score"].max())
    # Hospital at node 0 already covers nodes 0-3; the best new site covers
    # nodes 4-7 -> 40 people on both code paths.
    assert top_score == pytest.approx(40.0)
    assert res["objective"] == pytest.approx(top_score)
    assert scored.iloc[res["selected"][0]]["score"] == pytest.approx(top_score)


def test_matrix_scoring_matches_legacy(monkeypatch):
    """The pipeline's matrix path must equal the legacy per-candidate scorer."""
    monkeypatch.setattr(
        "osmnx.distance.nearest_nodes", lambda graph, x, y: _snap_int(x, y))
    graph, candidates, pop, hospitals = _line_world()

    legacy = score_all_candidates_population(
        candidates, pop, graph, hospitals=hospitals, threshold=3)
    inst = build_coverage_matrix(candidates, pop, graph, hospitals=hospitals,
                                 threshold=3)
    matrix = score_from_matrix(inst)

    assert np.allclose(legacy["score"], matrix["score"])
    assert np.allclose(legacy["score_total"], matrix["score_total"])
    assert (matrix["score_type"] == legacy["score_type"]).all()
