"""Restricted facility class Mysuru 5m pipeline run

Mysuru's full 85 facility curated network is already saturated at 5 minutes. 
This script runs Mysuru for baseline 5 min coverage, best single site (Rank-1) and after best single site, jointly selected set (greedy vs exact MILP, gap), 
median/max drive times before and after.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import geopandas as gpd

from backend.candidates.generate import generate_candidates
from backend.config import city_config
from backend.constraints.filters import apply_all_filters
from backend.data.population import load_population_grid
from backend.gis.hospitals import _curate
from backend.objectives.baseline import (compute_baseline, compute_optimized,
                                         compute_optimized_set, print_stats)
from backend.objectives.coverage import add_travel_times, score_from_matrix
from backend.optimizers.mclp import build_coverage_matrix, select_sites
from backend.optimizers.ranker import get_top_candidates
from backend.routing.graph import load_graph

CFG = city_config("mysuru", "Mysuru, Karnataka, India")
THRESHOLD = 300


def load_restricted_facilities():
    """The v0.1/v0.2 restricted class"""
    hospitals = gpd.read_file(CFG.hospitals_path).to_crs(CFG.crs)
    hospitals = hospitals[hospitals.geometry.notnull()]
    hospitals = _curate(hospitals)
    is_poly = hospitals.geometry.apply(
        lambda g: g.geom_type in ("Polygon", "MultiPolygon"))
    restricted = hospitals[is_poly].copy()
    restricted["geometry"] = restricted.geometry.centroid
    return restricted


def main():
    hospitals = load_restricted_facilities()
    print(f"Permitted facility class: {len(hospitals)} polygon-mapped hospitals")

    graph = add_travel_times(load_graph(CFG))
    pop_gdf = load_population_grid(CFG)
    candidates = generate_candidates(CFG)
    candidates = apply_all_filters(candidates, hospitals, graph, CFG)
    candidates = candidates.reset_index(drop=True)

    instance = build_coverage_matrix(candidates, pop_gdf, graph,
                                     hospitals=hospitals, threshold=THRESHOLD)

    baseline = compute_baseline(hospitals.geometry, graph, pop_gdf,
                                threshold=THRESHOLD)
    print_stats("Baseline Accessibility (existing + 19 polygon-mapped facilities)",
                baseline, threshold=THRESHOLD)

    scored = score_from_matrix(instance)
    top = get_top_candidates(scored, top_n=5)
    print("\nIndependent ranking (best single sites):")
    for _, row in top.iterrows():
        print(f"  Rank {int(row['rank'])}: +{int(row['score']):,} people")

    mclp = select_sites(instance, 5, mode="both")
    print("\nJoint MCLP (restricted class, k=5):")
    for name, res in mclp.items():
        if not isinstance(res, dict):
            continue
        print(f"  {res['solver']}: {len(res['selected'])} site(s), "
              f"new coverage {res['objective']:,.0f} people",
              end="")
        if "gap_pct" in mclp:
            print(f"  (gap {mclp['gap_pct']:.2f}%)", end="")
        print()
        for order, j in enumerate(res["selected"], start=1):
            print(f"    site {order}: row {j}")

    selection = {"candidates": scored,
                 "selected": mclp["greedy"]["selected"],
                 "mode": "greedy", "k": 5}
    if "exact" in mclp:
        selection["exact"] = mclp["exact"]

    optimized_set = compute_optimized_set(selection, hospitals, graph, pop_gdf,
                                          threshold=THRESHOLD)
    print_stats(
        f"Optimized Accessibility (existing + {len(selection['selected'])} jointly "
        f"selected sites)",
        optimized_set, threshold=THRESHOLD)

    optimized_one = compute_optimized(top, hospitals, graph, pop_gdf,
                                      threshold=THRESHOLD)
    print("\nResults (5-minute threshold, restricted facility class):")
    print(f"  Baseline:        {baseline['coverage_pct']:.1f}%  "
          f"({baseline['covered_population']:,.0f} / "
          f"{baseline['total_population']:,.0f})")
    print(f"  Best single:     {optimized_one['coverage_pct']:.1f}%  "
          f"(+{int(top['score'].iloc[0]):,})")
    print(f"  Joint greedy k=5: {optimized_set['coverage_pct']:.1f}%  "
          f"(+{int(mclp['greedy']['objective']):,})")
    print(f"  Max drive time:  {baseline['max_travel_time_min']:.1f} -> "
          f"{optimized_set['max_travel_time_min']:.1f} min")
    print(f"  Median drive time: {baseline['median_travel_time_min']:.1f} -> "
          f"{optimized_set['median_travel_time_min']:.1f} min")
    print(f"  Still uncovered: "
          f"{optimized_set['total_population'] - optimized_set['covered_population']:,.0f}")


if __name__ == "__main__":
    main()
