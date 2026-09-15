"""Tests for the slim Parquet road-graph format (no network required)."""
import json
import os

import networkx as nx
import pytest

from backend.config import city_config
from backend.routing.graph import (_load_graphml_trimmed, has_slim,
                                   load_graph, load_graph_slim,
                                   save_graph_slim)


def _write_meta(cfg, epsg=32643):
    os.makedirs(cfg.raw_dir, exist_ok=True)
    with open(cfg.meta_path, "w") as f:
        json.dump({"slug": cfg.slug, "utm_epsg": epsg}, f)


@pytest.fixture
def graphml_city(tmp_path, monkeypatch):
    """A city whose roads.graphml is a genuine OSMnx-saved file.

    Built in-test from a small synthetic graph (repo GraphMLs are deleted
    after slim migration) so the ox.save_graphml -> ox.load_graphml path is
    exercised exactly as in production.
    """
    import osmnx as ox
    monkeypatch.setattr("backend.config.DATA_DIR", str(tmp_path))
    dst = tmp_path / "raw" / "mysuru"
    dst.mkdir(parents=True)

    # Small lattice near Mysuru with realistic edge attrs incl. maxspeed.
    G = nx.MultiDiGraph()
    G.graph.update({"crs": "epsg:4326", "simplified": True})
    lat0, lon0, step = 12.30, 76.65, 0.005
    for i in range(5):
        for j in range(5):
            n = 1000 + i * 5 + j
            G.add_node(n, x=lon0 + j * step, y=lat0 + i * step,
                       street_count=4)
    for i in range(5):
        for j in range(5):
            n = 1000 + i * 5 + j
            if j < 4:
                G.add_edge(n, n + 1, osmid=9000 + n, highway="residential",
                           length=550.0, oneway=False, reversed=False,
                           maxspeed="50", name="A Street")
                G.add_edge(n + 1, n, osmid=9000 + n, highway="residential",
                           length=550.0, oneway=False, reversed=True,
                           maxspeed="50", name="A Street")
            if i < 4:
                G.add_edge(n, n + 5, osmid=9500 + n, highway="primary",
                           length=560.0, oneway=True, reversed=False,
                           maxspeed="80", name="B Road")
    ox.save_graphml(G, filepath=dst / "roads.graphml")

    cfg = city_config("mysuru")
    _write_meta(cfg)
    return cfg


def test_roundtrip_matches_graphml(graphml_city):
    """Slim save/load must reproduce topology and lengths exactly."""
    G1 = _load_graphml_trimmed(graphml_city)
    save_graph_slim(G1, graphml_city)
    assert has_slim(graphml_city)

    G2 = load_graph_slim(graphml_city)
    assert set(G1.nodes) == set(G2.nodes)
    assert set(G1.edges(keys=True)) == set(G2.edges(keys=True))
    assert G1.graph["crs"] == G2.graph["crs"] == "epsg:4326"

    for u, v, k in list(G1.edges(keys=True))[:500]:
        d1, d2 = G1[u][v][k], G2[u][v][k]
        assert d1["length"] == pytest.approx(d2["length"], rel=1e-12)
        assert d1["oneway"] == d2["oneway"]
        assert d1["reversed"] == d2["reversed"]
        assert d1.get("osmid") == d2.get("osmid")
        for tag in ("highway", "maxspeed", "name"):
            assert d1.get(tag) == d2.get(tag), (u, v, k, tag)


def test_load_graph_prefers_slim_and_projects(graphml_city):
    save_graph_slim(_load_graphml_trimmed(graphml_city), graphml_city)
    slim = load_graph(graphml_city)  # slim exists now
    assert slim.graph["crs"].to_epsg() == 32643  # projected to UTM
    # and it equals the GraphML backend projected the same way
    import osmnx as ox
    legacy = ox.project_graph(_load_graphml_trimmed(graphml_city),
                              to_crs=graphml_city.crs)
    assert set(slim.nodes) == set(legacy.nodes)
    assert set(slim.edges(keys=True)) == set(legacy.edges(keys=True))


def test_graphml_fallback_when_no_slim(graphml_city, tmp_path):
    cfg = city_config("noslim")
    os.makedirs(os.path.dirname(cfg.roads_path), exist_ok=True)
    os.link(graphml_city.roads_path, cfg.roads_path)  # GraphML only
    _write_meta(cfg)
    assert not has_slim(cfg)
    G = load_graph(cfg)  # must fall back, project, and trim tags
    assert G.graph["crs"].to_epsg() == 32643
    for _, _, d in G.edges(data=True):
        assert set(d) <= {"length", "maxspeed", "name", "oneway", "reversed",
                          "osmid", "highway"}


def test_slim_writer_drops_pipeline_useless_tags(graphml_city):
    G = _load_graphml_trimmed(graphml_city)
    save_graph_slim(G, graphml_city)
    G2 = load_graph_slim(graphml_city)
    for _, _, d in G2.edges(data=True):
        assert "geometry" not in d
        assert set(d) <= {"length", "maxspeed", "name", "oneway", "reversed",
                          "osmid", "highway"}


def test_meta_recorded_in_schema(graphml_city):
    save_graph_slim(_load_graphml_trimmed(graphml_city), graphml_city)
    from backend.routing.graph import _read_meta
    meta = _read_meta(graphml_city.roads_nodes_path)
    assert meta["slim_format_version"] == "1"
    assert meta["crs"] == "epsg:4326"
    assert meta["simplified"] == "True"
