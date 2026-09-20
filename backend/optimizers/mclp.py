"""Joint multi-facility selection (Maximal Covering Location Problem).

v0.1/v0.2 rank candidates independently: the Rank-2 site is the best *single*
site ignoring Rank-1, so the top-N list double-counts population. This module
selects a *set* of k sites jointly.

Formulation. With A[j, i] = 1 iff candidate j's drive-time catchment covers
population node i, and w_i the population *newly* coverable at node i
(already-covered facilities contribute w_i = 0, matching the marginal
coverage semantics of `objectives.coverage`):

    maximize   sum_i w_i * y_i
    subject to y_i <= sum_j A[j,i] * x_j      (coverage implication)
               sum_j x_j <= k                 (site budget)
               x, y binary

Two solvers on one shared coverage matrix:

* ``greedy_mclp`` — lazy greedy (cost-benefit heap, Minoux 1978). Selecting
  the best marginal site k times on a monotone submodular objective gives
  f(GREEDY) >= (1 - (1 - 1/k)^k) * OPT >= (1 - 1/e) * OPT ~= 63.2%.
* ``exact_mclp`` — the MILP above via PuLP + CBC, the benchmark for the
  greedy gap. ~300-candidate / ~1,000-node instances solve in seconds.

``build_coverage_matrix`` reuses the exact scoring machinery of
``objectives.coverage`` (same travel times, same reverse-graph reachability,
same node snapping), so the matrix is consistent with the marginal scores:
for k = 1 greedy reproduces the Rank-1 result exactly.
"""
import heapq
import time
import warnings

import networkx as nx
import numpy as np
import osmnx as ox

from backend.objectives.coverage import (
    _reachable_from,
    add_travel_times,
    nearest_nodes_unique,
    population_by_node,
)


class CoverageInstance:
    """Everything both solvers need, plus the frames to map results back.

    ``A`` is aligned row-for-row with ``candidates`` (the filtered candidate
    frame, in order) and column-for-column with ``pop_node_ids`` (sorted
    graph node ids, deterministic).
    """

    def __init__(self, A, w_new, w_total, candidates, pop_node_ids, threshold):
        self.A = A                  # (C, P) bool: candidate j covers pop node i
        self.w_new = w_new          # (P,) float: newly coverable population
        self.w_total = w_total      # (P,) float: total population (reporting)
        self.candidates = candidates
        self.pop_node_ids = pop_node_ids
        self.threshold = threshold

    @property
    def n_candidates(self):
        return self.A.shape[0]

    @property
    def n_pop_nodes(self):
        return self.A.shape[1]


def build_coverage_matrix(candidates, pop_gdf, graph, hospitals=None,
                          threshold=None):
    """Build the candidate x population-node coverage matrix.

    Uses the same reverse-graph reachability and node snapping as the legacy
    per-candidate scorer, so greedy with k = 1 reproduces the Rank-1
    marginal result (regression-tested both directions in tests/test_mclp.py).
    """
    graph = add_travel_times(graph)
    graph_rev = graph.reverse(copy=True)

    node_pop = population_by_node(pop_gdf, graph_rev)
    pop_node_ids = np.array(sorted(node_pop))
    w_total = np.array([node_pop[n] for n in pop_node_ids], dtype=float)

    # Population already within threshold of an existing facility cannot be
    # *newly* covered: its weight is zeroed for the objective (kept in
    # w_total for reporting).
    w_new = w_total.copy()
    if hospitals is not None and len(hospitals):
        hospital_nodes = nearest_nodes_unique(hospitals.geometry, graph_rev)
        already = nx_multi_source(graph_rev, hospital_nodes, threshold)
        mask = np.isin(pop_node_ids, list(already))
        w_new[mask] = 0.0

    candidate_nodes = ox.distance.nearest_nodes(
        graph_rev, candidates.geometry.x.values, candidates.geometry.y.values,
    )
    order = {n: i for i, n in enumerate(pop_node_ids)}
    A = np.zeros((len(candidate_nodes), len(pop_node_ids)), dtype=bool)
    for j, node in enumerate(candidate_nodes):
        reachable = _reachable_from(graph_rev, node, threshold)
        cols = [order[n] for n in reachable if n in order]
        A[j, cols] = True

    return CoverageInstance(A, w_new, w_total, candidates,
                            pop_node_ids, threshold)


def cell_groups_for_nodes(pop_gdf, graph, pop_node_ids):
    """Per-cell coverage-column groups for the cell-level bootstrap.

    Returns one column-index array per population cell: the column(s) its
    centroid snapped to in the matrix built by ``build_coverage_matrix``.
    The inverse of the cell -> node weight aggregation, letting the
    uncertainty module resample whole WorldPop cells as units.
    """
    nodes = ox.distance.nearest_nodes(
        graph, pop_gdf.geometry.x.values, pop_gdf.geometry.y.values)
    order = {n: i for i, n in enumerate(pop_node_ids)}
    return [np.array([order[n]]) for n in nodes if n in order]


