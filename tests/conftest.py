"""Shared fixtures and builders for synthetic graph tests (no network)."""
import networkx as nx
import numpy as np
import geopandas as gpd


def snap_int(x, y):
    """Stand-in for ox.distance.nearest_nodes on the synthetic line graph."""
    vals = np.asarray(x, dtype=int)
    return vals.item() if vals.ndim == 0 else vals


def line_world(n=8, hospital_at=0, pop_per_node=10.0):
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
        {"population": np.full(n, pop_per_node)},
        geometry=gpd.points_from_xy(xs, np.zeros(n)), crs="EPSG:32632")
    hospitals = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy([float(hospital_at)], [0.0]),
        crs="EPSG:32632")
    return g, candidates, pop, hospitals


def snap_patch(monkeypatch):
    """Patch ox.distance.nearest_nodes for synthetic (already-snapped) input."""
    monkeypatch.setattr("osmnx.distance.nearest_nodes",
                        lambda graph, x, y: snap_int(x, y))
