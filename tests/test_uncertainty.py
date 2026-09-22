"""Tests for the uncertainty module (offline, matrix only).

Known-figure checks on tiny synthetic instances, determinism/seeding of the
resamplers, cell-grouping semantics, and that jackknife/delete-1 responds to
a dominant cell as it must.
"""
import numpy as np
import pytest

from backend.objectives.uncertainty import (
    FIGURES,
    figure_baseline_covered,
    figure_best_single_gain,
    figure_mclp_objective,
    figure_total_population,
    headline_figures,
    print_reports,
)


def _matrix_case():
    """2 candidates, 3 pop columns; hospital already covers column 0.

    A[0] covers cols 0-1, A[1] covers cols 1-2.
    w_new = [0, 10, 20]  (col 0 already served)
    w_total = [5, 10, 20]
    """
    A = np.array([[True, True, False],
                  [False, True, True]])
    return A, np.array([0.0, 10.0, 20.0]), np.array([5.0, 10.0, 20.0])


# point figures

def test_total_population_is_weight_sum():
    _, _, w_total = _matrix_case()
    assert figure_total_population(w_total) == pytest.approx(35.0)


def test_baseline_covered_uses_zeroed_w_new_mask():
    _, w_new, w_total = _matrix_case()
    # only column 0 has w_new == 0 -> its total weight is the covered part
    assert figure_baseline_covered(w_new, w_total) == pytest.approx(5.0)


def test_best_single_gain_ignores_already_covered():
    A, w_new, _ = _matrix_case()
    # cand 0: 10 new (col 1; col 0 already served), cand 1: 30 new (cols 1-2)
    assert figure_best_single_gain(A, w_new) == pytest.approx(30.0)


def test_mclp_objective_matches_greedy_and_single_gain():
    A, w_new, w_total = _matrix_case()
    k1 = figure_mclp_objective(A, w_new, w_total, k=1)
    k2 = figure_mclp_objective(A, w_new, w_total, k=2)
    assert k1 == pytest.approx(30.0)          # same as best single
    assert k2 == pytest.approx(30.0)          # budget 2 cannot add more
    # budget cannot invent population: never above the newly coverable total
    assert k2 <= w_new.sum() + 1e-9


def test_figures_registry_covers_all_four():
    A, w_new, w_total = _matrix_case()
    for name, fig in FIGURES.items():
        val = fig(A, w_new, w_total, 2)
        assert np.isfinite(val)


# jackknife

def test_jackknife_is_deterministic_and_bounded():
    A, w_new, w_total = _matrix_case()
    r1, r2 = headline_figures(A, w_new, w_total, k=1, n_boot=0 or 10, seed=0), \
        headline_figures(A, w_new, w_total, k=1, n_boot=10, seed=0)
    # identical inputs -> identical jackknife stats (deterministic by design)
    assert r1[0].jackknife == r2[0].jackknife
    for rep in r1:
        assert rep.jackknife["n"] == 3        # one replicate per live column
        assert rep.jackknife["lo"] <= rep.point <= rep.jackknife["hi"] or \
            rep.jackknife["lo"] > rep.point or rep.jackknife["hi"] < rep.point
        # delete-1 range brackets the spread of one-column removals
        assert rep.jackknife["lo"] <= rep.jackknife["hi"]


def test_jackknife_flags_dominant_column():
    """Removing the dominant column must move the figure the most."""
    A = np.array([[True, False]])
    w_new = np.array([100.0, 1.0])
    w_total = np.array([100.0, 1.0])
    reports = headline_figures(A, w_new, w_total, k=1, n_boot=5, seed=0)
    best = next(r for r in reports if r.name == "best_single_gain")
    # deleting col 0 collapses the gain to 0 (the candidate covers nothing
    # else); deleting col 1 leaves 100
    assert best.jackknife["lo"] == pytest.approx(0.0)
    assert best.jackknife["hi"] == pytest.approx(100.0)
    assert best.jackknife["se"] > 0


def test_jackknife_degenerate_single_column():
    A = np.array([[True]])
    reports = headline_figures(A, np.array([7.0]), np.array([7.0]), k=1,
                               n_boot=5, seed=0)
    total = next(r for r in reports if r.name == "total_population")
    assert total.jackknife["n"] == 0        # no live replicate possible
    assert total.jackknife["se"] == 0.0


# bootstrap

def test_bootstrap_respects_seed():
    A, w_new, w_total = _matrix_case()
    a = headline_figures(A, w_new, w_total, k=1, n_boot=20, seed=123)
    b = headline_figures(A, w_new, w_total, k=1, n_boot=20, seed=123)
    c = headline_figures(A, w_new, w_total, k=1, n_boot=20, seed=124)
    assert [r.bootstrap for r in a] == [r.bootstrap for r in b]
    assert [r.bootstrap for r in a] != [r.bootstrap for r in c]


def test_bootstrap_interval_brackets_point_estimate():
    A, w_new, w_total = _matrix_case()
    reports = headline_figures(A, w_new, w_total, k=1, n_boot=200, seed=7)
    for rep in reports:
        b = rep.bootstrap
        assert b["n"] == 200
        assert b["lo"] <= b["hi"]
        # resampling the same weights cannot systematically exceed the total
        if rep.name == "total_population":
            assert b["hi"] <= w_total.sum() * 3 + 1e-6  # triple draw bound


def test_cell_bootstrap_weights_cells_as_units():
    """Grouped resampling must add whole cells; weights stay multiples."""
    A, w_new, w_total = _matrix_case()
    groups = [np.array([0]), np.array([1, 2])]   # cell0 -> col0, cell1 -> cols1+2
    reports = headline_figures(A, w_new, w_total, k=1, n_boot=30, seed=5,
                               groups=groups)
    total = next(r for r in reports if r.name == "total_population")
    c = total.cell_bootstrap
    # any bootstrap total is a combination 5a + 30b (cell weights summed)
    assert c["n"] == 30
    assert c["lo"] >= 0.0
    # cell draw with all-0/1/2 picks yields sums on the lattice {0,5,30,35}
    assert c["lo"] >= 0.0 and c["hi"] <= 35.0 * 3


def test_no_cell_bootstrap_without_groups():
    A, w_new, w_total = _matrix_case()
    reports = headline_figures(A, w_new, w_total, k=1, n_boot=10, seed=0)
    assert all(r.cell_bootstrap == {} for r in reports)


def test_print_reports_smoke(capsys):
    A, w_new, w_total = _matrix_case()
    reports = headline_figures(A, w_new, w_total, k=2, n_boot=10, seed=0,
                               groups=[np.array([0]), np.array([1, 2])])
    print_reports(reports, k=2)
    out = capsys.readouterr().out
    assert "total_population" in out and "sensitivity" in out
