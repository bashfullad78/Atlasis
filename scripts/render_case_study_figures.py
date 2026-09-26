"""Render the four figures of docs/case-study-v0.3.md from cached data.
Usage:
    venv/bin/python scripts/render_case_study_figures.py            # all
    venv/bin/python scripts/render_case_study_figures.py --figs 1 4
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  
import numpy as np  
import osmnx as ox  
from matplotlib.collections import LineCollection  
from matplotlib.colors import Normalize 
from matplotlib.lines import Line2D 

from backend.candidates.generate import generate_candidates  
from backend.config import city_config  
from backend.constraints.filters import apply_all_filters  
from backend.data.population import load_population_grid  
from backend.gis.hospitals import load_hospitals  
from backend.objectives.coverage import (  
    add_travel_times, score_from_matrix)
from backend.optimizers.mclp import build_coverage_matrix, select_sites  
from backend.optimizers.ranker import get_top_candidates 
from backend.routing.graph import load_graph  

THRESHOLD = 300  # 5-minute emergency window
OUTDIR = os.path.join(ROOT, "docs", "figures")

FREIBURG = ("freiburg", "Freiburg im Breisgau, Germany")
ZERO_COLOR = "#d7301f"      # red: beyond the 5m window
COVERED_CMAP = "YlGnBu"
NEW_EDGE_COLOR = "#1b7837"  # newly reachable road, Fig 4
DOC = {  # documented headline values (analysis-v0.3.md), crosschecking
    "baseline_pct": {"freiburg": 66.4, "krakow": 73.5,
                     "heidelberg": 77.9, "kampala": 98.7},
    "joint_objective": 56588,          # Freiburg greedy k=5
    "joint_gains_k": [18.0, 16.8, 10.6, 7.8, 3.3],  # thousands
    "freiburg_joint_pct": 92.7,
}


def _check(label, got, want, tol):
    ok = abs(got - want) <= tol
    tag = "ok" if ok else "MISMATCH"
    print(f"  [{tag}] {label}: computed {got:,.1f} vs documented {want:,.1f}")


def _read_baseline(slug):
    import geopandas as gpd
    cfg = city_config(slug)
    path = os.path.join(cfg.processed_dir, "accessibility_baseline.gpkg")
    gdf = gpd.read_file(path)
    return cfg, gdf


def _pop_weighted_pct(gdf):
    """Population-weighted share of cells with any 5-min access."""
    covered = gdf.loc[gdf["accessibility"] > 0, "population"].sum()
    return 100.0 * covered / gdf["population"].sum()


def _median_spacing(gdf):
    pts = np.c_[gdf.geometry.x, gdf.geometry.y]
    d2 = ((pts[:, None, :] - pts[None, :, :]) ** 2).sum(-1)
    np.fill_diagonal(d2, np.inf)
    return float(np.sqrt(d2.min(axis=1)).mean())


def _square_size(ax, fig, gdf):
    """Matplotlib marker area (pt^2) so squares tile like the real grid.

    Scale comes from the DATA coordinate span (never ax.get_xlim(): before
    the first scatter it is still the default (0, 1), which once rendered
    every marker panel-sized). Call fig.canvas.draw() first so the
    constrained-layout positions are final; with aspect('equal') the
    binding axis is min(width/xspan, height/yspan).
    """
    spacing = _median_spacing(gdf)
    xspan = float(gdf.geometry.x.max() - gdf.geometry.x.min())
    yspan = float(gdf.geometry.y.max() - gdf.geometry.y.min())
    pos = ax.get_position()
    pw_pts = pos.width * fig.get_figwidth() * 72
    ph_pts = pos.height * fig.get_figheight() * 72
    pts_per_m = min(pw_pts / xspan, ph_pts / yspan)
    return (0.9 * spacing * pts_per_m) ** 2


# Figure 1
def fig1():
    import geopandas as gpd
    cities = [("freiburg", "Freiburg (DEU)"),
              ("krakow", "Kraków (POL)"),
              ("heidelberg", "Heidelberg (DEU)"),
              ("kampala", "Kampala (UGA)")]
    grids = {}
    vmax = 0.0
    print("\n== Figure 1: baseline accessibility, four cities ==")
    for slug, label in cities:
        _, gdf = _read_baseline(slug)
        grids[slug] = (label, gdf)
        vmax = max(vmax, gdf.loc[gdf["accessibility"] > 0,
                                 "accessibility"].max())
        _check(f"{slug} baseline % within 5 min", _pop_weighted_pct(gdf),
               DOC["baseline_pct"][slug], tol=0.2)

    fig, axes = plt.subplots(1, len(cities), figsize=(15.5, 4.9),
                             constrained_layout=True)
    norm = Normalize(vmin=0.0, vmax=vmax)
    for ax, (slug, _) in zip(axes, cities):
        label, gdf = grids[slug]
        ax.set_facecolor("#f7f7f7")
        ax.set_title(f"{label}\n"
                     f"{DOC['baseline_pct'][slug]:.1f}% within 5 min",
                     fontsize=11)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
    sm = plt.cm.ScalarMappable(cmap=COVERED_CMAP, norm=norm)
    cbar = fig.colorbar(sm, ax=axes, shrink=0.82, pad=0.015, aspect=32)
    cbar.set_label("baseline accessibility (shared scale across panels)")
    fig.suptitle("The access gap before anything is built: per-cell 5-minute "
                 "accessibility to existing hospitals", fontsize=13)
    fig.canvas.draw()  # finalize constrained layout before sizing markers
    for ax, (slug, _) in zip(axes, cities):
        _, gdf = grids[slug]
        x, y = gdf.geometry.x, gdf.geometry.y
        zeros = gdf["accessibility"] == 0
        s = _square_size(ax, fig, gdf)
        ax.scatter(x[~zeros], y[~zeros], c=gdf.loc[~zeros, "accessibility"],
                   cmap=COVERED_CMAP, norm=norm, marker="s", s=s,
                   linewidths=0, zorder=2)
        ax.scatter(x[zeros], y[zeros], c=ZERO_COLOR, marker="s",
                   s=s * 1.05, linewidths=0, zorder=3)
    path = os.path.join(OUTDIR, "fig1_baseline_surfaces.png")
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


# Freiburg MCLP reproduction
def _freiburg_instance():
    """Rebuild the exact pipeline state: candidates, matrix, greedy k=5."""
    slug, place = FREIBURG
    cfg = city_config(slug, place)
    hospitals = load_hospitals(cfg)
    graph = add_travel_times(load_graph(cfg))
    graph_rev = graph.reverse(copy=True)
    pop_gdf = load_population_grid(cfg)
    candidates = generate_candidates(cfg)
    candidates = apply_all_filters(candidates, hospitals, graph, cfg)
    candidates = candidates.reset_index(drop=True)
    inst = build_coverage_matrix(candidates, pop_gdf, graph,
                                 hospitals=hospitals, threshold=THRESHOLD)
    return cfg, hospitals, graph, graph_rev, pop_gdf, candidates, inst


def _cell_nodes(pop_gdf, graph):
    nodes = ox.distance.nearest_nodes(
        graph, pop_gdf.geometry.x.values, pop_gdf.geometry.y.values)
    return np.asarray(nodes)


# Figure 2
def fig2():
    print("\n== Figure 2: independent ranking vs joint selection (Freiburg) ==")
    _, hospitals, graph, graph_rev, pop_gdf, _, inst = _freiburg_instance()
    scored = score_from_matrix(inst)
    top5 = get_top_candidates(scored, top_n=5)
    mclp = select_sites(inst, 5, mode="greedy")
    greedy = mclp["greedy"]
    sel, gains = greedy["selected"], greedy["gains"]
    print(f"  greedy objective: {greedy['objective']:,.0f} people "
          f"({greedy['seconds']:.2f}s)")
    _check("greedy objective (k=5)", greedy["objective"],
           DOC["joint_objective"], tol=1)
    _check("Rank-1 gain == greedy gain 1", gains[0],
           float(top5["score"].iloc[0]), tol=1)

    def newly_covered(rows):
        """Boolean mask over pop nodes newly covered by candidate rows."""
        return inst.A[list(rows)].max(axis=0)

    indep_mask = newly_covered(top5.index)
    joint_mask = newly_covered(sel)
    indep_union_pop = float((inst.w_new * indep_mask).sum())
    indep_sum_scores = float(top5["score"].sum())
    print(f"  independent top-5: scores sum {indep_sum_scores:,.0f} "
          f"(double-counted) | union newly covers {indep_union_pop:,.0f}")

    cell_node = _cell_nodes(pop_gdf, graph)
    order = {n: i for i, n in enumerate(inst.pop_node_ids)}
    cols = np.array([order.get(n, -1) for n in cell_node])
    valid = cols >= 0
    cell_new_indep = np.zeros(len(pop_gdf), dtype=bool)
    cell_new_joint = np.zeros(len(pop_gdf), dtype=bool)
    cell_new_indep[valid] = indep_mask[cols[valid]]
    cell_new_joint[valid] = joint_mask[cols[valid]]

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 6.4),
                             constrained_layout=True)
    for ax, mask, sites, gains_i, title in (
        (axes[0], cell_new_indep, top5, None,
         "a) Top-5 by independent ranking\n"
         f"scores sum {indep_sum_scores / 1e3:.1f}k (overlap double-counted)"
         f" — union covers only +{indep_union_pop / 1e3:.1f}k"),
        (axes[1], cell_new_joint, scored.iloc[sel], gains,
         "b) Jointly selected set (greedy, k=5)\n"
         f"+{greedy['objective'] / 1e3:.1f}k people newly covered"),
    ):
        ax.set_facecolor("#f7f7f7")
        ax.scatter(pop_gdf.geometry.x[~mask], pop_gdf.geometry.y[~mask],
                   c="#d9d9d9", s=26, linewidths=0, zorder=2)
        ax.scatter(pop_gdf.geometry.x[mask], pop_gdf.geometry.y[mask],
                   c="#2166ac", s=34, linewidths=0, zorder=3)
        ax.scatter(hospitals.geometry.x, hospitals.geometry.y, marker="o",
                   c="#252525", s=42, zorder=5)
        xs, ys = sites.geometry.x, sites.geometry.y
        ax.scatter(xs, ys, marker="*", c="#e08214", edgecolors="#7f3b08",
                   linewidths=0.6, s=420, zorder=6)
        for i, (sx, sy) in enumerate(zip(xs, ys), start=1):
            label = f"{i}"
            if gains_i is not None:
                label += f"\n+{gains_i[i - 1] / 1e3:.1f}k"
            ax.annotate(label, (sx, sy), xytext=(5, 5),
                        textcoords="offset points", fontsize=8.5,
                        fontweight="bold", zorder=7,
                        bbox=dict(boxstyle="round,pad=0.15", fc="white",
                                  ec="#999999", alpha=0.85))
        ax.set_title(title, fontsize=11)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
    handles = [
        Line2D([], [], marker="s", ls="", color="#2166ac",
               label="population newly covered by the set"),
        Line2D([], [], marker="s", ls="", color="#d9d9d9",
               label="population already covered / not covered"),
        Line2D([], [], marker="o", ls="", color="#252525",
               label="existing hospitals (9)"),
        Line2D([], [], marker="*", ls="", color="#e08214",
               markeredgecolor="#7f3b08", label="recommended site"),
    ]
    axes[1].legend(handles=handles, loc="upper left", fontsize=8,
                   framealpha=0.9)
    fig.suptitle("Picking sites one at a time systematically underbuilds — "
                 "Freiburg, 5-min threshold", fontsize=13)
    path = os.path.join(OUTDIR, "fig2_joint_vs_independent.png")
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


#  Figure 3
def fig3():
    import geopandas as gpd
    print("\n== Figure 3: per-cell E2SFCA change, baseline -> optimized ==")
    slug, place = FREIBURG
    cfg = city_config(slug, place)
    base = gpd.read_file(os.path.join(cfg.processed_dir,
                                      "accessibility_baseline.gpkg"))
    opt = gpd.read_file(os.path.join(cfg.processed_dir,
                                     "accessibility_optimized.gpkg"))
    if not base.geometry.geom_equals(opt.geometry).all():
        raise SystemExit("baseline/optimized cell sets differ -- cannot join")
    delta = opt["accessibility"] - base["accessibility"]
    still_zero = opt["accessibility"] == 0
    gained = delta > 0
    print(f"  cells: {len(base)} | gained access {int(gained.sum())} | "
          f"still beyond 5 min {int(still_zero.sum())} | "
          f"unchanged {int((delta == 0).sum())}")

    fig, ax = plt.subplots(figsize=(8.2, 7.2), constrained_layout=True)
    ax.set_facecolor("#f7f7f7")
    norm = Normalize(vmin=0.0, vmax=float(delta.max()))
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    boundary = gpd.read_file(cfg.boundary_path).to_crs(cfg.crs)
    boundary.boundary.plot(ax=ax, color="#737373", linewidth=0.9, zorder=1)
    ax.set_title("Which neighborhoods the new sites rescued — Freiburg\n"
                 "change in per-cell E2SFCA access, 9 existing + 5 joint "
                 "sites (5 min)", fontsize=11)
    sm = plt.cm.ScalarMappable(cmap="Blues", norm=norm)
    cbar = fig.colorbar(sm, ax=ax, shrink=0.85)
    cbar.set_label("accessibility gained (optimized − baseline)")
    ax.annotate("hatched: still beyond the 5-min window",
                xy=(0.01, 0.01), xycoords="axes fraction", fontsize=8.5,
                color="#b2182b")
    fig.canvas.draw()  # finalize constrained layout before sizing markers
    s = _square_size(ax, fig, base)
    ax.scatter(base.geometry.x[~gained], base.geometry.y[~gained],
               c="#bdbdbd", marker="s", s=s, linewidths=0, zorder=2)
    ax.scatter(base.geometry.x[gained], base.geometry.y[gained],
               c=delta[gained], cmap="Blues", norm=norm, marker="s", s=s,
               linewidths=0, zorder=3)
    if still_zero.any():
        ax.scatter(base.geometry.x[still_zero], base.geometry.y[still_zero],
                   marker="s", s=s, facecolors="none", edgecolors="#b2182b",
                   linewidths=0.5, hatch="////", zorder=4)
    path = os.path.join(OUTDIR, "fig3_2sfca_change.png")
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


# Figure 4
def fig4():
    print("\n== Figure 4: 5-min road isochrones, baseline vs +5 sites ==")
    cfg, hospitals, graph, graph_rev, pop_gdf, _, inst = _freiburg_instance()
    scored = score_from_matrix(inst)
    greedy = select_sites(inst, 5, mode="greedy")["greedy"]
    sel = greedy["selected"]

    hospital_nodes = ox.distance.nearest_nodes(
        graph_rev, hospitals.geometry.x.values, hospitals.geometry.y.values)
    site_geoms = scored.iloc[sel]
    site_nodes = ox.distance.nearest_nodes(
        graph_rev, site_geoms.geometry.x.values, site_geoms.geometry.y.values)

    d_base = nx.multi_source_dijkstra_path_length(
        graph_rev, list(set(hospital_nodes)), cutoff=THRESHOLD,
        weight="travel_time")
    d_opt = nx.multi_source_dijkstra_path_length(
        graph_rev, list(set(hospital_nodes) | set(site_nodes)),
        cutoff=THRESHOLD, weight="travel_time")

    inf = float("inf")
    seg_base, seg_new, seg_ctx = [], [], []
    for u, v, data in graph.edges(data=True):
        seg = ((graph.nodes[u]["x"], graph.nodes[u]["y"]),
               (graph.nodes[v]["x"], graph.nodes[v]["y"]))
        tt = data.get("travel_time", inf)
        base_ok = min(d_base.get(u, inf), d_base.get(v, inf)) + tt <= THRESHOLD
        opt_ok = min(d_opt.get(u, inf), d_opt.get(v, inf)) + tt <= THRESHOLD
        if opt_ok and not base_ok:
            seg_new.append(seg)
        elif base_ok:
            seg_base.append(seg)
        else:
            seg_ctx.append(seg)

    cell_node = _cell_nodes(pop_gdf, graph_rev)
    cell_pop = pop_gdf["population"].astype(float).values
    pct_base = 100 * cell_pop[np.isin(cell_node, list(d_base))].sum() \
        / cell_pop.sum()
    pct_opt = 100 * cell_pop[np.isin(cell_node, list(d_opt))].sum() \
        / cell_pop.sum()
    _check("baseline coverage", pct_base, 66.4, tol=0.2)
    _check("coverage with joint k=5", pct_opt,
           DOC["freiburg_joint_pct"], tol=0.2)

    fig, ax = plt.subplots(figsize=(9.8, 8.6), constrained_layout=True)
    ax.set_facecolor("#f7f7f7")
    ax.add_collection(LineCollection(seg_ctx, colors="#e0e0e0",
                                     linewidths=0.5, zorder=1))
    ax.add_collection(LineCollection(seg_base, colors="#8f8f8f",
                                     linewidths=1.0, alpha=0.9, zorder=2))
    ax.add_collection(LineCollection(seg_new, colors=NEW_EDGE_COLOR,
                                     linewidths=1.4, zorder=3))
    ax.scatter(hospitals.geometry.x, hospitals.geometry.y, marker="s",
               c="#252525", s=42, zorder=5)
    ax.scatter(site_geoms.geometry.x, site_geoms.geometry.y, marker="*",
               c="#e08214", edgecolors="#7f3b08", linewidths=0.6, s=430,
               zorder=6)
    for i, (sx, sy) in enumerate(zip(site_geoms.geometry.x,
                                     site_geoms.geometry.y), start=1):
        ax.annotate(str(i), (sx, sy), xytext=(6, 6),
                    textcoords="offset points", fontsize=9,
                    fontweight="bold", zorder=7,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white",
                              ec="#999999", alpha=0.85))
    ax.autoscale()
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("Freiburg: who is within a 5-minute drive of a hospital\n"
                 f"baseline {pct_base:.1f}%  ->  with 5 jointly selected "
                 f"sites {pct_opt:.1f}%", fontsize=12)
    handles = [
        Line2D([], [], color="#8f8f8f", lw=2,
               label="reachable within 5 min (existing hospitals)"),
        Line2D([], [], color=NEW_EDGE_COLOR, lw=2.4,
               label="newly reachable with the recommended sites"),
        Line2D([], [], color="#e0e0e0", lw=1.2,
               label="road network (unreached)"),
        Line2D([], [], marker="s", ls="", color="#252525",
               label="existing hospitals (9)"),
        Line2D([], [], marker="*", ls="", color="#e08214",
               markeredgecolor="#7f3b08", label="jointly selected sites (5)"),
    ]
    ax.legend(handles=handles, loc="lower left", fontsize=8.5,
              framealpha=0.92)
    path = os.path.join(OUTDIR, "fig4_before_after_isochrones.png")
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


FIGS = {1: fig1, 2: fig2, 3: fig3, 4: fig4}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--figs", nargs="+", type=int,
                        choices=sorted(FIGS), default=sorted(FIGS),
                        help="which figures to render (default: all)")
    args = parser.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    written = []
    for n in args.figs:
        written.append(FIGS[n]())
    print()
    for path in written:
        print(f"Wrote {os.path.relpath(path, ROOT)} "
              f"({os.path.getsize(path) / 1e3:.0f} kB)")


if __name__ == "__main__":
    main()
