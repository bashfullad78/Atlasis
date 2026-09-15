from pyproj import Transformer

from backend.config import CityConfig
from backend.gis.boundary import load_boundary
from backend.gis.hospitals import load_hospitals
from backend.routing.graph import load_graph
from backend.candidates.generate import generate_candidates
from backend.constraints.filters import apply_all_filters
from backend.objectives.coverage import add_travel_times, score_all_candidates_population
from backend.objectives.baseline import (
    compute_baseline,
    compute_optimized,
    compare_baseline_optimized,
    print_stats,
)
from backend.data.population import load_population_grid
from backend.optimizers.ranker import get_top_candidates, save_results


def run_pipeline(cfg: CityConfig, spacing_m=None, top_n=None, threshold=None):
    spacing_m = spacing_m or cfg.spacing_m
    top_n = top_n or cfg.top_n
    threshold = threshold or cfg.threshold_sec

    print(f"\n=== ATLASIS Pipeline: {cfg.place} ===\n")

    print("[1/7] Loading boundary...")
    boundary = load_boundary(cfg)

    print("[2/7] Loading existing facilities...")
    hospitals = load_hospitals(cfg)
    print(f"      Found {len(hospitals)} existing facilities")

    print("[3/7] Loading road graph and adding travel times...")
    graph = add_travel_times(load_graph(cfg))
    print(f"      Nodes: {len(graph.nodes)} | Edges: {len(graph.edges)}")

    print("[4/7] Generating candidate sites...")
    candidates = generate_candidates(cfg, spacing_m=spacing_m)

    print("[5/7] Applying hard constraints...")
    candidates = apply_all_filters(candidates, hospitals, graph, cfg)

    print(f"[6/7] Scoring candidates (marginal {threshold / 60:.0f}-min drive coverage)...")
    pop_gdf = load_population_grid(cfg)
    scored = score_all_candidates_population(
        candidates, pop_gdf, graph, hospitals=hospitals, threshold=threshold
    )
    top = get_top_candidates(scored, top_n=top_n)
    save_results(top, cfg)

    t = Transformer.from_crs(cfg.crs, "EPSG:4326", always_xy=True)
    print(f"\nRecommended sites (people newly within {threshold / 60:.0f} min of a facility):")
    for _, row in top.iterrows():
        lng, lat = t.transform(row.geometry.x, row.geometry.y)
        print(
            f"  Rank {int(row['rank'])}: Lat {lat:.5f}, Lng {lng:.5f} | "
            f"+{int(row['score']):,} people ({row['score_type']})"
        )

    print("\n[7/7] Baseline vs optimized accessibility...")
    baseline = compute_baseline(hospitals, graph, pop_gdf, threshold=threshold)
    print_stats("Baseline Accessibility (existing facilities only)", baseline, threshold=threshold)
    optimized = compute_optimized(top, hospitals, graph, pop_gdf, threshold=threshold)
    print_stats("Optimized Accessibility (existing + best recommended site)", optimized, threshold=threshold)
    comparison = compare_baseline_optimized(baseline, optimized)

    return {"baseline": baseline, "optimized": optimized, "comparison": comparison, "top_sites": top}
