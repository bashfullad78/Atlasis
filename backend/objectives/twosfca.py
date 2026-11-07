"""Two-step floating catchment area (2SFCA) accessibility index.

The binary threshold metric ("within 15 min of a hospital: yes/no") cannot
distinguish two cells that are both covered but 1 vs 14 minutes away, and it
cannot represent facility load. The E2SFCA index (Luo & Qi 2009) with a
Gaussian decay over travel time does both:

Step 1 (facility supply): for each facility j, its weighted catchment
    S_j = sum_i W(t_i->j) * P_i
    over population nodes i within the threshold, where W is the Gaussian
    weight exp(-(t/60)^2 / beta) on travel time in minutes. With uniform
    capacity (the default, since bed data is not modeled), S_j approximates
    the population load competing for facility j.

Step 2 (cell demand): each population cell's accessibility is the decayed
    share of nearby capacity,
    A_i = sum_{j reachable from i} W(t_i->j) * cap_j / S_j.

The city index is the population-weighted mean of A_i. A useful exact
invariant (used in tests): sum_i P_i A_i = total capacity, for ANY decay and
population distribution, as long as every facility has S_j > 0.

Gaussian beta: W(tau) ~ 0.01 at the threshold tau (Luo & Qi's calibration),
so beta = tau_min^2 / ln(100); pass ``beta_min`` to override.

Directions follow the pipeline convention: dijkstra on the REVERSED graph
from facility nodes yields person -> facility travel times, the same
semantics as ``objectives.baseline``.
"""
import math

import networkx as nx
import numpy as np
import osmnx as ox

from backend.objectives.coverage import (
    TRAVEL_TIME_THRESHOLD,
    add_travel_times, reverse_with_travel_times,
    population_by_node,
)


def gaussian_beta(threshold):
    """Gaussian beta (min^2) so that W(threshold) ~ 0.01."""
    tau_min = threshold / 60
    return tau_min ** 2 / math.log(100)


def gaussian_weight(times_s, beta_min):
    """Gaussian distance-decay weight on travel time in seconds."""
    t_min = np.asarray(times_s, dtype=float) / 60
    return np.exp(-(t_min ** 2) / beta_min)


