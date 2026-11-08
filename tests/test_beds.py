"""Beds-capacity wiring (2SFCA) tests: fallback semantics, invariance, scale."""
import networkx as nx
import numpy as np
import geopandas as gpd
import pytest
from shapely.geometry import Point

from backend.objectives.twosfca import compute_2sfca
from backend.gis.hospitals import beds_capacities
from backend.objectives.coverage import reverse_with_travel_times


def _graph():
    g = nx.MultiDiGraph(crs="EPSG:32632")
    for i in range(8):
        g.add_node(i, x=i * 250.0, y=0.0)
    for i in range(7):
        g.add_edge(i, i + 1, key=0, length=250.0)
        g.add_edge(i + 1, i, key=0, length=250.0)
    return g


def _pop():
    return gpd.GeoDataFrame(
        {"population": [20.0] * 8},
        geometry=[Point(i * 250.0, 0.0) for i in range(8)], crs="EPSG:32632")


def _hospitals(beds):
    pts = [Point(i * 250.0, 0.0) for i in range(len(beds))]
    return gpd.GeoDataFrame({"beds": beds}, geometry=pts, crs="EPSG:32632")


def test_beds_numeric_string_and_missing_values():
    caps = beds_capacities(_hospitals([150, "75", 0, np.nan, None]))
    assert caps == pytest.approx([150, 75, 1.0, 1.0, 1.0])


def test_beds_column_absent_is_uniform_presence_only():
    gdf = gpd.GeoDataFrame(geometry=[Point(0, 0), Point(1, 1)], crs="EPSG:32632")
    assert beds_capacities(gdf) == pytest.approx([1.0, 1.0])


def test_2sfca_sum_invariant_holds_with_real_beds():
    """Module invariant: sum_i P_i A_i == total capacity, any capacities."""
    hospitals = _hospitals([7, 3])
    caps = beds_capacities(hospitals)
    result = compute_2sfca(hospitals, graph=_graph(), pop_gdf=_pop(),
                           threshold=300, capacities=caps)
    cell_scores = result["cell_scores"]
    # cell_scores maps each pop cell to its graph node's accessibility
    assert "accessibility" in cell_scores.columns
    total = float((cell_scores["population"] * cell_scores["accessibility"]).sum())
    assert total == pytest.approx(float(caps.sum()), rel=1e-6)


def test_2sfca_scales_linearly_with_beds():
    """Beds scale the accessibility index; decay/threshold semantics don't move."""
    graph = reverse_with_travel_times(_graph())
    kw = dict(graph=graph, pop_gdf=_pop(), threshold=300)
    one = compute_2sfca(_hospitals([1]), capacities=np.array([1.0]), **kw)
    ten = compute_2sfca(_hospitals([10]), capacities=np.array([10.0]), **kw)
    assert np.allclose(ten["cell_scores"]["accessibility"],
                       10 * one["cell_scores"]["accessibility"])
    assert ten["pop_with_access_pct"] == pytest.approx(one["pop_with_access_pct"])


def test_2sfca_without_beds_column_matches_capacity_one_runs():
    """No beds => exactly the old presence-only result."""
    graph = reverse_with_travel_times(_graph())
    kw = dict(graph=graph, pop_gdf=_pop(), threshold=300)
    no_beds = compute_2sfca(_hospitals([np.nan, np.nan]), **kw)
    ones = compute_2sfca(_hospitals([np.nan, np.nan]),
                         capacities=np.array([1.0, 1.0]), **kw)
    assert np.allclose(no_beds["cell_scores"]["accessibility"],
                       ones["cell_scores"]["accessibility"])
