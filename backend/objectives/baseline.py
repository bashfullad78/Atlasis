"""Accessibility measurement (baseline vs. with recommended sites added).

Uses one multi-source Dijkstra per scenario on the reversed graph (paths model
person -> facility) instead of one shortest-path query per facility per cell.
"""
import networkx as nx

from backend.objectives.coverage import (
    TRAVEL_TIME_THRESHOLD,
    add_travel_times,
    nearest_nodes_unique,
    population_by_node,
)


def _weighted_time_stats(times_pops):
    """Population-weighted min/median/mean/max of travel times (seconds)."""
    total_pop = sum(p for _, p in times_pops)
    min_t = min(t for t, _ in times_pops)
    max_t = max(t for t, _ in times_pops)
    mean_t = sum(t * p for t, p in times_pops) / total_pop

    acc, median_t = 0.0, max_t
    for t, p in sorted(times_pops):
        acc += p
        if acc >= total_pop / 2:
            median_t = t
            break

    pop_at_zero = sum(p for t, p in times_pops if t == 0)
    return {"min_s": min_t, "median_s": median_t, "mean_s": mean_t,
            "max_s": max_t, "pop_at_zero": pop_at_zero}


def accessibility_stats(source_geometries, graph, pop_gdf, threshold=TRAVEL_TIME_THRESHOLD):
    """Population-weighted accessibility to the nearest of ``source_geometries``."""
    graph = add_travel_times(graph)
    graph_rev = graph.reverse(copy=True)

    source_nodes = nearest_nodes_unique(source_geometries, graph_rev)
    if not source_nodes:
        raise ValueError("accessibility_stats needs at least one facility geometry")

    dist = nx.multi_source_dijkstra_path_length(graph_rev, source_nodes, weight="travel_time")
    node_pop = population_by_node(pop_gdf, graph_rev)
    total_pop = sum(node_pop.values())

    times_pops, unreachable_pop = [], 0.0
    for node, pop in node_pop.items():
        t = dist.get(node)
        if t is None:
            unreachable_pop += pop
        else:
            times_pops.append((t, pop))

    stats = _weighted_time_stats(times_pops)
    covered_pop = sum(p for t, p in times_pops if t <= threshold)

    return {
        "coverage_pct": (covered_pop / total_pop) * 100 if total_pop else 0.0,
        "mean_travel_time_min": stats["mean_s"] / 60,
        "median_travel_time_min": stats["median_s"] / 60,
        "min_travel_time_min": stats["min_s"] / 60,
        "max_travel_time_min": stats["max_s"] / 60,
        "pop_at_zero_min": stats["pop_at_zero"],
        "total_population": total_pop,
        "covered_population": covered_pop,
        "unreachable_population": unreachable_pop,
        "num_facilities": len(source_nodes),
    }


def print_stats(title, s, threshold=TRAVEL_TIME_THRESHOLD):
    """Readable report of an accessibility stats dict."""
    print(f"\n--- {title} ---")
    print(
        f"  People within {threshold / 60:.0f} min of a hospital: "
        f"{s['coverage_pct']:.1f}%  ({s['covered_population']:,.0f} / {s['total_population']:,.0f})"
    )
    print(
        f"  Drive time to nearest hospital (min):  min {s['min_travel_time_min']:.1f}  "
        f"| median {s['median_travel_time_min']:.1f}  | mean {s['mean_travel_time_min']:.1f}  "
        f"| max {s['max_travel_time_min']:.1f}"
    )
    if s["pop_at_zero_min"] > 0:
        print(
            f"    ({s['pop_at_zero_min']:,.0f} people share a 1-km cell centroid with a "
            f"hospital -> 0.0 min is a cell-resolution artifact)"
        )
    if s["unreachable_population"] > 0:
        share = s["unreachable_population"] / s["total_population"] * 100
        print(
            f"    {s['unreachable_population']:,.0f} people ({share:.1f}%) have no road "
            f"path to any hospital and are excluded from the times above"
        )


def compute_baseline(hospitals, graph, pop_gdf, threshold=TRAVEL_TIME_THRESHOLD):
    """Accessibility using only the facilities that already exist."""
    return accessibility_stats(hospitals.geometry, graph, pop_gdf, threshold=threshold)


def compute_optimized(top_candidates, hospitals, graph, pop_gdf, threshold=TRAVEL_TIME_THRESHOLD):
    """Accessibility with existing facilities + the Rank-1 recommended site."""
    best = top_candidates.iloc[0]
    geometries = list(hospitals.geometry) + [best.geometry]
    return accessibility_stats(geometries, graph, pop_gdf, threshold=threshold)


def compare_baseline_optimized(baseline, optimized):
    delta_coverage = optimized["coverage_pct"] - baseline["coverage_pct"]
    delta_mean = baseline["mean_travel_time_min"] - optimized["mean_travel_time_min"]
    delta_median = baseline["median_travel_time_min"] - optimized["median_travel_time_min"]
    newly_covered = optimized["covered_population"] - baseline["covered_population"]

    print(f"\n=== Baseline vs Optimized (existing + best new site) ===")
    print(f"  Coverage improvement:       +{delta_coverage:.1f}%   (+{newly_covered:,.0f} people)")
    print(f"  Median travel time change:  {delta_median:+.1f} min  (mean {delta_mean:+.1f} min)")
    if delta_coverage >= 1:
        verdict = "✓ Meaningful improvement"
    elif baseline["coverage_pct"] >= 99.9:
        verdict = (
            "⚠ City already ~fully covered within the threshold; no site can add people. "
            "Lower the threshold (e.g. 5-min emergency access), restrict the facility class, "
            "or model capacity instead of mere presence."
        )
    else:
        verdict = "⚠ Marginal: try a denser candidate grid or looser constraints"
    print(f"  {verdict}")

    return {
        "delta_coverage_pct": delta_coverage,
        "delta_travel_time_min": delta_mean,
        "delta_median_travel_time_min": delta_median,
        "newly_covered_population": newly_covered,
    }


if __name__ == "__main__":
    from backend.config import city_config
    from backend.gis.boundary import load_boundary
    from backend.gis.hospitals import load_hospitals
    from backend.routing.graph import load_graph
    from backend.data.population import load_population_grid

    cfg = city_config()
    pop_gdf = load_population_grid(cfg)
    baseline = compute_baseline(load_hospitals(cfg), load_graph(cfg), pop_gdf)
    print_stats("Baseline Accessibility (existing facilities only)", baseline)
