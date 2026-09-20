"""Tests for the E2SFCA accessibility index (no network).

Centers on an exact invariant: with uniform capacity and any decay,
sum_i P_i * A_i equals total capacity whenever every facility catchment has
positive population -- the marginal-accessibility identity of 2SFCA."""
import geopandas as gpd
import numpy as np
import pytest

from backend.objectives.twosfca import (compare_2sfca, compute_2sfca,
                                        gaussian_beta, gaussian_weight,
                                        print_2sfca)
from conftest import line_world, snap_patch


def _pts(xs):
    return gpd.GeoDataFrame(
        geometry=gpd.points_from_xy([float(x) for x in xs], np.zeros(len(xs))),
        crs="EPSG:32632")


# decay math

def test_gaussian_beta_calibrates_to_one_percent():
    beta = gaussian_beta(300)  # 5 min threshold
    w = gaussian_weight(300, beta)
    assert w == pytest.approx(0.01, rel=1e-6)


def test_gaussian_weight_monotone_and_bounded():
    t = np.array([0.0, 60.0, 300.0, 600.0])
    w = gaussian_weight(t, gaussian_beta(600))
    assert w[0] == 1.0
    assert (np.diff(w) < 0).all()          # strictly decreasing, never really zero
    assert w[-1] > 0                        


# exact identity on a line

def _access(pop_gdf, res):
    """Per-cell accessibility aligned to the input pop frame order."""
    out = res["cell_scores"]
    assert (out.index == pop_gdf.index).all()
    return out["accessibility"].values


def test_uniform_population_identity_holds(monkeypatch):
    """sum_i P_i A_i == total capacity (1 facility => exactly 1.0)."""
    graph, _cands, pop, hosp = line_world()
    snap_patch(monkeypatch)
    res = compute_2sfca(hosp, graph, pop, threshold=3)
    total = float((_access(pop, res) * pop["population"]).sum())
    assert total == pytest.approx(1.0)


def test_identity_holds_under_uneven_population_and_two_facilities(monkeypatch):
    graph, _c, pop, _h = line_world()
    snap_patch(monkeypatch)
    # Uneven population: all 100 people at node 6; facilities at nodes 0 and 2.
    pop.loc[:, "population"] = 0.0
    pop.loc[6, "population"] = 100.0
    res = compute_2sfca(_pts([0, 2]), graph, pop, threshold=10)
    total = float((_access(pop, res) * pop["population"]).sum())
    assert total == pytest.approx(2.0)  # uniform capacity 1 + 1
    # Both facilities reach node 6, so it carries both supply shares.
    assert _access(pop, res)[6] == pytest.approx(2.0 / 100.0)


def test_closer_cells_have_higher_accessibility(monkeypatch):
    """Strict decay with distance; zero beyond the threshold."""
    graph, _c, pop, _h = line_world(n=8)
    snap_patch(monkeypatch)
    # 1 s hops are far below the minute-calibrated default beta, so pass a
    # beta that makes 1-second differences matter.
    res = compute_2sfca(_pts([0]), graph, pop, threshold=6, beta_min=0.01)
    A = _access(pop, res)
    assert A[0] > A[2] > A[5] > A[6] > 0.0  # strict decay with distance
    assert A[7] == 0.0                       # 7 s > threshold: no access


def test_capacity_pooling_at_shared_node(monkeypatch):
    """Two facilities snapping to one node double the supply there."""
    graph, _c, pop, _h = line_world()
    snap_patch(monkeypatch)
    a1 = _access(pop, compute_2sfca(_pts([0]), graph, pop, threshold=3))
    a2 = _access(pop, compute_2sfca(_pts([0, 0]), graph, pop, threshold=3))
    assert np.allclose(a2, 2 * a1)


def test_zero_capacity_facility_ignored_and_capacity_mismatch_raises(monkeypatch):
    graph, _c, pop, _h = line_world()
    snap_patch(monkeypatch)
    res = compute_2sfca(_pts([0]), graph, pop, threshold=3, capacities=[0.0])
    assert (_access(pop, res) == 0).all()   # zero-capacity facility adds nothing
    with pytest.raises(ValueError):
        compute_2sfca(_pts([0]), graph, pop, threshold=3, capacities=[1.0, 2.0])


def test_summary_keys_and_unreachable_population(monkeypatch):
    graph, _c, pop, hosp = line_world()
    snap_patch(monkeypatch)
    res = compute_2sfca(hosp, graph, pop, threshold=3)
    for key in ("pop_weighted_index", "mean_cell_index", "beta_min",
                "pop_with_access_pct", "total_population", "num_facilities",
                "cell_scores"):
        assert key in res
    assert res["pop_with_access_pct"] == pytest.approx(50.0)  # nodes 0-3 of 8
    assert res["total_population"] == pytest.approx(80.0)


def test_compare_and_print_helpers(capsys):
    base = {"pop_weighted_index": 0.5}
    opt = {"pop_weighted_index": 0.75}
    out = compare_2sfca(base, opt)
    assert out["delta_pct"] == pytest.approx(50.0)
    s = {"beta_min": 5.4, "num_facilities": 3, "pop_weighted_index": 0.1,
         "mean_cell_index": 0.1, "max_cell_index": 0.2,
         "pop_with_access_pct": 90.0, "pop_at_zero": 10.0,
         "total_population": 100.0}
    print_2sfca("T", s, threshold=300)
    text = capsys.readouterr().out
    assert "2SFCA" in text and "cell-resolution artifact" in text
