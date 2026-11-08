# ATLASIS v0.3 Analysis — Overpass Hardening, Heidelberg Onboarding

> First v0.3 layer: network reliability work plus the. Tests: 97/97 offline
> (`venv/bin/python -m pytest tests/ -q`).
> Implementation log: `docs/logs/v0.3-log.md`.

## Note: forbidden-zone tag sensitivity

While reviewing the forbidden filter, the tag set inherited unchanged from
v0.1 (`natural=water`, `waterway=*`, `landuse=reservoir/cemetery`,
`leisure=park`) was found to be broader in intent than in effect: the
`waterway` group returns almost exclusively line geometries, which the
point-in-polygon filter cannot exclude, so water filtering rests entirely
on the `natural=water` / `landuse=reservoir` polygons. To test whether the
published results depend on this definition, a tightened tag set
(additionally `amenity=grave_yard`, `natural=wetland/wood`,
`landuse=forest/quarry/landfill`) was fetched for Heidelberg and Kraków
via the combined-query path, and the full 5-min pipeline was run twice per
city (production tags vs tightened tags, merged in-memory; no caches
changed).

* **Candidate pool:** the tightened set removes 62 additional candidates
  in Heidelberg (220 → 158, −28%) and 146 in Kraków (988 → 842, −15%).
  At the 500 m grid these exclusions come almost entirely from
  forest/wood (61 of 62 in Heidelberg) and forest + wetland (Kraków);
  `amenity=grave_yard` excludes zero candidates in both cities.
* **Results:** all optimized outputs are identical under both filter
  definitions — top-5 sites and scores, best-site gains (+14,867
  Heidelberg, +40,751 Kraków), k = 5 joint selection (32,478 / 136,383
  people; 99.0% and 91.7% coverage; greedy–exact gap 0.00%), and the 2SFCA
  surfaces (+15.6% / +20.0%). The only difference is one tie-broken greedy
  site in Heidelberg moving to the adjacent grid point (equal gain) after
  the original fell in forest.

The published v0.3 numbers therefore do not depend on the breadth of the
forbidden tag set: the winning sites lie on unambiguously buildable land,
and results are robust to reasonable variation in the forbidden-zone
definition. Any future tightening (candidate for v0.4) changes the
candidate pool but not the published conclusions; `waterway=*` may be
trimmed to area values purely to cut fetch payload.

## What changed in the pipeline

The methodological chain (candidate generation, scoring, thresholds) is
untouched. All changes are in the data-fetch path:

1. **Mirror failover** (`backend/data/overpass.py`): every Overpass-backed
   call (roads, facilities, forbidden zones) probes each mirror's `/status`
   endpoint with a short timeout and runs on the first healthy one, failing
   over on any error. The last-good mirror is remembered and tried first.
   Client timeouts are decoupled from the server-side query budget: osmnx
   formats `requests_timeout` into the query's own `[timeout:...]` string,
   so a naive 45 s setting would have aborted legitimate big-city road
   queries server-side. Instead the HTTP client gets a `(10 s connect,
   170 s read)` tuple — a dead mirror now fails in ~10 s instead of hanging
   ~180 s — while the server keeps its 180 s budget. `ATLASIS_OVERPASS_MIRRORS`
   overrides the chain.
2. **Combined forbidden query** (`backend/constraints/forbidden.py`): the
   five sequential per-tag-group requests (each an independent 180 s hang
   risk; three v0.2 working windows died here) are now one request — osmnx
   unions tag groups within a single query. Per-layer try/except was
   removed: a partially fetched forbidden cache silently weakens the
   filter, so the fetch is all-or-nothing and fails over as a unit. The
   cache write is atomic (`.part` + rename), matching the other downloaders.
3. **`--skip-forbidden`** (`backend/main.py`, `prepare.py`, `pipeline.py`,
   `filters.py`): defers the forbidden fetch and disables the filter, with
   an explicit printed warning that results are not comparable to filtered
   runs. Intended for very unstable networks; the filter is re-enabled
   automatically once `forbidden.gpkg` exists.

## City results

### Heidelberg, Germany (new city; boundary/roads/hospitals were fetched in v0.2)

Bootstrap completed with the hardened fetch path: forbidden zones in **one
combined request** (682 zones), DEU density raster (3.0 MB) downloaded once
and shared, population cache built offline-forever after.

* Population cells: 194, total ~153,618 people (official city population
  ~160,000 — within ~4%)