def nx_multi_source(graph_rev, sources, threshold):
    """Union of nodes reachable from any source within threshold seconds."""
    return set(nx.multi_source_dijkstra_path_length(
        graph_rev, sources, cutoff=threshold, weight="travel_time"))


def greedy_mclp(inst: CoverageInstance, k):
    """Lazy greedy maximization of new coverage under a budget of k sites.

    Returns a result dict with ``selected`` (candidate row indices),
    ``gains`` (per-site new population, in selection order) and
    ``objective`` (sum of gains). Stops early if no remaining site adds
    anyone (the budget cannot be usefully spent).
    """
    t0 = time.perf_counter()
    C, P = inst.A.shape
    covered = np.zeros(P, dtype=bool)
    remaining = set(range(C))

    # Initial marginal gains: w_new over each candidate's full catchment.
    marg = inst.A.astype(float) @ inst.w_new
    heap = [(-g, j) for j, g in enumerate(marg)]
    heapq.heapify(heap)

    selected, gains = [], []
    while len(selected) < k and heap and remaining:
        neg, j = heapq.heappop(heap)
        if j not in remaining:
            continue
        fresh = inst.A[j] & ~covered
        gain = inst.w_new[fresh].sum()
        # Lazy evaluation: if another candidate's (stale) estimate beats this
        # fresh gain, push back and let it surface first.
        if heap and -heap[0][0] > gain + 1e-9:
            heapq.heappush(heap, (-gain, j))
            continue
        if gain <= 0.0:
            break  # every remaining marginal gain is zero; budget unspendable
        selected.append(j)
        gains.append(float(gain))
        covered |= fresh
        remaining.discard(j)

    return {
        "solver": "greedy",
        "selected": selected,
        "gains": gains,
        "objective": float(sum(gains)),
        "covered_mask": covered,
        "seconds": time.perf_counter() - t0,
    }


def exact_mclp(inst: CoverageInstance, k, time_limit_s=60):
    """Exact MILP benchmark via PuLP + CBC. Same result dict as greedy.

    Raises RuntimeError with install guidance if PuLP is missing.
    """
    try:
        import pulp
    except ImportError as e:
        raise RuntimeError(
            "exact MCLP needs PuLP (pip install pulp); "
            "use mode=greedy for the guaranteed approximation"
        ) from e

    t0 = time.perf_counter()
    C, P = inst.A.shape
    # pulp 3.3 deprecation noise (LpVariable constructor, bundled-CBC access);
    # revisit with prob.add_variable/COIN_CMD when pinning pulp >= 4.0.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        prob = pulp.LpProblem("mclp", pulp.LpMaximize)
        x = [pulp.LpVariable(f"x{j}", cat="Binary") for j in range(C)]
        y = [pulp.LpVariable(f"y{i}", cat="Binary") for i in range(P)]
        prob += pulp.lpSum(inst.w_new[i] * y[i] for i in range(P))
        prob += pulp.lpSum(x) <= k
        for i in range(P):
            covering = np.nonzero(inst.A[:, i])[0]
            # Pop nodes no candidate can reach are forced to y_i = 0
            # (constraint against an empty sum); they remain unreachable,
            # not lost population.
            prob += y[i] <= pulp.lpSum(int(inst.A[j, i]) * x[j]
                                       for j in covering)

        solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit_s)
        status = prob.solve(solver)
    covered = np.zeros(P, dtype=bool)
    for i in range(P):
        if y[i].value() is not None and y[i].value() > 0.5:
            covered[i] = True
    selected = sorted(j for j in range(C)
                      if x[j].value() is not None and x[j].value() > 0.5)

    return {
        "solver": "exact",
        "selected": selected,
        "gains": None,  # per-site attribution is meaningless for a joint optimum
        "objective": float(inst.w_new[covered].sum()),
        "covered_mask": covered,
        "status": pulp.LpStatus[status],
        "seconds": time.perf_counter() - t0,
    }


def select_sites(inst: CoverageInstance, k, mode="greedy"):
    """Run the requested solver(s); returns per-solver result dicts.

    mode="both" also returns ``gap_pct``: how far greedy fell short of the
    exact optimum, relative to it (0.0 when greedy is optimal).
    """
    if k <= 0:
        raise ValueError(f"k must be >= 1, got {k}")
    if inst.n_candidates == 0:
        raise ValueError("No candidates to select from (all filtered out?).")

    results = {}
    greedy = greedy_mclp(inst, k)
    results["greedy"] = greedy
    if mode in ("exact", "both"):
        exact = exact_mclp(inst, k)
        results["exact"] = exact
        if mode == "both":
            opt = exact["objective"]
            results["gap_pct"] = (
                0.0 if opt <= 0 else (opt - greedy["objective"]) / opt * 100.0
            )
    return results
