"""Uncertainty reporting for headline figures.

Every headline figure the pipeline reports is a deterministic function of
(population weights, candidate x population-node coverage matrix A):

    total_population   = w_total.sum()
    baseline_covered   = w_total[w_new == 0].sum()
    best_single_gain   = max(A @ w_new)                    (Rank-1 marginal)
    mclp_objective(k)  = lazy-greedy objective for budget k

The travel-time model itself is not resampled (a road-network perturbation
study is a different, graph-resampling exercise), but the dominant
uncertainty: where people are and how many live on each 1 km cell can be
quantified exactly, because resampling only touches the weights while the
expensive Dijkstra work is frozen in ``A``:

* **Delete-1 jackknife** (deterministic, one replicate per populated
  column): zero one coverage column at a time and recompute the figure.
  Measures the influence of single cell-clusters; interval from standard
  pseudo-values (pseudo_i = n*theta - (n-1)*theta_(i)).
* **Bootstrap, seeded**: resample weight units with replacement and
  recompute. Two granularities:
  - ``groups=None`` -> resample snapped coverage *columns* (node-clustered
    population): sensitivity to the clustering pattern.
  - ``groups=g``    -> resample *cells* (each unit = the columns one
    WorldPop 1 km cell snapped to, counts kept as a unit): respects the
    spatially structured, correlated error of the density product.
  Several cells may snap to the same node; duplicate draws accumulate
  weights, which is exactly the cell-level bootstrap.

**Interpretation caveat, stated plainly:** WorldPop is a modeled surface,
not a sample from a superpopulation, so these intervals are *sensitivity*
measures — how far one cell (or one resampled draw of cells) can move a
figure — not confidence intervals about truth.

City agnostic by construction: matrix only, no paths, no names, no network.
"""
from dataclasses import dataclass, field

import numpy as np

from backend.optimizers.mclp import CoverageInstance, greedy_mclp



# figures: pure functions of (A, w_new, w_total, k)


def figure_total_population(w_total):
    return float(w_total.sum())


def figure_baseline_covered(w_new, w_total):
    """Population already within threshold of existing facilities.

    ``build_coverage_matrix`` zeroes w_new exactly where existing facilities
    already cover a node, so the mask ``w_new == 0`` recovers the baseline
    covered set without any graph work.
    """
    return float(w_total[w_new == 0.0].sum())


def figure_best_single_gain(A, w_new):
    reachable = np.asarray(A) @ w_new
    return float(reachable.max()) if reachable.size else 0.0


def figure_mclp_objective(A, w_new, w_total, k):
    inst = CoverageInstance(np.asarray(A, dtype=bool), w_new, w_total,
                            candidates=None, pop_node_ids=None, threshold=None)
    return float(greedy_mclp(inst, k)["objective"])


FIGURES = {
    "total_population": lambda A, wn, wt, k: figure_total_population(wt),
    "baseline_covered": lambda A, wn, wt, k: figure_baseline_covered(wn, wt),
    "best_single_gain": lambda A, wn, wt, k: figure_best_single_gain(A, wn),
    "mclp_objective": lambda A, wn, wt, k: figure_mclp_objective(A, wn, wt, k),
}



# shared replicate weight sets (computed once, reused by every figure)


def _group_columns(groups, n_cols):
    """Default units: one column = one unit."""
    if groups is None:
        return [np.array([i]) for i in range(n_cols)]
    return [np.asarray(g, dtype=int) for g in groups]


def _bootstrap_sets(w_new, w_total, units, n_boot, rng):
    """``n_boot`` resampled (w_new, w_total) pairs over the given units."""
    sets = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(units), size=len(units))
        wn, wt = np.zeros_like(w_new), np.zeros_like(w_total)
        for u in pick:                      # a unit may be drawn 0..n times
            cols = units[u]
            wn[cols] += w_new[cols]
            wt[cols] += w_total[cols]
        sets.append((wn, wt))
    return sets