* Road network / facility counts from the v0.2 fetch (see meta.json)
* Candidates after constraints: see `data/processed/heidelberg/top_sites.gpkg`

| Threshold | Baseline coverage | Optimized (best site) | Best gain |
|---|---|---|---|
| 15 min | 99.6% (153,010) | 100.0% (153,618) | +608 people (+0.4 pp) |
| 5 min | 77.9% (119,626) | 87.6% (134,493) | +14,867 people (+9.7 pp) |

At 15 minutes the only uncovered population is the 0.4% with no road path to
any facility; the recommended site makes them reachable, which is why
optimized reaches 100.0% while max drive time stays 8.8 min. At 5 minutes the
gap is substantial — the **largest relative improvement of any city in the
suite so far** (Guwahati: +4.2 pp) — with the best candidates clustered in the
south-west of the city (Bergheim/Wieblingen direction, lat ~49.383, lng
~8.65–8.66). Median drive time improves 3.1 → 2.5 min.

### Kampala, Uganda (forbidden layer completed with the cemetery tag group)

The stale forbidden cache (missing `landuse=cemetery` since the v0.2
Overpass timeouts) was deleted and refetched with the combined query:
**6,619 zones**. Full pipeline rerun at 5 minutes:

* Baseline 98.7% (1,643,049 / 1,665,310) → optimized 99.8% (1,662,445)
* Best gain **+19,395 people** — identical to the v0.2 headline
* Population total unchanged at ~1,665,310 (~1% of the official figure)

The completed forbidden layer does not move any top-5 candidate: the
recommended sites were never inside a cemetery. This is a useful robustness
check of the v0.2 results, not a no-op result — the filter now also excludes
cemetery land for future Kampala runs (denser grids, other thresholds).

## Reproduction commands

```bash
python -m backend.main --city heidelberg --place "Heidelberg, Germany"            # 15-min
python -m backend.main --city heidelberg --threshold 300                          # 5-min
python -m backend.main --city kampala --threshold 300                             # 5-min
python -m backend.main --city freiburg --place "Freiburg im Breisgau, Germany" --threshold 300
```

Warm-cache runs are fully offline. First-fetch runs use the mirror chain;
both Heidelberg fetches in this session succeeded first-try on the primary.

### Fresh city sanity check (Overpass overhead)

To validate the hardened fetch path end to end, the pipeline was run for a
city that had never been touched (Freiburg im Breisgau, DEU raster already
cached from Heidelberg, so the population step was offline):

```bash
time python -m backend.main --city freiburg \
  --place "Freiburg im Breisgau, Germany" --threshold 300
# real 1m32.1s  — boundary geocode + roads + hospitals + forbidden (one
# combined request) + population crop + full scoring + baseline report
```

**92 s wall** for a complete bootstrap-plus-run. The benchmark asks
for "one-command new city, < 10 min including downloads on a normal
connection" — v0.2 spent three ~10-minute working windows on Heidelberg's
forbidden step alone under Overpass load-shedding. The overhead reduction
comes from all three hardening pieces at once: fast-fail probing (~10 s to
detect a dead mirror instead of ~180 s), one combined forbidden request
instead of five, and failover never being needed because the primary was
healthy in the same minute the checks ran.

## Joint MCLP selection (first methodological v0.3 layer)

Candidates are no longer only ranked independently. `backend/optimizers/mclp.py`
builds a candidate x population-node coverage matrix (same reverse-graph
reachability and snapping as the marginal scorer) and selects a *set* of k
sites jointly:

* **greedy** — lazy cost-benefit greedy (Minoux), monotone submodular
  objective, so f(greedy) >= (1 - 1/e) x OPT ~= 63.2% by construction;
* **exact** — the MILP (maximize newly covered population subject to a k-site
  budget) solved by PuLP + CBC, as the benchmark;
* the pipeline derives the independent ranking *from the same matrix*
  (marginal score = coverage row dot weights), so both views cost one
  Dijkstra pass total, and k = 1 is regression-pinned to the legacy scorer.

CLI: `--num-sites k --mode greedy|exact|both`. Benchmarks:

| City | k (budget) | greedy | exact | gap | exact solve |
|---|---|---|---|---|---|
| Freiburg | 5 | 56,588 | 57,288 | 1.22% | 0.10 s |
| Heidelberg | 5 | 32,478 | 32,478 | 0.00% | 0.07 s |
| Kolkata | 10 (early stop) | 64,389 | 64,389 | 0.00% | 0.24 s |

