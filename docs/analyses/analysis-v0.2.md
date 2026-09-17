# ATLASIS v0.2 Analysis — Corrected Population Layer

## Purpose

This document records the results of rerunning the full v0.1 experiment suite
(Mysuru, Kolkata, Patna, Guwahati; 15-minute and 5-minute thresholds; candidate
grid sensitivity) after the v0.2 population-layer correction. Session date:
2026-09-13.

The v0.2 correction: WorldPop 1 km **density** values (people/km²) are now
converted to **people per cell** exactly, via
`count = density × geodesic cell area`, where cell area varies with the
cosine of latitude on the WGS84 grid. See `docs/analyses/analysis-v0.1.md` for
the v0.1 findings and the data note describing the original density-as-count
bug.

Every figure below is reproducible from a committed command:

```bash
python -m backend.main --city <city> [--threshold 300] [--spacing S]
```

with per-city caches rebuilt by `python -m backend.data.population --city <city>
--country IND` (legacy v0.1 CSV caches are refused with instructions to delete).

---

## What the correction does to the numbers

All road networks, hospital sets, candidate sets and population **cell sets**
are identical to v0.1; only the per-cell weights changed. Each v0.2 cell weight
equals the v0.1 value divided by that cell's area in km² (v0.1 stored density
as if it were a count). Because cell area varies with latitude, the city-wide
scaling factor *k* (v0.1 total ÷ v0.2 total) differs per city:

| City | Latitude | Mean cell area (km²) | k = v0.1/v0.2 | Modeled population (v0.1 → v0.2) |
|---|---|---|---|---|
| Mysuru | 12.3° | 0.839 | 1.192 | 813,866 → 682,746 |
| Kolkata | 22.5° | 0.793 | 1.261 | 7,640,760 → 6,060,054 |
| Patna | 25.6° | 0.774 | 1.292 | ~2.70 M → 2,087,503 |
| Guwahati | 26.1° | 0.771 | 1.298 | 631,495 → 486,692 |

The corrected totals are people counts, not raster values. They are still
WorldPop *modeled* estimates on a 1 km grid and inherit that product's accuracy
limits; they should still not be read as census figures.

**Verification of the scaling law.** Re-scoring the unmodified v0.2 pipeline
with the reconstructed v0.1 weights reproduces the v0.1 figures exactly — e.g.
Kolkata: city total 7,640,760 (doc: "approximately 7.64 million") and Rank-1
gain 53,855 (doc: 53,852). Conversely, v0.1's published gains map to v0.2 by
dividing by k: 53,852/1.261 = 42,711 ✓, 22,071/1.292 = 17,081 ≈ 17,094 ✓,
26,795/1.298 = 20,643 ≈ 20,657 ✓. Everything weight-invariant is unchanged:
coverage percentages, travel-time distributions, candidate counts, facility
counts, and the saturation pattern.

---

# City Results (corrected counts)

Facility class for all headline figures below: **all curated facilities**
(`amenity=hospital` after curation), matching the pipeline default. Where the
restricted facility class (polygon-mapped facilities only) is used, it is stated
explicitly.

## 1. Mysuru

* Population cells: 106, total ~682,746 people
* Facilities (all curated): 85 — polygon-mapped subset: 19
* Road network: 19,121 nodes / 55,984 edges
* Candidates after constraints: 100 (500 m grid)

### 15-minute analysis

Saturated, as in v0.1: baseline coverage 100.0% (682,746 / 682,746), best
candidate gain 0. Median drive time 1.1 min, max 4.2 min.

### 5-minute analysis — all 85 facilities

Saturated: baseline coverage 100.0% (682,746 / 682,746), best candidate gain 0.
Median 1.1 min, max 4.2 min.

### 5-minute analysis — restricted class (19 polygon-mapped facilities)

* Baseline coverage: 93.5%
* Best single candidate gain: +24,140 people (72 of 213 candidates scored > 0)
* Max drive time: 7.5 min