def _replicate_sets(A, w_new, w_total, groups, n_boot, seed):
    """All replicate weight sets, shared across figures.

    Returns (jackknife_sets, column_bootstrap_sets, cell_bootstrap_sets|None).
    Jackknife is delete-1 over populated columns (deterministic); the two
    bootstraps share one seeded RNG stream, so the column bootstrap and the
    cell bootstrap use different draws for the same replicate index.
    """
    live = np.nonzero(w_total > 0)[0]
    jackknife = []
    if live.size >= 2:                  # spread needs >= 2 delete-1 units
        for i in live:
            wn, wt = w_new.copy(), w_total.copy()
            wn[i] = wt[i] = 0.0
            jackknife.append((wn, wt))

    rng = np.random.default_rng(seed)
    col_units = _group_columns(None, A.shape[1])
    col_boot = _bootstrap_sets(w_new, w_total, col_units, n_boot, rng)
    cell_boot = None
    if groups is not None:
        cell_boot = _bootstrap_sets(w_new, w_total,
                                    _group_columns(groups, A.shape[1]),
                                    n_boot, rng)
    return jackknife, col_boot, cell_boot



# intervals


def _jackknife_interval(point, reps):
    """Pseudo-value SE and the delete-1 min/max influence range.

    ``reps`` is a list of (weights_set, figure_value); only values are used.
    """
    n = len(reps)
    if n < 2:
        return {"se": 0.0, "lo": point, "hi": point, "n": n}
    vals = np.asarray([v for _, v in reps], dtype=float)
    pseudo = n * point - (n - 1) * vals
    se = float(pseudo.std(ddof=1) / np.sqrt(n))
    return {"se": se, "lo": float(vals.min()), "hi": float(vals.max()), "n": n}


def _percentile_interval(reps):
    vals = np.asarray(reps, dtype=float)
    return {"mean": float(vals.mean()),
            "lo": float(np.percentile(vals, 2.5)),
            "hi": float(np.percentile(vals, 97.5)),
            "n": int(vals.size)}



# public API


@dataclass
class FigureReport:
    name: str
    point: float
    jackknife: dict = field(default_factory=dict)
    bootstrap: dict = field(default_factory=dict)
    cell_bootstrap: dict = field(default_factory=dict)


def headline_figures(A, w_new, w_total, k, groups=None, n_boot=200, seed=0):
    """Point estimates + jackknife + bootstrap intervals for the four figures.

    ``groups`` optionally maps WorldPop cells to coverage columns (a list of
    column-index arrays, one per cell); when given, a cell-level bootstrap
    (resampling cells as units) runs in addition to the column-level one.
    Deterministic for a fixed ``seed``.
    """
    A = np.asarray(A)
    w_new = np.asarray(w_new, dtype=float)
    w_total = np.asarray(w_total, dtype=float)
    if n_boot < 1:
        raise ValueError(f"n_boot must be >= 1, got {n_boot}")

    jack_sets, col_sets, cell_sets = _replicate_sets(
        A, w_new, w_total, groups, n_boot, seed)

    reports = []
    for name, fig in FIGURES.items():
        point = fig(A, w_new, w_total, k)
        rep = FigureReport(name=name, point=point)
        rep.jackknife = _jackknife_interval(
            point, [(wn, fig(A, wn, wt, k)) for wn, wt in jack_sets])
        rep.bootstrap = _percentile_interval(
            [fig(A, wn, wt, k) for wn, wt in col_sets])
        if cell_sets is not None:
            rep.cell_bootstrap = _percentile_interval(
                [fig(A, wn, wt, k) for wn, wt in cell_sets])
        reports.append(rep)
    return reports


def print_reports(reports, k=None):
    """Readable uncertainty table for the pipeline report."""
    print("\n--- Uncertainty (delete-1 jackknife + seeded bootstrap) ---")
    print("    Intervals are sensitivity measures (modeled surface), not CIs about truth.")
    for r in reports:
        print(f"\n  {r.name}: {r.point:,.0f}")
        j = r.jackknife
        print(f"    jackknife : SE {j['se']:,.1f} | delete-1 range "
              f"[{j['lo']:,.0f}, {j['hi']:,.0f}] over {j['n']} clusters")
        b = r.bootstrap
        print(f"    bootstrap : mean {b['mean']:,.0f} | 95% range "
              f"[{b['lo']:,.0f}, {b['hi']:,.0f}] over {b['n']} draws (columns)")
        if r.cell_bootstrap:
            c = r.cell_bootstrap
            print(f"    cells     : mean {c['mean']:,.0f} | 95% range "
                  f"[{c['lo']:,.0f}, {c['hi']:,.0f}] over {c['n']} draws (1 km cells)")
    if k is not None:
        print(f"\n    (mclp_objective uses the lazy-greedy solver, budget k = {k})")