* **< 1 min exact solve** target: 0.07-0.25 s on ~300-candidate instances
  (worst case measured 0.25 s on Kolkata's 314 candidates over a 41.5k-node
  graph).
* **k = 1 regression**: greedy's first pick reproduces the known headline
  numbers exactly (Kolkata +42,711 = analysis-v0.2; Freiburg's first pick is
  the Rank-1 site +18,037), as required by the shared-machinery design.
* The guarantee holds in practice: measured gaps 0.00-1.22%, far above the
  63.2% floor. Where greedy lost (Freiburg, 700 people), the exact solver
  found a genuinely different set — the benchmark does real work.
* Greedy's per-site gains diminish submodularly (Freiburg: 18.0k, 16.8k,
  10.6k, 7.8k, 3.3k) and it stops early when the budget cannot buy more
  coverage (Kolkata: 4 sites exhaust the reachable uncovered population).

What joint selection buys over the independent ranking (5-min threshold,
existing facilities included in the baseline):

| City | Baseline | + best single site | + jointly selected set | Set size |
|---|---|---|---|---|
| Freiburg | 66.4% | 74.8% | **92.7%** (199,963 / 215,793) | 5 |
| Heidelberg | 77.9% | 87.6% | **99.0%** (152,104 / 153,618) | 5 |
| Kolkata | 98.9% | 99.6% | **100.0%** (6,060,054 / 6,060,054) | 4 |

Arithmetic cross-check: baseline covered + greedy objective equals the
optimized-set covered population in every city (e.g. Kolkata
5,995,665 + 64,389 = 6,060,054), i.e. the two views of "newly covered" agree.
The ranking's top-N list should no longer be read as a site set: ranks 2-5
double-count the population that Rank-1 already covers.

Reproduction:

```bash
python -m backend.main --city freiburg --threshold 300 --num-sites 5 --mode both
python -m backend.main --city heidelberg --threshold 300 --num-sites 5 --mode both
python -m backend.main --city kolkata --threshold 300 --num-sites 10 --mode both
python -m backend.main --city kolkata --threshold 300 --num-sites 1 --mode both   # +42,711
```

## 2SFCA accessibility index (second scoring add-on)

`backend/objectives/twosfca.py` implements the enhanced two-step floating
catchment area index (E2SFCA, Luo & Qi 2009) with Gaussian decay over drive
time: step 1 sums each facility's decay-weighted population load, step 2
assigns each population cell the decayed share of nearby capacity. Beta is
calibrated Luo-&-Qi style so the weight at the threshold is ~0.01
(beta = tau_min^2 / ln(100); 5 min -> beta = 5.43 min^2).

Implementation notes that matter:

* **No facility-node dedup** (deliberately unlike the coverage scorer):
  facilities sharing a road node pool their capacity there.
* Both steps run from the same facility-side Dijkstras on the reversed
  graph (person -> facility times, same convention as the binary metric).
* An exact invariant holds and is unit-tested: sum_i P_i A_i = total
  capacity, for any decay and population distribution.
* Per-cell scores are exported to
  `data/processed/<city>/accessibility_{baseline,optimized}.gpkg` for QGIS
  and the planned demo map.

Freiburg (5-min threshold, 9 existing + 5 jointly selected facilities):

| Scenario | Facilities | Pop-weighted index | Cell max |
|---|---|---|---|
| Baseline | 9 | 0.0000417 | 0.00025 |
| Optimized | 14 | 0.0000649 (+55.6%) | 0.00071 |

**Caveat:** with uniform (presence-only)
capacity, the capacity identity pins the population-weighted city index to
exactly `num_facilities / total_population` -- the +55.6% above is 14/9 - 1.
The city-level scalar therefore carries no more information than the
facility count at this stage. What the index *does* add today is the
**spatial distribution of access intensity** (per-cell decayed share of
capacity; cell max nearly tripled around the new sites) and a formulation
that becomes genuinely informative the moment per-facility capacity data
(beds, patient loads) is available -- `compute_2sfca(..., capacities=[...])`
already accepts it.

Reproduction: any `--num-sites k` run now prints both 2SFCA scenarios and
writes the cell-score GPKGs.

## Facility curation review (the German-language mishap)

> Reviewing facilities blocklist reshaped the filter
> itself (`backend/gis/hospitals.py`, regression-tested in
> `tests/test_hospital_curation.py`).

**Finding: the name blocklist is inert outside English.** It is a list of
English substrings ("clinic", "dental", "lab", ...). German "Klinik" does
not match "clinic"; Japanese/Korean/Thai/Cyrillic names cannot match at all.
Real-cache audit:

