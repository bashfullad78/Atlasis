import os

import geopandas as gpd
from pyproj import Transformer

from backend.config import CityConfig
from backend.gis.boundary import load_boundary
from backend.gis.hospitals import load_hospitals
from backend.routing.graph import load_graph
from backend.candidates.generate import generate_candidates
from backend.constraints.filters import apply_all_filters
from backend.objectives.coverage import (add_travel_times, score_from_matrix,
                                         score_type_label)
from backend.objectives.baseline import (
    compute_baseline,
    compute_optimized,
    compute_optimized_set,
    compare_baseline_optimized,
    print_stats,
)
from backend.objectives.twosfca import (compare_2sfca, compute_2sfca,
                                        print_2sfca)
from backend.objectives.uncertainty import headline_figures, print_reports
from backend.data.population import load_population_grid
from backend.optimizers.mclp import (build_coverage_matrix, cell_groups_for_nodes,
                                     select_sites)
from backend.optimizers.ranker import get_top_candidates, save_results


def run_pipeline(cfg: CityConfig, spacing_m=None, top_n=None, threshold=None,
                 skip_forbidden=False, num_sites=None, mode="greedy",
                 uncertainty=False, n_boot=200, seed=0):
    spacing_m = spacing_m or cfg.spacing_m
    top_n = top_n or cfg.top_n
    threshold = threshold or cfg.threshold_sec
    num_sites = num_sites or 1

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
    candidates = apply_all_filters(candidates, hospitals, graph, cfg,
                                   skip_forbidden=skip_forbidden)

    print(f"[6/7] Scoring candidates (marginal {threshold / 60:.0f}-min drive coverage)...")
    pop_gdf = load_population_grid(cfg)
    # One coverage-matrix pass feeds BOTH the independent ranking and the
    # joint MCLP selection: the marginal scores are matrix-vector products,
    # so no Dijkstras are spent twice.
    inst = build_coverage_matrix(candidates, pop_gdf, graph,
                                 hospitals=hospitals, threshold=threshold)
    scored = score_from_matrix(inst)
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

    # Joint selection (MCLP) on the same matrix: greedy with the (1 - 1/e)
    # guarantee, optionally benchmarked against the exact MILP.
    print(f"\n[6b] Joint selection of {num_sites} site(s) ({mode})...")
    mclp = select_sites(inst, num_sites, mode=mode)
    for name, res in mclp.items():
        if not isinstance(res, dict):
            continue
        sites = res["selected"]
        print(f"  {res['solver']}: {len(sites)} site(s), "
              f"new coverage {res['objective']:,.0f} people "
              f"({res['seconds']:.2f}s)")
        for order, j in enumerate(sites, start=1):
            lng, lat = t.transform(scored.iloc[j].geometry.x,
                                   scored.iloc[j].geometry.y)
            gain = (f" +{mclp['greedy']['gains'][order - 1]:,.0f}"
                    if res["solver"] == "greedy" else "")
            print(f"    site {order}: Lat {lat:.5f}, Lng {lng:.5f}{gain}")
    if "gap_pct" in mclp:
        print(f"  greedy vs exact gap: {mclp['gap_pct']:.2f}%")

    selection = {"candidates": scored, "selected": mclp["greedy"]["selected"],
                 "mode": mode, "k": num_sites}
    if mode in ("exact", "both"):
        selection["exact"] = mclp["exact"]

    print("\n[7/7] Baseline vs optimized accessibility...")
    baseline = compute_baseline(hospitals, graph, pop_gdf, threshold=threshold)
    print_stats("Baseline Accessibility (existing facilities only)", baseline, threshold=threshold)
    optimized = compute_optimized(top, hospitals, graph, pop_gdf, threshold=threshold)
    print_stats("Optimized Accessibility (existing + best recommended site)", optimized, threshold=threshold)
    comparison = compare_baseline_optimized(baseline, optimized)

    if selection["selected"]:
        optimized_set = compute_optimized_set(
            selection, hospitals, graph, pop_gdf, threshold=threshold)
        print_stats(
            f"Optimized Accessibility (existing + {len(selection['selected'])} jointly selected sites)",
            optimized_set, threshold=threshold)
    else:
        optimized_set = None

    print("\n[7b] 2SFCA accessibility index (Gaussian decay over drive time)...")
    baseline_2sfca = compute_2sfca(hospitals, graph, pop_gdf, threshold=threshold)
    print_2sfca("2SFCA Baseline (existing facilities)", baseline_2sfca,
                threshold=threshold)
    added = [scored.iloc[j].geometry for j in selection["selected"]] \
        or [top.iloc[0].geometry]
    optimized_2sfca = compute_2sfca(
        gpd.GeoDataFrame(geometry=list(hospitals.geometry) + added,
                         crs=hospitals.crs),
        graph, pop_gdf, threshold=threshold)
    print_2sfca("2SFCA Optimized (existing + recommended)", optimized_2sfca,
                threshold=threshold)
    twosfca_comparison = compare_2sfca(baseline_2sfca, optimized_2sfca)

    os.makedirs(cfg.processed_dir, exist_ok=True)
    for label, scores in (("baseline", baseline_2sfca),
                          ("optimized", optimized_2sfca)):
        path = os.path.join(cfg.processed_dir, f"accessibility_{label}.gpkg")
        scores["cell_scores"].to_file(path, driver="GPKG")
        print(f"2SFCA cell scores saved to {path}")

    uncertainty_summary = None
    if uncertainty:
        print("\n[7c] Uncertainty (delete-1 jackknife + seeded bootstrap)...")
        # Cell-level bootstrap: map every WorldPop cell to the matrix
        # column(s) its centroid snapped to, so cells resample as units.
        groups = cell_groups_for_nodes(pop_gdf, graph, inst.pop_node_ids)
        reports = headline_figures(inst.A, inst.w_new, inst.w_total, k=num_sites,
                                   groups=groups, n_boot=n_boot, seed=seed)
        print_reports(reports, k=num_sites)
        uncertainty_summary = {
            r.name: {"point": r.point, "jackknife": r.jackknife,
                     "bootstrap": r.bootstrap,
                     "cell_bootstrap": r.cell_bootstrap}
            for r in reports
        }

    return {"baseline": baseline, "optimized": optimized,
            "optimized_set": optimized_set, "comparison": comparison,
            "top_sites": top, "selection": selection,
            "twosfca": {"baseline": baseline_2sfca,
                        "optimized": optimized_2sfca,
                        "comparison": twosfca_comparison},
            "uncertainty": uncertainty_summary}
