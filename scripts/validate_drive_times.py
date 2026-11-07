"""External validation of the modeled drive times ("modeled times not yet validated").

Samples OD pairs from a city's road graph, compares our model's travel time
(length / posted maxspeed, see backend.objectives.coverage.add_travel_times)
against the public OSRM demo router, and reports error statistics.

Usage:
    venv/bin/python scripts/validate_drive_times.py --city freiburg
        [--pairs 50] [--osrm https://router.project-osrm.org] [--out <json>]

Needs a complete city graph cache (roads_*.parquet). Network is used only
when this script is run; OSM/OSRM usage stays small (<= ~60 requests paired
with `--sleep`, default 1.0 s per call, well inside the demo server's
courtesy range). Writes a JSON report + prints a human summary.
"""
import argparse
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests

from backend.config import city_config, utc_now_iso
from backend.data.prepare import gpd_read_boundary
from backend.objectives.coverage import add_travel_times
from backend.routing.graph import load_graph

OSRM_URL_DEFAULT = "https://router.project-osrm.org"
UA = {"User-Agent": "atlasis-drive-time-validation (academic, tiny)"}


def _pair_time_osrm(src, dst, osrm_url, timeout=(5, 60)):
    """OSRM duration (s) driving from (lon, lat) to (lon, lat); None if unroutable."""
    url = (f"{osrm_url}/route/v1/driving/"
           f"{src[0]},{src[1]};{dst[0]},{dst[1]}?overview=false")
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    js = r.json()
    if js.get("code") != "Ok" or not js.get("routes"):
        return None
    return float(js["routes"][0]["duration"])


def _lonlat(graph):
    """Node coordinates as (lon, lat); slim graphs may be cached projected
    (e.g. freiburg, EPSG:32632) or unprojected (epsg:4326)."""
    crs = graph.graph.get("crs", "epsg:4326")
    if "4326" in str(crs).lower():
        return {n: (d["x"], d["y"]) for n, d in graph.nodes(data=True)}
    from pyproj import Transformer
    t = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    return {n: tuple(round(v, 6) for v in t.transform(d["x"], d["y"]))
            for n, d in graph.nodes(data=True)}


def _sample_pairs(cfg, k, seed=7):
    """k (src, dst) node pairs inside the boundary, seeded, src != dst.

    Each 'ours_s' is the modeled drive time source -> target on the FORWARD
    graph (same direction OSRM drives); it's the same edge-time model
    add_travel_times gives the coverage matrices.
    """
    import random

    import networkx as nx
    from shapely.geometry import Point

    graph = add_travel_times(load_graph(cfg))
    lonlat = _lonlat(graph)
    boundary_4326 = gpd_read_boundary(cfg).to_crs(epsg=4326).union_all()
    inside = [n for n, (x, y) in lonlat.items()
              if boundary_4326.contains(Point(x, y))]
    rng = random.Random(seed)
    rng.shuffle(inside)
    if len(inside) < 2:
        raise SystemExit(f"not enough graph nodes inside boundary for {cfg.slug}")
    pairs = []
    while len(pairs) < k and len(inside) >= 2:
        a = rng.randrange(len(inside))
        b = rng.randrange(len(inside))
        if a == b:
            continue
        n1, n2 = inside[a], inside[b]
        try:
            t = nx.dijkstra_path_length(graph, n1, n2, weight="travel_time")
        except (nx.NetworkXError, nx.NetworkXNoPath):
            continue
        pairs.append({"src": lonlat[n1], "dst": lonlat[n2],
                      "ours_s": round(float(t), 1)})
        inside.pop(max(a, b))
    return pairs[:k], graph


def _validate(cfg, pairs, osrm_url):
    rows = []
    for i, p in enumerate(pairs):
        s, d = p["src"], p["dst"]
        try:
            osrm = _pair_time_osrm(s, d, osrm_url)
        except Exception as e:
            print(f"  [{i+1}] osrm error: {type(e).__name__}: {str(e)[:60]}")
            osrm = None
        if osrm is None:
            continue
        rows.append({"src_lon": s[0], "src_lat": s[1],
                     "dst_lon": d[0], "dst_lat": d[1],
                     "ours_s": p["ours_s"], "osrm_s": round(osrm, 1),
                     "delta_s": round(osrm - p["ours_s"], 1)})
        print(f"  [{i+1}/{len(pairs)}] ours {p['ours_s']:7.1f}s | osrm "
              f"{osrm:7.1f}s | delta {osrm - p['ours_s']:+7.1f}s")
        time.sleep(1.0)  # courtesy pacing for the free public router
    return rows


def _summarize(rows):
    if not rows:
        return None
    deltas = [r["delta_s"] for r in rows]
    osrm = [r["osrm_s"] for r in rows]
    return {
        "pairs_validated": len(rows),
        "mae_s": round(statistics.mean(abs(d) for d in deltas), 1),
        "median_abs_err_s": round(statistics.median(abs(d) for d in deltas), 1),
        "bias_s": round(statistics.mean(deltas), 1),  # +: OSRM slower than ours
        "mae_rel": round(statistics.mean(
            abs(r["delta_s"]) / max(r["osrm_s"], 1.0) for r in rows), 3),
        "osrm_median_s": round(statistics.median(osrm), 1),
    }


def _report(cfg, rows, summary, osrm_url, out):
    doc = {
        "slug": cfg.slug, "place": cfg.place,
        "generated_at": utc_now_iso(), "router": osrm_url,
        "summary": summary, "pairs": rows,
    }
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(doc, f, indent=2)
    print(f"\nReport written: {out}")
    if summary:
        print(f"pairs validated: {summary['pairs_validated']}, "
              f"MAE {summary['mae_s']}s ({summary['mae_rel']:.0%}), "
              f"bias {summary['bias_s']:+.1f}s (OSRM vs modeled)")


if __name__ == "__main__":
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("--city", required=True, help="data dir slug, e.g. freiburg")
    a.add_argument("--pairs", type=int, default=50)
    a.add_argument("--osrm", default=OSRM_URL_DEFAULT)
    a.add_argument("--seed", type=int, default=7)
    a.add_argument("--out", help="report json path (default "
                   "docs/analyses/validate-<slug>.json)")
    ns = a.parse_args()
    cfg = city_config(ns.city)
    pairs, graph = _sample_pairs(cfg, ns.pairs, seed=ns.seed)
    print(f"{cfg.slug}: validating {len(pairs)} OD pairs against {ns.osrm}")
    rows = _validate(cfg, pairs, ns.osrm)
    summary = _summarize(rows)
    _report(cfg, rows, summary, ns.osrm, ns.out or
            os.path.join("docs", "analyses", f"validate-{cfg.slug}.json"))