| City | raw rows | kept | dropped | verdict |
|---|---|---|---|---|
| Heidelberg | 32 | 32 | 0 | blocklist never fired — unreviewed, not clean |
| Freiburg | 9 | 9 | 0 | same |
| Mysuru (control, English names) | 106 | 85 | 21 | blocklist works as designed |

The old printout (`dropped 0 mis-tagged`) read like verification had
happened; in Germany nothing had been verified. Kept-set inspection: 19 of
32 Heidelberg rows are UKHD campus sub-institutes (specialty clinics, NCT,
therapy centers) snapping to only 22 distinct graph nodes. That makes German
coverage/MCLP results *conservative* (facility-heavy), but the
uniform-capacity 2SFCA city scalar (32 / 153,618) is overstated ~1.5x
against ~13 genuinely independent inpatient institutions. Reference lists
are themselves imperfect: German Wikipedia lists 12 hospital articles for
Heidelberg and 4 for Freiburg (notability-gated; misses Ethianum, Salem,
ATOS) — consistent with the standing "not a census" caveat.

**First fix attempt, caught by the real-cache check and reverted.**
Structural tags (`healthcare=*`, `beds=*`, `building=*`) were added as
language-independent overrides of the name blocklist. Validation immediately
showed a reproducibility break: Mysuru kept 87 instead of the documented 85,
because `healthcare=hospital` is applied broadly by mappers — including on
two facilities deliberately curated out in v0.1 (a dental clinic and a
polyclinic/day-care). Generic tags are context, not inpatient evidence.

**Shipped semantics:**

* A polygon footprint (as in v0.1) or mapped `beds > 0` — a deliberate,
  checkable claim of inpatient care — overrides any name-blocklist hit, in
  any language.
* Generic tags (`healthcare`, `building`) are preserved and reported as
  review aids, never overrides; explicit per-city exclude verdicts stay
  absolute.
* Caches without structural columns (every pre-v0.3 fetch) print an explicit
  "name-based only, not reviewed for this city's language" note instead of
  the misleading `dropped 0`.
* `download.py` preserves `amenity/healthcare/beds/building/operator` so
  future fetches carry the evidence. Existing caches keep their documented
  counts exactly (verified: 85 / 32 / 9 unchanged end-to-end).

## Kraków, Poland — end-to-end fresh-city run (fourth country)

> The first city onboarded from zero through the
> complete v0.3 fetch path: hardened Overpass (mirror chain + combined
> forbidden query) AND the tag-preserving facility downloader +
> evidence-based curation.

**Benchmark:** full bootstrap + 5-min run in **8m54s wall** (geocode →
roads → hospitals → forbidden → POL raster → population → pipeline with
MCLP + 2SFCA + uncertainty). Under the < 10 min benchmark, but server-bound
this time: the forbidden fetch took 2m36s and the POL density raster 1m41s
(~27 KB/s on data.worldpop.org); the warm-cache pipeline portion itself is
~3 min. A fast-network city reproduces the 92 s Freiburg figure.

**Data:** UTM 34834 → EPSG:32634; roads 9,860 nodes / 21,620 edges (0.70 MB
slim); POL raster 2.7 MB (shared, future Polish cities are offline);
589 population cells, **751,856 modeled people vs 816,614 official
(city proper, GUS end-2025) — within ~7.9%**, against the 5–10% benchmark.

**Facilities & curation (first live exercise of the new path):** 27 raw
`amenity=hospital` rows; the new downloader preserved
`amenity/healthcare/building/operator` (25/27 carry `healthcare`; no `beds`
mapped in Kraków's OSM). Curation: kept 26 (1 same-name duplicate dropped,
0 name-blocklist hits). Names are genuine hospitals (Szpital Uniwersytecki
CM UJ, Centrum Onkologii, ...); Polish "Szpital" is invisible to the English
blocklist and needed no curation — consistent with the Kyoto finding that
high keep-rates are correct where tagging culture is clean.

**Results, 5-minute threshold (k = 5, mode both):**

* Baseline coverage: 73.5% (552,706 / 751,856) — second-lowest in the suite
  after Freiburg; median 3.2 min, max 17.8 min
* Best single site: +5.4 pp, **+40,751 people** (78.9%)
* Joint MCLP set (k = 5): **91.7% (689,089)** — +18.2 pp over baseline;
  greedy objective 136,383, exact 136,383, **gap 0.00%**, exact solve 0.32 s