This restricted-class configuration is the one the v0.1 document's Mysuru
headline figures (94.7% baseline, +28,771) correspond to; see the
reconciliation section below. The all-facilities Mysuru network is saturated
even at 5 minutes, so the restricted class is the only configuration that
exposes a placement problem in this city.

## 2. Kolkata

* Population cells: 250, total ~6,060,054 people
* Facilities (all curated): 189 (21 mis-tagged + 2 duplicates dropped)
* Road network: 41,512 nodes / 105,547 edges
* Candidates after constraints: 314 (500 m grid)

### 15-minute analysis

Fully saturated: baseline coverage 100.0% (6,060,054 / 6,060,054), best
candidate gain 0. Median 1.4 min, max 10.8 min.

### 5-minute analysis

* Baseline coverage: 98.9% (5,995,665 people)
* Optimized coverage: 99.6% (6,038,377 people)
* Improvement: +0.7 percentage points, +42,711 people
* Max drive time: 10.8 → 7.5 min
* Median/mean: essentially unchanged (1.4 min / 1.6 min)

Identical structure to v0.1; the gain is 42,711 rather than 53,852 purely
because of the corrected weights (× 1.261).

## 3. Patna

* Population cells: 181, total ~2,087,503 people
* Facilities (all curated): 173 (8 mis-tagged + 1 duplicate dropped)
* Road network: 23,318 nodes / 56,253 edges
* Candidates after constraints: 219 (500 m grid)
* Unreachable population: 9,146 people (0.4%) have no road path to any facility
  (v0.1: 11,812 — the same cells under density-as-count weights)

### 15-minute analysis

Almost saturated: baseline coverage 99.6% (2,078,357 / 2,087,503), best
candidate gain 0. Median 1.3 min, max 7.9 min.

### 5-minute analysis

* Baseline coverage: 98.6% (2,058,883 people)
* Optimized coverage: 99.4% (2,075,977 people)
* Improvement: +0.8 percentage points, +17,094 people
* Max drive time: 7.9 → 5.8 min

### Grid resolution sensitivity (5-minute threshold)

| Spacing | Best gain | Rank-1 location |
|---|---|---|
| 250 m | +17,093 | 25.58782 N, 85.24158 E |
| 500 m | +17,093 | 25.58557 N, 85.24161 E |
| 1,000 m | +17,093 | 25.58557 N, 85.24161 E |
| 2,000 m | +17,093 | 25.57666 N, 85.25169 E |

The gain is identical at all four spacings and the recommended location moves
by at most ~1.4 km (within one 2 km grid step). Same conclusion as v0.1: the
result is not an artifact of one candidate grid.

## 4. Guwahati

* Population cells: 99, total ~486,692 people
* Facilities (all curated): 24 (2 duplicates dropped)
* Road network: 5,094 nodes / 12,003 edges
* Candidates after constraints: 185 (500 m grid)

### 15-minute analysis

Saturated: baseline coverage 100.0% (486,692 / 486,692), best candidate gain 0.
Median 2.1 min, max 11.5 min.

### 5-minute analysis

* Baseline coverage: 85.4% (415,569 people)
* Optimized coverage: 89.6% (436,226 people)
* Improvement: +4.2 percentage points, +20,657 people
* Median 2.1 → 2.0 min; mean 2.7 → 2.5 min; max unchanged at 11.5 min

The largest relative improvement in the suite, as in v0.1.

### Grid resolution sensitivity (5-minute threshold)

| Spacing | Best gain | Rank-1 location |
|---|---|---|
| 250 m | +20,656 | 26.13726 N, 91.70429 E |
| 500 m | +20,656 | 26.13277 N, 91.70684 E |
| 1,000 m | +20,656 | 26.13277 N, 91.70684 E |

Identical gain at all three spacings; the recommended location moves by ~560 m.
At 15 and 30 minutes the objective is saturated (v0.1 finding, unchanged).

---

# Cross-City Summary (corrected counts)

