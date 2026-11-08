"""Offline tag coverage per city: which share of road edges gets its travel
time from a mapped OSM ``maxspeed`` instead of the flat 40 km/h default.

The OSRM calibration in ``docs/analyses/validation-v0.4.md`` splits cities into tag-rich (one-sided
~24% optimism) and tag-sparse (no usable correction). This turns that
categorical split into a measured variable: how much of each network the
speed model can actually inform.

Self check: the local parse is compared edge-case by edge-case against the
pipeline's real ``backend.objectives.coverage.add_travel_times``; a mismatch
fails the run. Usage: ``venv/bin/python scripts/tag_coverage.py``
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.objectives.coverage import DEFAULT_SPEED_KMH, add_travel_times  # noqa: E402

RAW = Path(ROOT) / "data" / "raw"
VALIDATE = Path(ROOT) / "docs" / "analyses"
# same rule as the validation protocol: its streets are synthetic
SKIP = {"testville"}


def mapped_speed(value) -> float | None:
    """Speed in km/h the model reads off this tag, or ``None`` when it falls
    back to the default — the exact parse ``add_travel_times`` performs
    (list -> first entry, str -> first token as float; 'none'/missing/
    unparseable -> default)."""
    if isinstance(value, (list, tuple, np.ndarray)):
        if len(value) == 0:
            return None
        value = value[0]
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return float(value.split()[0])
        except (ValueError, IndexError):
            return None
    return float(value) if value else None


def uses_mapped_speed(value) -> bool:
    return mapped_speed(value) is not None


def check_against_pipeline() -> None:
    """Pin the local parse to the real travel-time model on edge cases."""
    cases = ["50", ["30", "50"], "none", "50 mph", None, "", 50]
    g = nx.MultiDiGraph()
    for i, v in enumerate(cases):
        kwargs = {} if v is None else {"maxspeed": v}
        g.add_edge(0, i, length=1000.0, **kwargs)
    add_travel_times(g)
    for i, v in enumerate(cases):
        speed = mapped_speed(v)
        expected = 1000.0 / (((speed if speed is not None
                               else DEFAULT_SPEED_KMH) * 1000) / 3600)
        got = g.edges[0, i, 0]["travel_time"]
        assert abs(got - expected) < 1e-9, (v, got, expected)


def city_dirs() -> list[Path]:
    """One directory per place; the pre-v0.3 fetch path left duplicate caches,
    so keep the most complete one per place."""
    by_place: dict[str, list[Path]] = {}
    for meta in sorted(RAW.glob("*/meta.json")):
        if meta.parent.name in SKIP:
            continue
        m = json.loads(meta.read_text())
        by_place.setdefault(m.get("place") or meta.parent.name, []).append(meta.parent)
    return [max(ds, key=lambda d: (len(list(d.iterdir())), d.name))
            for ds in by_place.values()]


def main() -> None:
    check_against_pipeline()
    rows = []
    for d in sorted(city_dirs()):
        edges = pd.read_parquet(d / "roads_edges.parquet")
        mapped = edges["maxspeed"].map(uses_mapped_speed)
        total = len(edges)
        assert total > 0 and mapped.sum() <= total, d
        vfile = VALIDATE / f"validate-{d.name}.json"
        bias = (json.loads(vfile.read_text())["summary"]["bias_s"]
                if vfile.exists() else None)
        rows.append({
            "city": d.name,
            "edges": total,
            "mapped_pct": 100 * mapped.mean(),
            "length_pct": 100 * edges.loc[mapped, "length"].sum() / edges["length"].sum(),
            "bias": bias,
        })
    assert len(rows) >= 4, "expected the cached real cities"

    print("| city | edges | mapped maxspeed | ... by length | OSRM bias (s) |")
    print("|---|---:|---:|---:|---:|")
    for r in sorted(rows, key=lambda r: -r["mapped_pct"]):
        bias = f"{r['bias']:+.1f}" if r["bias"] is not None else "--"
        print(f"| {r['city']} | {r['edges']:,} | {r['mapped_pct']:.1f}% "
              f"| {r['length_pct']:.1f}% | {bias} |")


if __name__ == "__main__":
    main()
