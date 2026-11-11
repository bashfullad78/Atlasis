# ATLASIS

> **v0.4**
> Results in this version should not be used for real world planning decisions.
> See [Current limitations](#current-limitations), the [version history](#version-history-and-documents), and the full analyses in `docs/analyses/`.

ATLASIS recommends where to place new hospitals in a city so that the maximum number of people can reach one within a fixed drive time (default: 15 minutes; the interesting cases live at 5). It runs entirely on free data: OpenStreetMap road networks via OSMnx and WorldPop population rasters. Beyond ranking single sites, it can select a complementary *set* of k sites jointly (MCLP), grade access with an E2SFCA index, and attach uncertainty intervals to every headline number, with no paid data or API.

---

## How the pipeline works

1. **Data ingestion** — city boundary, drivable road graph, existing hospitals (`amenity=hospital` from OSM), forbidden zones (water, parks, cemeteries), and a WorldPop population raster. Anything missing is fetched automatically by the one-command bootstrap (see [Downloading data](#downloading-data)).
2. **Candidate generation** — a grid of possible sites inside the city boundary.
3. **Hard constraints** — drops sites too close to an existing hospital, too far from a road, or inside forbidden zones.
4. **Scoring** — each candidate is scored by the population it would *newly* bring within the travel time threshold of a hospital. Uses road network drive time, not straight line distance. One coverage-matrix pass feeds both the independent ranking and the joint selection.
5. **Joint selection (MCLP)** — selects a *set* of k sites jointly: a lazy greedy solver with the >= (1 − 1/e) approximation guarantee, benchmarked against an exact MILP (PuLP/CBC). Greedy stops early when the budget cannot buy more coverage.
6. **Ranking, accessibility and uncertainty reporting** — top candidates, before/after accessibility (binary coverage plus population-weighted drive-time stats), per-cell E2SFCA accessibility surfaces, and optional jackknife/bootstrap intervals on the headline figures.

## What v0.3 adds

v0.3 keeps the pipeline above and adds the methodological layers plus a hardened data path:

- **Joint multi-facility selection (MCLP).** `--num-sites k --mode greedy|exact|both`: lazy greedy (Minoux) with the (1 − 1/e) guarantee, benchmarked against an exact MILP on the same coverage matrix. Measured greedy–exact gaps 0.00–1.22%; exact solves take < 0.5 s on ~300-candidate instances; k = 1 reproduces the legacy ranking exactly. Where the access gap is structural, only the joint set closes it (Freiburg 66.4% → 92.7%; Guwahati 85.4% → 99.8%); where the reachable uncovered population is exhausted, the optimizer stops early and says so (Kolkata k=4, Patna k=2, Kampala k=1).
- **E2SFCA accessibility index.** Enhanced two-step floating catchment area (Luo & Qi 2009) with Gaussian decay over drive time; per-cell scores exported to QGIS-ready GPKGs. With uniform capacity the city-level scalar mirrors the facility count — the per-cell intensity surface is the informative part today (see `docs/analyses/analysis-v0.3.md`).
- **Uncertainty reporting.** `--uncertainty`: delete-1 jackknife plus seeded column/cell bootstraps over the headline figures, computed from the frozen coverage matrix (sensitivity intervals, not CIs — the demand surface is modeled).
- **Overpass hardening.** Mirror failover with fast-fail timeouts, one combined forbidden-zone request instead of five, `--skip-forbidden` for very unstable networks. A fresh city bootstraps in 92 s wall (Freiburg; worst observed 8m54s for Kraków, server-bound).
- **Hardened facility curation.** A polygon footprint or mapped `beds > 0` overrides the (English-only) name blocklist in any language; generic tags are review aids, never overrides; the Kyoto test (`tests/test_hospital_curation_kyoto.py`) pins language independence by construction.
- **Four countries, eight cities.** Heidelberg, Freiburg, Kraków onboarded alongside the four Indian cities and Kampala; population totals land within ~1–8% of official figures. All eight cities now have joint-selection results. Non-technical write-up: `docs/case-study-v0.3.md` (figures + PDF).
- **CI.** GitHub Actions runs the offline test suite (97 tests) on every push/PR; the few real-cache pins skip automatically on a fresh checkout.

Full methods and per-city results: `docs/analyses/analysis-v0.3.md`. Implementation log: `docs/logs/v0.3-log.md`.

## What v0.4 adds

v0.4 turns the prototype into a runnable demo and closes two roadmap items:

- **Async Jobs API.** `backend/api/` — submit a city/threshold/k and poll it to completion behind FastAPI (`python -m backend.api.app`, port 7860): single worker, queue cap 1, per-IP soft rate limit, failure surfaced as job state, monotonic elapsed time (the container clock jumps), and a 240 s acquisition fallback that retries with `skip_forbidden` and flags the payload **NOT comparable** instead of failing.
- **Interactive demo map.** `web/` — dark OSM-style Leaflet UI served from the same port: form → live stages → before/after coverage layers with server-computed isochrones, site list, provenance stamps, mobile layout, reduced-motion respected.
- **Provenance stamps.** Every result carries `data_stamps`/`data_as_of` from `meta.json`, so cache staleness is visible in both the API payload and the UI.
- **External drive-time validation** (roadmap #2). `scripts/validate_drive_times.py` cross-checks modeled times against the public OSRM router on 50 seeded OD pairs per city; four cities validated. Tag-rich cities (Freiburg, Kraków) run a systematic **~24% optimistic** — every pair positive, so a 15-min guarantee behaves like 15–19 min; tag-sparse cities (Guwahati, Kampala) show **no usable scale factor** (sign-mixed, Kampala even pessimistic on long trips). `docs/analyses/validation-v0.4.md`.
- **Bed capacity in the 2SFCA** (roadmap #3): mapped OSM `beds` become per-facility capacities (uniform 1.0 fallback where absent); runs print "Bed capacity modeled: N/M facilities".
- **Acquisition resilience, live-verified**: the last-good Overpass mirror is trusted without a status probe (probe traffic itself gets throttled), mirror failover proven live across five fresh city fetches, and the fallback→pipeline `skip_forbidden` bug that live fire exposed fixed with a regression test.
- **Suite grew 97 → 137 tests** (still fully offline), and the CI install gap it uncovered (`httpx2`, required by starlette's TestClient, now pinned in `requirements.txt`).

Validation write-up: `docs/analyses/validation-v0.4.md`. Implementation log: `docs/logs/v0.4-log.md`.

## What v0.3 does not do

*(Historical at v0.3: the API/map, external validation and bed wiring shipped in v0.4 — realtime traffic and parcel feasibility did not; current state lives in [Current limitations](#current-limitations).)*

- Hospital capacity or bed availability (a small clinic counts the same as a large hospital; the 2SFCA `capacities=` parameter is plumbed but no city cache maps beds yet).
- Real time or time of day traffic; drive times are not yet validated against an external router (planned; see the roadmap in `docs/analyses/analysis-v0.3.md`).
- Parcel level feasibility (land ownership, zoning, cost).
- Public-facing API and interactive demo map — the remaining v0.3 deliverable.

---

## Setup

Prerequisites: Python 3.12+ (the CI matrix pins 3.12) and `git`. All
dependencies install from `requirements.txt` via pip — no system GIS
stack is needed (GeoPandas/Fiona/PyProj install as wheels).

```bash
git clone https://github.com/bashfullad78/Atlasis.git
cd Atlasis
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Verify the install by running the offline test suite — it makes no
network calls and needs no data (~15 s; real-cache pins skip
automatically where `data/` has no caches):

```bash
pytest tests/ -q    # 137 tests
```

To try the Jobs API + interactive map:

```bash
python -m backend.api.app   # then open http://localhost:7860
```

Next: fetch a city and run the pipeline — see [Downloading data](#downloading-data)
and [Running the optimization](#running-the-optimization). Everything
caches under `data/raw/<city>/`; a warm cache makes zero network calls.

## Downloading data

The pipeline bootstraps any city automatically: `python -m backend.main --city X --place "City, Country"` fetches whatever is missing (boundary, roads, hospitals, forbidden zones, population cache) before optimizing, and a warm cache triggers zero network calls. Helper flags:

```bash
python -m backend.main --city kampala --place "Kampala, Uganda" --probe   # list what exists / would be downloaded
python -m backend.main --city mysuru --no-download                        # run only if everything is cached
```

The individual builders can still be run by hand if needed:

```bash
python -m backend.data.download --city mysuru          # boundary, roads, hospitals
python -m backend.constraints.forbidden --city mysuru  # water / parks / cemeteries
python -m backend.data.population --city mysuru        # build population cache
```

Pass `--country ISO3` (e.g. `UGA`, `DEU`) to skip automatic country detection; otherwise the country is resolved from the geocoder and stored in `data/raw/<city>/meta.json` (`iso_country`). Resolved facts are written back to `meta.json` automatically (country, UTM zone, population-source provenance), so after the first successful build, subsequent runs — including population-cache rebuilds — work fully offline.

All Overpass-backed fetches run through a mirror failover chain (`overpass-api.de` → `overpass.kumi.systems`, overridable via `ATLASIS_OVERPASS_MIRRORS`): each call probes mirror health first and fails over on any error. On very unstable networks, `--skip-forbidden` defers the forbidden-zone fetch and disables the filter (results are then not comparable to filtered runs; the filter re-enables automatically once `forbidden.gpkg` exists).

Data lands in `data/raw/<city>/`. A `meta.json` there records the city's UTM zone and country, computed automatically from its location. City data is gitignored (`data/raw`, `data/processed`) — every artifact is rebuilt from one bootstrap command; the committed code is the source of truth.

### Population data

The WorldPop 1 km population-DENSITY raster for the city's country is downloaded once into `data/raw/worldpop/<ISO3>/` (India 18 MB, Uganda 1.1 MB, Germany 3 MB, Poland 2.7 MB), shared across all cities of that country, cropped to the city boundary, converted to **people per cell** (density x geodesic cell area), and cached as a small Parquet file (`data/raw/<city>/population.parquet`). The national raster can be deleted once all target cities of that country have caches.

The windowed streaming approach (`/vsicurl`) was evaluated and rejected: `data.worldpop.org` does not honor HTTP Range requests (it advertises `Accept-Ranges` but returns the full file), so only the small 1 km density products are practical to fetch. The 100 m population-count rasters would require downloading entire countries (India: 1.8 GB).

### Storage

Road graphs are stored as two compressed Parquet tables (one row per node / directed edge), keeping only the tags the travel-time model consumes — roughly 10x smaller than OSMnx GraphML (e.g. Mysuru: 19.9 MB -> 1.2 MB) with topology verified identical. Legacy `roads.graphml` files remain loadable for un-migrated cities. `python -m backend.data.cleanup` reports (and with `--apply` deletes) re-derivable artifacts: superseded GraphMLs, processed outputs, interrupted-download leftovers, and national rasters whose every city already has a population cache.

## Running the optimization

```bash
python -m backend.main --city mysuru
python -m backend.main --city bengaluru --place "Bengaluru, Karnataka, India"
python -m backend.main --city mysuru --threshold 300   # 5-minute threshold

# Full v0.3 run: joint selection of 5 sites (greedy vs exact) + uncertainty
python -m backend.main --city freiburg --place "Freiburg im Breisgau, Germany" \
    --threshold 300 --num-sites 5 --mode both --uncertainty
```

Flags:

| Flag | Default | Description |
|---|---|---|
| `--city` | required | Slug for the data directory (`data/raw/<city>/`) |
| `--place` | none | Any geocodable place name, in any country |
| `--country` | auto | ISO 3166-1 alpha-3 code (e.g. `IND`, `UGA`, `DEU`); auto resolved when omitted |
| `--spacing` | 500 | Candidate grid spacing in metres |
| `--top-n` | 5 | Number of sites to report |
| `--threshold` | 900 | Drive time guarantee in seconds (900 = 15 min) |
| `--num-sites` | 1 | Jointly select this many sites (MCLP) |
| `--mode` | greedy | MCLP solver: `greedy`, `exact` (PuLP/CBC benchmark), or `both` (reports the gap) |
| `--uncertainty` | off | Jackknife + bootstrap sensitivity intervals on the headline figures |
| `--n-boot` | 200 | Bootstrap replicates for `--uncertainty` |
| `--seed` | 0 | RNG seed for `--uncertainty` |
| `--probe` | off | List what exists / would be downloaded, then exit |
| `--no-download` | off | Run only if every artifact is already cached |
| `--skip-forbidden` | off | Run without the forbidden filter and defer its fetch (unstable-network workaround; not comparable to filtered runs) |

Per city defaults (spacing, threshold, distances, facility tags) live in the `CityConfig` dataclass in `backend/config.py`.

Results are written to `data/processed/<city>/`: `top_sites.gpkg` (ranked + jointly selected sites) and `accessibility_{baseline,optimized}.gpkg` (per-cell E2SFCA scores), all openable directly in QGIS.

---

## Reading the results

### Saturation

The most common result across the suite is that a 15-minute threshold is already saturated: in every one of the eight cities, essentially all modeled population is within 15 minutes of an existing hospital, so no candidate produces a nonzero gain.

This is a correct result, not a failure. It means the placement problem is not meaningful at the chosen threshold. Before concluding there is nothing to optimize, tighten the threshold:

```bash
python -m backend.main --city mysuru --threshold 300   # 5 minutes
```

Across all eight cities, a 5-minute threshold exposes the real gaps (baselines 66.4% in Freiburg to 98.9% in Kolkata; Mysuru's gap appears only once the facility class is restricted to polygon-mapped hospitals). Full results: `docs/analyses/analysis-v0.1.md` (provisional v0.1 figures), `analysis-v0.2.md` (corrected rerun), `analysis-v0.3.md` (v0.3 layers + all joint selections).

### The 0.0 min artifact

Population comes from 1 km WorldPop cells whose population is modeled at the cell centroid. A cell whose centroid falls on a hospital node gets a travel time of 0.0 minutes. The run reports how many people this affects. It is a data resolution artifact, not a bug. The v0.2 density-to-count correction changes the reported number of people (they are now true counts) but not the artifact itself.

### OSM hospital curation

`amenity=hospital` in OSM includes clinics, diagnostic centers, dental practices, and occasionally unrelated facilities. The loader in `backend/gis/hospitals.py` keeps polygon-mapped facilities, drops explicit per-city excludes, and applies an English name blocklist to points — **unless structural evidence (polygon footprint or mapped `beds > 0`) overrides it, in any language**. The name blocklist is inert outside English (that is a documented finding, not a bug), so review the kept set per city before interpreting results; caches without structural tags print an explicit "name-based only" note. See the curation review in `docs/analyses/analysis-v0.3.md`.

---

## Threshold selection

| Lever | Effect | Example |
|---|---|---|
| Lower the threshold | Stricter access standard | 5min threshold: Guwahati 85.4% baseline, best single site +20,657 people, joint set of 5 → 99.8% |
| Restrict the facility class | Only major hospitals count as served | Mysuru with the 19 polygon mapped hospitals: 5min baseline drops from 100.0% to 93.5%, best candidate adds +24,140; joint set of 2 → 100.0% (`scripts/run_restricted_mysuru.py`) |
| Model capacity | Crowded hospitals do not serve their full catchment | OSM `beds=` now feeds `capacities=` where mapped (uniform 1.0 fallback); full patient-load data still absent |

Always run the baseline first and check coverage at the chosen threshold. If coverage is near 100%, lower the threshold or restrict the facility class before scoring candidates.

---

## Current limitations

- **Population data:** Population is the WorldPop 1 km density product converted to people per cell via density x geodesic cell area. This corrects the v0.1 density-as-count error, but the underlying estimates remain modeled (not census counts) and 1 km cells locate population at cell centroids (the `0.0 min` artifact is reported per run). Modeled totals land within ~1–8% of official figures where checked.
- **Population layer (v0.1 results):** All population figures in `docs/analyses/analysis-v0.1.md` predate the density-to-count correction and are provisional.
- **Hospital data:** OSM completeness varies by city. Retained facility counts should not be treated as a census of hospitals. Only OSM-mapped `beds` are modeled as capacity (uniform 1.0 elsewhere); specialty and quality are not modeled, and with mostly-uniform capacities the 2SFCA city-level index mirrors the facility count (the per-cell surface is the informative part).
- **Road network:** Travel times reflect the modeled network only. Real time traffic, congestion, road closures, and emergency routing are not represented. The OSRM cross-check (`docs/analyses/validation-v0.4.md`, four cities) bounds the model error: tag-rich cities run systematically ~20–25% optimistic (apply that scale factor to absolute guarantees), tag-sparse cities show no systematic offset but wider per-trip variance.
- **Candidate locations:** High scoring candidates are analysis locations. No parcel level feasibility check is performed; selected sites are capacity-less points.
- **Filtering:** `--skip-forbidden` runs exclude water/park/cemetery land only when the cache exists; the printed warning marks such runs as not comparable.
- **Reporting:** results surface through the CLI, GPKG exports, the documents — and the case study (`docs/case-study-v0.3.md`) — and through the Jobs API + interactive map (`python -m backend.api.app`, local/demo service; public hosting still pending).

---

## Version history and documents

### v0.1 — initial prototype

First end-to-end implementation (data ingestion, candidate generation, hard constraints, drive-time coverage scoring, ranking and reporting), exercised across four Indian cities: Mysuru, Kolkata, Patna, Guwahati.

Key findings (their structure is unchanged in v0.2):

- A 15-minute threshold is saturated in every city; a 5-minute threshold exposes measurable gains in three of four (Mysuru only once the facility class is restricted to polygon-mapped hospitals).
- Results are stable across candidate grid spacings (Patna, Guwahati).
- Saturation is an informative result, not a failure — the pipeline reports it explicitly.

Limitations specific to v0.1: the population layer stored WorldPop density values (people/km²) as if they were counts, so **every absolute v0.1 population figure is provisional** (see the data note in `docs/analyses/analysis-v0.1.md`). The Mysuru "94.7% @ 5 min" headline corresponds to the restricted facility class (~19 polygon-mapped hospitals), not the full ~85-facility network; the reconciliation is in `docs/analyses/analysis-v0.2.md`.

Full write-up: `docs/analyses/analysis-v0.1.md`.

### v0.2 — corrected population layer

Rebuilt data foundation on the v0.1 pipeline:

- **Corrected people counts.** v0.1 treated WorldPop *density* values (people/km²) as if they were population counts. v0.2 converts exactly: `count = density × geodesic cell area`, with the area varying by latitude on the WGS84 grid. Every population figure is now a true people count.
- **Country-agnostic data layer.** Any geocodable `--place "City, Country"` works: the ISO 3166-1 country code is resolved automatically, the national 1 km density raster is downloaded once per country and shared across its cities, and each city's population cache is built from it. Validated end to end on Kampala, Uganda: ~1,665,310 modeled people, within ~1% of the official city population.
- **One-command bootstrap.** `python -m backend.main --city X --place "City, Country"` fetches whatever is missing before optimizing (resumable; fully offline once cached). `--probe` previews, `--no-download` forbids fetching. Resolved facts (country, UTM zone, population provenance) persist to `meta.json`, so later runs — including population-cache rebuilds — need no network.
- **Slim road graphs.** OSMnx GraphML → two compressed Parquet tables per city, roughly 10x smaller with topology verified identical (e.g. Mysuru 19.9 MB → 1.2 MB), plus a cleanup tool for re-derivable artifacts.
- **All v0.1 experiments rerun on corrected weights.** Every v0.1 figure reconciles mechanically via a per-city latitude factor; corrected numbers in `docs/analyses/analysis-v0.2.md`.

Full write-up: `docs/analyses/analysis-v0.2.md` (includes the mechanical v0.1 → v0.2 reconciliation). Implementation log: `docs/logs/v0.2-log.md`.

### v0.3 — methodological layers, four countries, case study

On the stable v0.2 base:

- **Joint MCLP selection** (`backend/optimizers/mclp.py`): lazy greedy with the (1 − 1/e) guarantee + exact PuLP/CBC benchmark on one shared coverage matrix; the independent ranking and joint selection cost one Dijkstra pass total. Joint selections now published for **all eight cities** (Freiburg 92.7% at k=5, Kraków 91.7%, Heidelberg 99.0%, Guwahati 99.8%, Mysuru restricted 100.0% at k=2, Patna 99.6% at k=2, Kampala 99.8% at k=1, Kolkata 100.0% at k=4).
- **E2SFCA accessibility index** (`backend/objectives/twosfca.py`) with per-cell GPKG exports, and **uncertainty reporting** (`backend/objectives/uncertainty.py`: delete-1 jackknife + seeded bootstraps).
- **Overpass hardening**: mirror failover, fast-fail timeouts, one combined forbidden query, `--skip-forbidden`; fresh-city bootstrap 92 s (Freiburg) to 8m54s (Kraków, server-bound).
- **Evidence-based facility curation** (language-independent: polygon/beds override the English blocklist; ASCII-only logic pinned by the Kyoto test), with an honest audit of the old name-only filter.
- **New cities**: Heidelberg and Freiburg (DEU) and Kraków (POL) onboarded — suite now 8 cities across 4 countries (IND, UGA, DEU, POL); a forbidden-zone tag-sensitivity check (Heidelberg, Kraków) changes none of the published results.
- **Case study with figures** (`docs/case-study-v0.3.md`, figures rendered offline by `scripts/render_case_study_figures.py`) and **CI** on the offline test suite (`.github/workflows/ci.yml`).

Full write-up: `docs/analyses/analysis-v0.3.md`. Implementation log: `docs/logs/v0.3-log.md`.

### v0.4 — jobs API, interactive map, external validation (current)

On the v0.3 base:

- **Jobs API + web UI** — the remaining v0.3 deliverable shipped: async jobs behind FastAPI (live stages, queue cap, per-IP rate limit, failure-as-state, 240 s `skip_forbidden` acquisition fallback flagged NOT comparable) with a dark OSM-style Leaflet front end (before/after coverage, server-computed isochrones, mobile, reduced-motion).
- **Provenance surfaced** — `meta.json` fetch stamps flow into every payload (`data_stamps`, `data_as_of`) and the UI footer.
- **Drive-time validation vs OSRM** — four cities × 50 pairs close "modeled times not yet validated" with a split verdict: tag-rich cities ~24% optimistic (carry the scale factor), tag-sparse cities no systematic offset. `docs/analyses/validation-v0.4.md`.
- **Bed capacity** wired from OSM `beds` into the 2SFCA (uniform 1.0 fallback where absent).
- **Acquisition hardening** — probe-free last-good-mirror trust, the 240 s fallback live-verified end to end, and the runner bug it exposed (the flag never reached the pipeline) fixed with a regression test.

Implementation log: `docs/logs/v0.4-log.md`.

## Roadmap after v0.3

Ordered by leverage; details and status in `docs/analyses/analysis-v0.3.md`:

1. **FastAPI + Leaflet demo on a public URL** — a thin API over `run_pipeline`'s return dict and an interactive before/after map (the remaining v0.3 deliverable). **API + map shipped in v0.4; the frontend + precomputed saved results deploy as a free static site (Vercel, root directory `web/`); live job submission stays local (`python -m backend.api.app`).**
2. **External validation of drive times** — cross-check modeled times against OSRM/Valhalla on ~50 OD pairs per city to bound the network-model error. **Done in v0.4 for four cities.**
3. **Per-facility capacity (beds)** for the 2SFCA — wire mapped `beds=` into `capacities=` on the next facility refetch; national-registry fallback where OSM has none. **Wired in v0.4 (OSM beds only; no national registry).**
4. **Release hygiene** — tag `v0.3`, release notes, optional Zenodo DOI (LICENSE and `CITATION.cff` are in place; CI runs).

---

## Repository layout

```
backend/
  config.py      CityConfig: per city settings, auto UTM CRS, data paths
  api/           Jobs API: app.py (FastAPI routes + static mount), runner.py
                 (worker thread, queue cap, rate limit, 240 s fallback)
  data/          download + population crop, density-to-count conversion & Parquet cache,
                 Overpass mirror failover (overpass.py), bootstrap orchestrator (prepare.py)
  gis/           boundary + hospital loading with evidence-based OSM curation
  routing/       road graph loading (slim Parquet format)
  candidates/    candidate site generation
  constraints/   hard filters (existing facilities, roads, forbidden zones)
  objectives/    scoring (coverage.py), accessibility (baseline.py), E2SFCA (twosfca.py),
                 uncertainty (uncertainty.py)
  optimizers/    independent ranking + joint MCLP selection (mclp.py) + result export
  pipeline.py    end to end orchestration
  main.py        CLI entry point

web/             interactive demo map: index.html + app.js (Leaflet, dark OSM style)

data/
  raw/<city>/    boundary.gpkg, roads_nodes.parquet + roads_edges.parquet (slim graph;
                 legacy cities may still carry roads.graphml), hospitals.gpkg,
                 forbidden.gpkg, population.parquet, meta.json
  raw/worldpop/<ISO3>/  shared national 1 km density raster (deletable once cached)
  processed/<city>/     top_sites.gpkg, accessibility_{baseline,optimized}.gpkg

tests/           offline test suite (137 tests; real-cache pins skip without data/)
.github/workflows/ci.yml  CI: install pinned deps, run the offline suite

docs/
  analyses/analysis-v0.1.md    Experimental findings across Mysuru, Kolkata, Patna, Guwahati
  analyses/analysis-v0.2.md    Corrected-population rerun of the same four cities (people per cell)
  analyses/analysis-v0.3.md    v0.3 findings: MCLP, E2SFCA, uncertainty, curation, all 8 cities
  analyses/validation-v0.4.md  Drive-time validation vs OSRM + tag coverage + cross-city
                                uncertainty (all 4 cities)
  case-study-v0.3.md           Non-technical four-country case study (figures in docs/figures/)
  logs/v0.2-log.md             v0.2 implementation log (population correction, bootstrap)
  logs/v0.3-log.md             v0.3 implementation log (what was built, file by file)
  logs/v0.4-log.md             v0.4 implementation log (jobs API, web UI, validation)

scripts/
  validate_drive_times.py      OSRM drive-time validation (50 seeded OD pairs per city)
  tag_coverage.py              Forbidden-tag coverage per city (offline, cached data only)
  fetch_forbidden_chunked.py   Chunked forbidden-zone fetch for Overpass 413 rejections
  render_case_study_figures.py Offline matplotlib rendering of the case-study figures,
                                with automated cross-checks against analysis-v0.3.md
  run_restricted_mysuru.py     Mysuru restricted-class (19 polygon-mapped) 5-min run
  export_gallery.py            Precompute per-city saved results for the demo map's
                               "Saved results" chips (writes web/gallery/, committed)
```

## License & citation

Code is released under the [MIT license](LICENSE). The data ATLASIS consumes
belongs to its providers: OpenStreetMap (ODbL) and WorldPop (CC BY 4.0) keep
their own terms — see the notice in `LICENSE`.

To cite ATLASIS, use the metadata in [`CITATION.cff`](CITATION.cff) (GitHub
renders it as a "Cite this repository" button), or:

> Zafar. (2026). *ATLASIS* (Version 0.4.0). GitHub.
> https://github.com/bashfullad78/Atlasis