def compute_2sfca(facilities, graph, pop_gdf, threshold=TRAVEL_TIME_THRESHOLD,
                  beta_min=None, capacities=None, graph_rev=None):
    """E2SFCA accessibility per population cell plus city-level summary.

    ``facilities``: point geometries in the graph's (projected) CRS --
    existing hospitals, or existing + selected sites for the optimized view.
    ``capacities``: optional per-facility capacity (e.g. beds); uniform 1s
    model presence only, which is the pipeline's current capability.
    ``graph_rev``: optional prebuilt reversed travel-time graph
    (``reverse_with_travel_times``) shared across the run's scenarios.
    Returns a dict with per-cell scores (``cell_scores``) and summaries.
    """
    graph = add_travel_times(graph)
    graph_rev = graph_rev if graph_rev is not None else graph.reverse(copy=True)

    node_pop = population_by_node(pop_gdf, graph_rev)
    pop_ids = np.array(sorted(node_pop))
    w = np.array([node_pop[n] for n in pop_ids], dtype=float)
    total_pop = w.sum()
    idx = {n: i for i, n in enumerate(pop_ids)}

    beta = beta_min if beta_min is not None else gaussian_beta(threshold)

    # NO dedup: facilities sharing a node pool their capacity there (two
    # facilities at one road node are jointly more supply, not one).
    fac_nodes = ox.distance.nearest_nodes(
        graph_rev, facilities.geometry.x.values, facilities.geometry.y.values
    )
    caps = np.ones(len(facilities)) if capacities is None else np.asarray(
        capacities, dtype=float)
    if len(caps) != len(facilities):
        raise ValueError("capacities must align with facilities rows")
    cap_by_node = {}
    for node, cap in zip(fac_nodes, caps):
        cap_by_node[node] = cap_by_node.get(node, 0.0) + float(cap)

    # Step 1 + step 2 from the same facility-side Dijkstras: dist(node)
    # holds person -> facility travel times for every reachable pop node.
    A = np.zeros(len(pop_ids))
    for node, cap in cap_by_node.items():
        dist = nx.single_source_dijkstra_path_length(
            graph_rev, node, cutoff=threshold, weight="travel_time")
        pairs = []
        s_j = 0.0
        for n, t in dist.items():
            i = idx.get(n)
            if i is None:
                continue
            wgt = float(gaussian_weight(t, beta))
            pairs.append((i, wgt))
            s_j += wgt * w[i]
        if s_j <= 0:
            continue  # facility with no reachable population: no supply share
        for i, wgt in pairs:
            A[i] += wgt * cap / s_j

    pop_at_zero = float(w[np.isin(pop_ids, list(cap_by_node))].sum())
    pop_with_access = float(w[A > 0].sum())

    cell_scores = pop_gdf.copy()
    # A is per graph node (population cells aggregate onto nodes); map each
    # cell to its node so the export is per cell. Cells sharing a node share
    # the index -- correct 2SFCA semantics (accessibility is a location
    # property, and the aggregation is exactly the snapping used above).
    cell_nodes = ox.distance.nearest_nodes(
        graph_rev, pop_gdf.geometry.x.values, pop_gdf.geometry.y.values
    )
    cell_scores["accessibility"] = A[np.array([idx[n] for n in cell_nodes])]

    return {
        "beta_min": beta,
        "pop_weighted_index": float((A * w).sum() / total_pop) if total_pop else 0.0,
        "mean_cell_index": float(A.mean()),
        "median_cell_index": float(np.median(A)),
        "min_cell_index": float(A.min()),
        "max_cell_index": float(A.max()),
        "pop_with_access_pct": (pop_with_access / total_pop) * 100 if total_pop else 0.0,
        "pop_at_zero": pop_at_zero,
        "total_population": float(total_pop),
        "num_facilities": len(facilities),
        "cell_scores": cell_scores,
    }


def print_2sfca(title, s, threshold=TRAVEL_TIME_THRESHOLD):
    """Readable report of a 2SFCA summary dict."""
    print(f"\n--- {title} ---")
    print(
        f"  2SFCA index (Gaussian decay, beta {s['beta_min']:.2f} min^2, "
        f"{s['num_facilities']} facilities): pop-weighted {s['pop_weighted_index']:.5f} "
        f"| cell mean {s['mean_cell_index']:.5f} | cell max {s['max_cell_index']:.5f}"
    )
    print(
        f"  Population with a facility within {threshold / 60:.0f} min: "
        f"{s['pop_with_access_pct']:.1f}% (binary coverage; the index adds decay intensity)"
    )
    if s["pop_at_zero"] > 0:
        print(
            f"    ({s['pop_at_zero']:,.0f} people share a 1-km cell centroid with a "
            f"facility -> maximum weight; cell-resolution artifact)"
        )


def compare_2sfca(baseline, optimized):
    """Relative change of the population-weighted index; prints a summary."""
    base_idx = baseline["pop_weighted_index"]
    opt_idx = optimized["pop_weighted_index"]
    delta_pct = (opt_idx - base_idx) / base_idx * 100 if base_idx > 0 else 0.0
    print(f"\n=== 2SFCA baseline vs optimized ===")
    print(
        f"  Pop-weighted index: {base_idx:.5f} -> {opt_idx:.5f} "
        f"({delta_pct:+.1f}% relative change)"
    )
    return {"delta_pct": delta_pct}


if __name__ == "__main__":
    from backend.config import city_config
    from backend.data.population import load_population_grid
    from backend.gis.boundary import load_boundary
    from backend.gis.hospitals import load_hospitals
    from backend.routing.graph import load_graph

    cfg = city_config()
    s = compute_2sfca(load_hospitals(cfg), load_graph(cfg),
                      load_population_grid(cfg))
    print_2sfca("2SFCA (existing facilities)", s)