* Greedy gains diminish submodularly: 40.8k → 35.7k → 31.0k → 17.1k → 11.8k
* 2SFCA: 26 → 31 facilities, pop-weighted index +20.0%; 2,534 people (0.3%)
  unreachable (no road path)

**15-minute threshold:** 99.5% baseline (748,122), best gain +1,516
(+0.2 pp) — nearly saturated, matching the suite-wide saturation pattern
(every city saturates at 15 min; the placement problem lives at 5 min).

**Uncertainty (seed = 0, n_boot = 200; sensitivity, not CIs):** headline
+40,751 gain is robust to any single cell-cluster (delete-1 range
[38,635, 40,751] over 556 clusters) but spans [33,445, 59,940] under column
bootstrap / [37,608, 62,861] under cell bootstrap. Total population
jackknife SE 22,684; mclp_objective 136,383 with 95% cell-bootstrap range
[126,863, 178,568].

Reproduction:

```bash
python -m backend.main --city krakow --place "Kraków, Poland" \
  --threshold 300 --num-sites 5 --mode both --uncertainty
python -m backend.main --city krakow --threshold 900
```

## The remaining cities: 5-minute joint MCLP results now complete 

 Guwahati, Patna, Kampala(joint k=5 budget, mode both) and Mysuru under its restricted 19-facility
class (the only configuration that exposes a placement problem in that city;
the all-85 network is saturated at 5 minutes, +0). All runs are warm-cache,
offline, at the 300 s threshold.

| City | Baseline | + best single site | + jointly selected set | Set size |
|---|---|---|---|---|
| Guwahati | 85.4% | 89.6% | **99.8%** | 5 |
| Mysuru (19 polygon-mapped) | 93.5% | 97.0% | **100.0%** | 2 |
| Patna | 98.6% | 99.4% | **99.6%** | 2 |
| Kampala | 98.7% | 99.8% | **99.8%** | 1 |

Results in detail:

* **Guwahati** — the largest gap in the suite, and the largest joint win: a
  single site (Rank-1, +20,657) lifts the city from 85.4% to 89.6%,
  **but a 5-site joint set reaches 99.8% (+70,082)** — the largest joint
  gain in the suite — with the max drive time cut from 11.5 to 5.3 min.
  Greedy and the exact MILP agree exactly (gap 0.00%). The best single
  site leaves ~50.5k people outside the window; the joint set cuts that
  to 1,041 (0.2%).
* **Mysuru (restricted class)** — baseline 93.5% (638,055 / 682,746) with
  the 19 polygon-mapped hospitals. Rank-1 gain +24,140 (reproduces the
  v0.2 headline exactly), lifting coverage to 97.0%. The jointly selected
  set of just **2 sites reaches 100.0% (+44,691)**, max drive time 7.5 →
  4.8 min (greedy–exact gap 0.00%). Greedy spends only 2 of the 5 budgeted
  sites; the exact MILP matches the objective with 3 (optimal sets are not
  unique). (Full 85-facility network is saturated;
  see the 100.0% / +0 row in the cross-city summary.)
* **Patna** — baseline 98.6% (2,058,883 / 2,087,503). Rank-1 gain +17,094
  (reproduces the v0.2 headline exactly), 99.4%. The jointly selected set
  is **2 sites** (not 5): greedy and exact agree on the optimum (gap
  0.00%) and stop early because the remaining uncovered population is
  exactly the 9,146 people with no road path to any facility — the same
  behaviour Kolkata shows at k=4. Coverage reaches 99.6%; **nothing more
  is reachable, regardless of budget**.
* **Kampala** — baseline 98.7% (1,643,049 / 1,665,310). Rank-1 gain
  +19,395 reproduces the documented headline exactly; joint greedy–exact
  agree at k=1 (gap 0.00%) because the single best site already exhausts
  every reachable uncovered person, leaving only the 2,865 people (0.2%)
  with no road path. 2SFCA city index: +0.6% relative — a trivial
  capacity-weighted change for the same reason.

