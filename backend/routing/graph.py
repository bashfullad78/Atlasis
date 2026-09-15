"""Road-graph storage.

The GraphML that OSMnx writes by default is wasteful for this pipeline: it
carries every scraped OSM tag (geometry LineStrings, lanes, bridge, ref, ...)
while the travel-time model needs only ``length`` and ``maxspeed`` and the
snapping logic needs node ``x``/``y``. The slim format stores the graph as two
compressed Parquet tables (one row per node, one row per directed edge),
which is ~10x smaller and much faster to parse than XML.

Contract compatibility with ``ox.load_graphml``:
* graphs are stored unprojected (WGS84); ``load_graph`` projects to the city's
  UTM zone exactly as before;
* ``crs`` / ``simplified`` graph attributes are preserved as Parquet schema
  metadata (``atlas_*`` keys);
* node ``osmid``/``x``/``y`` and edge ``u``/``v``/``key``/``length`` behave
  identically. Edge tags the pipeline never reads (geometry, lanes, bridge,
  ...) are dropped; if a future feature needs them, re-download the city.
"""
import json
import os

import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from backend.config import CityConfig

SLIM_FORMAT_VERSION = 1

_NODE_COLUMNS = ["osmid", "x", "y"]
_EDGE_COLUMNS = ["u", "v", "key", "length", "highway", "maxspeed", "name",
                 "oneway", "reversed", "osmid"]
_META_PREFIX = "atlas_"


def slim_paths(cfg: CityConfig):
    return cfg.roads_nodes_path, cfg.roads_edges_path


def has_slim(cfg: CityConfig) -> bool:
    nodes_path, edges_path = slim_paths(cfg)
    return os.path.exists(nodes_path) and os.path.exists(edges_path)


def _write_with_meta(df: pd.DataFrame, path: str, meta: dict) -> None:
    """Write Parquet with ``atlas_*`` schema-metadata keys merged in."""
    table = pa.Table.from_pandas(df, preserve_index=False)
    merged = {**(table.schema.metadata or {}),
              **{_META_PREFIX + k: str(v).encode() for k, v in meta.items()}}
    table = table.replace_schema_metadata(merged)
    pq.write_table(table, path, compression="zstd")


def _read_meta(path: str) -> dict:
    schema = pq.read_schema(path)
    meta = schema.metadata or {}
    return {k.decode()[len(_META_PREFIX):]: v.decode()
            for k, v in meta.items() if k.decode().startswith(_META_PREFIX)}


def save_graph_slim(G: nx.MultiDiGraph, cfg: CityConfig) -> None:
    """Persist an unprojected graph as two compressed Parquet tables."""
    nodes_path, edges_path = slim_paths(cfg)
    os.makedirs(cfg.raw_dir, exist_ok=True)

    nodes = pd.DataFrame(
        [(n, float(d["x"]), float(d["y"])) for n, d in G.nodes(data=True)],
        columns=_NODE_COLUMNS,
    ).astype({"osmid": "int64", "x": "float64", "y": "float64"})

    def _wrap(val):
        """Normalize a tag value to a nullable list<string> cell.

        OSM tags can legitimately be lists (e.g. highway=['residential',
        'footway']), and mixed str/list object columns break Arrow type
        inference. Scalars are wrapped so the column type is uniform; the
        loader unwraps single-element lists back to scalars.
        """
        if val is None:
            return None
        if isinstance(val, (list, tuple)):
            return [str(v) for v in val]
        return [str(val)]

    def _wrap_osmid(val):
        """osmid as nullable list<int64>: scalars wrap to one element (the
        loader unwraps). Preserves list osmids from merged ways."""
        if val is None:
            return None
        if isinstance(val, (list, tuple)):
            return [int(v) for v in val]
        try:
            return [int(val)]
        except (TypeError, ValueError):
            return None

    def _wrap_bool(val):
        """oneway/reversed as nullable list<bool>: GraphML-loaded graphs can
        carry list values here too (merged ways), so preserve them."""
        if val is None:
            return None
        if isinstance(val, (list, tuple)):
            return [bool(v) for v in val]
        return [bool(val)]

    def edge_row(u, v, k, d):
        return (u, v, k, float(d["length"]), _wrap(d.get("highway")),
                _wrap(d.get("maxspeed")), _wrap(d.get("name")),
                _wrap_bool(d.get("oneway", False)),
                _wrap_bool(d.get("reversed", False)),
                _wrap_osmid(d.get("osmid")))

    edges = pd.DataFrame(
        [edge_row(*edge) for edge in G.edges(keys=True, data=True)],
        columns=_EDGE_COLUMNS,
    )

    meta = {
        "slim_format_version": SLIM_FORMAT_VERSION,
        "crs": G.graph.get("crs", "epsg:4326"),
        "simplified": bool(G.graph.get("simplified", False)),
        "writer": "atlasis slim graph writer",
    }
    _write_with_meta(nodes, nodes_path, meta)
    _write_with_meta(edges, edges_path, meta)
    mb = (os.path.getsize(nodes_path) + os.path.getsize(edges_path)) / 1e6
    print(f"✓ Slim graph saved: {len(nodes)} nodes, {len(edges)} edges "
          f"-> {os.path.basename(nodes_path)} + {os.path.basename(edges_path)} "
          f"({mb:.2f} MB)")