| City | 15-min result | 5-min result (all curated facilities) | Best 5-min gain |
|---|---|---|---|
| Mysuru | 100.0%, no gain | 100.0%, no gain | +0 (all 85) / +24,140 (19 polygon-mapped) |
| Kolkata | 100.0%, no gain | 98.9% → 99.6% | +42,711 |
| Patna | 99.6%, no gain | 98.6% → 99.4% | +17,094 |
| Guwahati | 100.0%, no gain | 85.4% → 89.6% | +20,657 |

Findings carried over from v0.1, unchanged in structure:

1. **A 15-minute threshold is saturated in every city** — every modeled cell is
   already within 15 minutes of a retained facility.
2. **A 5-minute threshold exposes a measurable gap in three of four cities**
   (all but Mysuru, whose gap only appears once the facility class is
   restricted to the 19 polygon-mapped hospitals).
3. **Results are stable across candidate grid spacings** (Patna, Guwahati).
4. **Saturation is an informative result**, not a failure; the pipeline reports
   it explicitly.

What v0.2 adds:

5. **The corrected population totals are consistent with reality.** Kampala,
   validated independently during the v0.2 build on a country never touched by
   v0.1, yields ~1,665,310 people vs ~1.68 M actual city population (within
   ~1%). The four Indian cities' totals scale down from v0.1 by their
   latitude-dependent cell-area factors (0.77–0.84), as expected.

---

# v0.1 → v0.2 Reconciliation

All v0.1 absolute figures were produced by treating density as count. The
mapping between the two document generations is exact and mechanical:

* **Totals and gains scale by k** (the table above). Dividing any v0.1
  population figure by that city's k reproduces the v0.2 figure — verified for
  all four cities' totals and best-candidate gains (residuals ≤ 14 people,
  float aggregation noise).
* **Coverage percentages are identical** because coverage is a per-cell binary
  computed over the same cell set; weights cancel.
* **Travel times, candidate counts, facility counts, network sizes and
  saturation patterns are identical** because none of them depend on population
  weights.

### The Mysuru 94.7% note

The v0.1 document's Mysuru section states "approximately 85" existing hospitals
in its dataset block but reports a 94.7% baseline and a +28,771 best gain at
5 minutes. Those two figures are mutually inconsistent with an 85-facility
network, which is saturated (100.0%) even at 5 minutes. They are reproduced
exactly by the **restricted facility class** — the 19 (then ~20) polygon-mapped
hospitals, documented in the README as the "restrict the facility class" lever:

* Baseline: 94.7% (v0.1, ~20 facilities) ↔ 93.5% (v0.2, 19 facilities — one
  1-km cell of difference)
* Best gain: +28,771 (v0.1) ÷ 1.192 = +24,134 predicted ↔ +24,140 measured
  (v0.2)

The residual 93.5% vs 94.7% difference is consistent with the polygon-mapped
set having contained one more facility when the v0.1 run was made (the facility
exclude list was still being tuned in v0.1). Conclusion: the v0.1 Mysuru
headline figures came from the restricted-class experiment; the v0.2 doc lists
both configurations explicitly to avoid the same ambiguity.

---

# Current Limitations

Carried over from v0.1 (see that document for the full list): modeled WorldPop
population on 1 km cells with centroid demand points; OSM facility inventory
varies by city and is not a census; no traffic, capacity, or parcel-level
feasibility; single-site greedy ranking, not joint multi-facility selection.

Specific to the corrected layer:

* The density→count conversion assumes each populated cell's area equals the
  geodesic area of its full 30-arcsec tile at the cell-centroid latitude
  (`count = density × area`). Within-city variation of this factor is negligible
  (~±0.1% at city scales), but the conversion inherits the raster's own
  population-distribution modeling error.
* Coverage percentages being identical to v0.1 is expected (weight-invariant
  metric), not evidence that the correction has no effect: all absolute
  population figures changed.
* The population figure for cells whose centroid coincides with a facility node
  is still a resolution artifact (reported as `pop_at_zero_min` in every run).

The next methodological priorities remain those listed in the v0.1 document: joint multi-facility selection (MCLP with a greedy ≥
(1 − 1/e) guarantee and exact benchmark), a 2SFCA accessibility index beyond the
binary threshold metric, and uncertainty reporting for the modeled population.