**What this adds to the finding.** The three near-saturated cities
(Kolkata, Patna, Kampala) all behave identically once the reachable
uncovered population is exhausted: the joint selector returns k=1, 2, and
4 sites respectively (Kolkata's k=4 earlier), and no budget can buy more
than the network physically allows. Guwahati is the counter-example that
makes the whole suite coherent: with a genuine 5-min access gap
(85.4% baseline), the joint set of 5 does what none of the rank-and-pick
lists could — it reaches 99.8%, i.e. the same league as Freiburg's
joint result, confirming that **where the access gap is structural (not
saturated), joint selection is the only strategy that closes it**; where
the gap is nearly saturated, the optimizer correctly reports "nothing
more is reachable" rather than padding the budget.

## Cross-city summary (5-minute threshold, all curated facilities)

| City | Baseline | Optimized | Best gain | Source |
|---|---|---|---|---|
| Guwahati | 85.4% | 89.6% | +20,657 | analysis-v0.2 |
| Kolkata | 98.9% | 99.6% | +42,711 | analysis-v0.2 |
| Patna | 98.6% | 99.4% | +17,094 | analysis-v0.2 |
| Mysuru (19 polygon-mapped) | 93.5% | 97.0% | +24,140 | analysis-v0.2 |
| **Heidelberg** | **77.9%** | **87.6%** | **+14,867** | this analysis |
| **Freiburg** | **66.4%** | **74.8%** | **+18,037** | this analysis |
| Kampala | 98.7% | 99.8% | +19,395 | analysis-v0.2 (unchanged) |
| **Kraków** | **73.5%** | **78.9%** | **+40,751** | this analysis (fresh city, POL) |
| **Guwahati (5-min)** | **85.4%** | **99.8%** | **+70,082** | this analysis, joint k=5 |
| **Patna (5-min)** | **98.6%** | **99.6%** | **+19,473** | this analysis, joint k=2  |
| **Kampala (5-min)** | **98.7%** | **99.8%** | **+19,395** | this analysis, joint k=1  |
| **Mysuru (5-min, restricted 19)** | **93.5%** | **100.0%** | **+44,690** | this analysis, joint k=2  |

Freiburg (5-min: 66.4% → 74.8%, +8.4 pp, 262 cells / ~215,793 people) now
anchors the low-coverage end of the suite, which makes both German cities
the most instructive for the v0.3 demo artifact (visible before/after).

## Current limitations

- **Demand:** modeled WorldPop 1 km centroid values (not census counts);
  the `-0.0 min` cell-centroid artifact persists (reported per run as
  `pop_at_zero`).
- **Supply:** the OSM facility inventory is not a census; capacities,
  specialties and quality are unmodeled.
- **Travel time:** the modeled network only. No traffic, time of day or
  emergency routing; drive times are not yet validated against an external
  router.
- **Optimization:** selected sites are capacity less points; the 2SFCA city
  scalar mirrors the facility count under uniform capacity until bed data
  is supplied (`capacities=` is already plumbed; the per cell intensity
  surface is informative already).
- **Filtering:** `--skip-forbidden` runs exclude water/park/cemetery land
  only when the cache exists; the printed warning marks such runs as not
  comparable.
- **Reporting:** the demo artifact (public before/after map over the API)
  is still pending, results currently surface only through the CLI,
  GPKG exports, and these documents.

## Minor tweaks after v0.3

Ordered by leverage for the project's stated goal: a methods backbone for
public-health accessibility work. 

Near term:

1. **FastAPI + Leaflet demo on a public URL** : a thin API over `run_pipeline`'s return dict and an interactive
   before/after map. 

2. **External validation of drive times** — verified not started (nothing
   in the codebase touches an external router). Sample ~50
   origin–destination pairs across cities and cross-check modeled times
   against OSRM/Valhalla to bound the network-model error.

3. **Per-facility capacity (beds)** for 2SFCA, half-built: the
   `capacities=` parameter is plumbed and `download.py` already preserves
   `beds=*` on future fetches, but no current city cache maps beds and the
   pipeline does not yet wire a beds column into `capacities=`. Remaining:
   that wiring plus a national-registry fallback where OSM has none.
   Closes the uniform-capacity caveat.


## References

- Church, R., ReVelle, C. (1974). The maximal covering location problem.
  *Papers of the Regional Science Association*, 32, 101–118.
- Minoux, M. (1978). Accelerated greedy algorithms for maximizing submodular
  set functions. *Optimization Techniques*, LNCS 7, 234–243.
- Luo, W., Qi, Y. (2009). An enhanced two-step floating catchment area
  (E2SFCA) method for measuring spatial accessibility to primary care
  physicians. *Health & Place*, 15(4), 1100–1107.
- WorldPop (2020). Global 1-km population density rasters
  (`GIS/Population_Density/Global_2000_2020_1km`), www.worldpop.org.
- OpenStreetMap contributors; Overpass API; OSMnx (Boeing, G., 2017,
  *Computers, Environment and Urban Systems*).