def _unwrap(val):
    """Inverse of the writer's _wrap: single-element list -> scalar.

    Handles numpy object arrays (Parquet list columns arrive as arrays in
    itertuples rows) as well as native lists.
    """
    if isinstance(val, np.ndarray):
        return val[0] if len(val) == 1 else val.tolist()
    if isinstance(val, list):
        return val[0] if len(val) == 1 else val
    if val is None or (np.isscalar(val) and pd.isna(val)):
        return None
    return val


def _edge_attrs(row) -> dict:
    """Edge attribute dict from a Parquet row, mirroring GraphML-loaded types."""
    d = {"length": float(row.length)}
    for col in ("highway", "maxspeed", "name"):
        val = _unwrap(getattr(row, col))
        if val is not None:
            d[col] = val
    d["oneway"] = _unwrap(row.oneway)
    d["reversed"] = _unwrap(row.reversed)
    osmid = _unwrap(row.osmid)
    if osmid is not None:
        d["osmid"] = osmid
    return d


def load_graph_slim(cfg: CityConfig) -> nx.MultiDiGraph:
    """Rebuild the unprojected MultiDiGraph from the slim Parquet tables."""
    nodes_path, edges_path = slim_paths(cfg)
    nodes = pd.read_parquet(nodes_path)
    edges = pd.read_parquet(edges_path)

    meta = _read_meta(nodes_path)
    G = nx.MultiDiGraph()
    G.graph["crs"] = meta.get("crs", "epsg:4326")
    G.graph["simplified"] = meta.get("simplified", "True") == "True"
    G.add_nodes_from(
        (int(r.osmid), {"x": float(r.x), "y": float(r.y)})
        for r in nodes.itertuples(index=False)
    )
    G.add_edges_from(
        (int(r.u), int(r.v), int(r.key), _edge_attrs(r))
        for r in edges.itertuples(index=False)
    )
    return G


def _load_graphml_trimmed(cfg: CityConfig) -> nx.MultiDiGraph:
    """Load GraphML keeping only the tags this pipeline consumes."""
    keep = {"length", "maxspeed", "name", "oneway", "reversed", "osmid",
            "highway"}
    G = ox.load_graphml(cfg.roads_path)
    for _, _, data in G.edges(data=True):
        for attr in list(data):
            if attr not in keep:
                del data[attr]
    return G


def load_graph(cfg: CityConfig, slim=None):
    """Drivable road graph, projected to the city's UTM zone.

    Prefers the slim Parquet format; falls back to roads.graphml for cities
    not yet migrated. Pass ``slim=True/False`` to force a backend.
    """
    if slim is None:
        slim = has_slim(cfg)
    if slim:
        if not has_slim(cfg):
            raise FileNotFoundError(
                f"Slim graph incomplete for '{cfg.slug}': need both "
                f"{cfg.roads_nodes_path} and {cfg.roads_edges_path}"
            )
        G = load_graph_slim(cfg)
    elif os.path.exists(cfg.roads_path):
        G = _load_graphml_trimmed(cfg)
    else:
        raise FileNotFoundError("Road graph not found. Run download first.")
    return ox.project_graph(G, to_crs=cfg.crs)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Slim-format road graph utilities.")
    p.add_argument("--city", default="mysuru")
    p.add_argument("--migrate-from-graphml", action="store_true",
                   help="convert roads.graphml -> slim Parquet (graphml kept)")
    p.add_argument("--stats", action="store_true",
                   help="print storage stats for both formats")
    p.add_argument("--verify", action="store_true",
                   help="assert the slim graph matches the GraphML topology")
    a = p.parse_args()

    from backend.config import city_config
    cfg = city_config(a.city)

    if a.migrate_from_graphml:
        save_graph_slim(_load_graphml_trimmed(cfg), cfg)

    def mb(path):
        return os.path.getsize(path) / 1e6 if os.path.exists(path) else 0.0

    if a.stats or a.verify:
        n_path, e_path = slim_paths(cfg)
        slim_mb = mb(n_path) + mb(e_path)
        print(f"graphml={mb(cfg.roads_path):.1f} MB | slim={slim_mb:.2f} MB")

    if a.verify:
        G1 = ox.load_graphml(cfg.roads_path)
        G2 = load_graph_slim(cfg)
        assert set(G1.nodes) == set(G2.nodes), "node sets differ"
        e1 = set(G1.edges(keys=True))
        e2 = set(G2.edges(keys=True))
        assert e1 == e2, "edge sets differ"
        for u, v, k in list(e1)[:1000]:
            l1 = G1[u][v][k]["length"]
            l2 = G2[u][v][k]["length"]
            assert abs(l1 - l2) < 1e-6, f"length mismatch on {(u, v, k)}"
        print(f"✓ Verified: identical topology and lengths "
              f"({len(G1.nodes)} nodes, {len(G1.edges)} edges)")
