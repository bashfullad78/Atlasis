# ATLASIS

> **v0.2 — experimental prototype.**
> Results in this version should not be used for real world planning decisions.
> See [Current limitations](#current-limitations), the [version history](#version-history-and-documents), and the full analyses in `docs/analyses/`.

ATLASIS recommends where to place new hospitals in a city so that the maximum number of people can reach one within a fixed drive time (default: 15 minutes). It runs entirely on free data: OpenStreetMap road networks via OSMnx and WorldPop population rasters.

---

## How the pipeline works

1. **Data ingestion** — city boundary, drivable road graph, existing hospitals (`amenity=hospital` from OSM), forbidden zones (water, parks, cemeteries), and a WorldPop population raster. Anything missing is fetched automatically by the one-command bootstrap (see [Downloading data](#downloading-data)).
2. **Candidate generation** — a grid of possible sites inside the city boundary.
3. **Hard constraints** — drops sites too close to an existing hospital, too far from a road, or inside forbidden zones.
4. **Scoring** — each candidate is scored by the population it would *newly* bring within the travel time threshold of a hospital. Uses road network drive time, not straight line distance.
5. **Ranking and reporting** — reports the top candidates, then compares accessibility before and after adding the best site, including population weighted min/median/mean/max drive times.

## What v0.2 adds

v0.2 keeps the pipeline above and rebuilds its data foundation:

- **Corrected people counts.** v0.1 treated WorldPop *density* values (people/km²) as if they were population counts. v0.2 converts exactly: `count = density × geodesic cell area`, with the area varying by latitude on the WGS84 grid. Every population figure is now a true people count.
- **Country-agnostic data layer.** Any geocodable `--place "City, Country"` works: the ISO 3166-1 country code is resolved automatically, the national 1 km density raster is downloaded once per country and shared across its cities, and each city's population cache is built from it. Validated end to end on Kampala, Uganda: ~1,665,310 modeled people, within ~1% of the official city population.
- **One-command bootstrap.** `python -m backend.main --city X --place "City, Country"` fetches whatever is missing before optimizing (resumable; fully offline once cached). `--probe` previews, `--no-download` forbids fetching. Resolved facts (country, UTM zone, population provenance) persist to `meta.json`, so later runs — including population-cache rebuilds — need no network.
- **Slim road graphs.** OSMnx GraphML → two compressed Parquet tables per city, roughly 10x smaller with topology verified identical (e.g. Mysuru 19.9 MB → 1.2 MB), plus a cleanup tool for re-derivable artifacts.
- **All v0.1 experiments rerun on corrected weights.** Every v0.1 figure reconciles mechanically via a per-city latitude factor; corrected numbers in `docs/analyses/analysis-v0.2.md`.

## What v0.2 does not do

- Multi facility joint optimization: candidates are ranked independently and only the single best site is added. Selecting a complementary set of k sites is planned for v0.3 (see [Roadmap](#roadmap-v03-in-development)).
- Hospital capacity or bed availability (a small clinic counts the same as a large hospital).
- Real time or time of day traffic.
- Parcel level feasibility (land ownership, zoning, cost).

---

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Downloading data

The pipeline bootstraps any city automatically: `python -m backend.main --city X --place "City, Country"` fetches whatever is missing (boundary, roads, hospitals, forbidden zones, population cache) before optimizing, and a warm cache triggers zero network calls. Two helper flags:

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

Data lands in `data/raw/<city>/`. A `meta.json` there records the city's UTM zone and country, computed automatically from its location. City data is gitignored (`data/raw`, `data/processed`) — every artifact is rebuilt from one bootstrap command; the committed code is the source of truth.

### Population data

The WorldPop 1 km population-DENSITY raster for the city's country is downloaded once into `data/raw/worldpop/<ISO3>/` (India 18 MB, Uganda 1.1 MB, Germany 3 MB), shared across all cities of that country, cropped to the city boundary, converted to **people per cell** (density x geodesic cell area), and cached as a small Parquet file (`data/raw/<city>/population.parquet`). The national raster can be deleted once all target cities of that country have caches.

The windowed streaming approach (`/vsicurl`) was evaluated and rejected: `data.worldpop.org` does not honor HTTP Range requests (it advertises `Accept-Ranges` but returns the full file), so only the small 1 km density products are practical to fetch. The 100 m population-count rasters would require downloading entire countries (India: 1.8 GB).

### Storage

Road graphs are stored as two compressed Parquet tables (one row per node / directed edge), keeping only the tags the travel-time model consumes — roughly 10x smaller than OSMnx GraphML (e.g. Mysuru: 19.9 MB -> 1.2 MB) with topology verified identical. Legacy `roads.graphml` files remain loadable for un-migrated cities. `python -m backend.data.cleanup` reports (and with `--apply` deletes) re-derivable artifacts: superseded GraphMLs, processed outputs, interrupted-download leftovers, and national rasters whose every city already has a population cache.

## Running the optimization

```bash
python -m backend.main --city mysuru
python -m backend.main --city bengaluru --place "Bengaluru, Karnataka, India"
python -m backend.main --city mysuru --threshold 300   # 5minute threshold
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
| `--probe` | off | List what exists / would be downloaded, then exit |
| `--no-download` | off | Run only if every artifact is already cached |

Per city defaults (spacing, threshold, distances, facility tags) live in the `CityConfig` dataclass in `backend/config.py`.

Results are written to `data/processed/<city>/top_sites.gpkg`, which can be opened directly in QGIS.

---

## Reading the results

### Saturation

The most common result across v0.1 experiments is that a 15minute threshold is already saturated: every modeled population cell is within 15 minutes of an existing hospital, so no candidate produces a nonzero gain.

This is a correct result, not a failure. It means the placement problem is not meaningful at the chosen threshold. Before concluding there is nothing to optimize, tighten the threshold:

```bash
python -m backend.main --city mysuru --threshold 300   # 5 minutes
```

Findings across four cities (Mysuru, Kolkata, Patna, Guwahati) showed that a 15minute threshold was saturated in every case, while a 5minute threshold produced measurable gains in all four (Mysuru's only once the facility class is restricted to polygon-mapped hospitals). Full results are in `docs/analyses/analysis-v0.1.md` (provisional v0.1 figures) and `docs/analyses/analysis-v0.2.md` (corrected people-per-cell rerun).

### The 0.0 min artifact

Population comes from 1 km WorldPop cells whose population is modeled at the cell centroid. A cell whose centroid falls on a hospital node gets a travel time of 0.0 minutes. The run reports how many people this affects. It is a data resolution artifact, not a bug. The v0.2 density-to-count correction changes the reported number of people (they are now true counts) but not the artifact itself.

### OSM hospital curation

`amenity=hospital` in OSM includes clinics, diagnostic centers, dental practices, and occasionally unrelated facilities. The loader in `backend/gis/hospitals.py` keeps polygon mapped facilities and filters point facilities through an editable blocklist. Review and tune the blocklist per city before interpreting results.

---

## Threshold selection

| Lever | Effect | Mysuru example |
|---|---|---|
| Lower the threshold | Stricter access standard | 5min threshold: Guwahati 85.4% baseline, best candidate adds +20,657 modeled people (Mysuru stays saturated at 5min with all 85 facilities) |
| Restrict the facility class | Only major hospitals count as served | Mysuru with the 19 polygon mapped hospitals: 5min baseline drops from 100.0% to 93.5%, best candidate adds +24,140 modeled people |
| Model capacity | Crowded hospitals do not serve their full catchment | Requires bed/patient load data, not yet implemented |

Always run the baseline first and check coverage at the chosen threshold. If coverage is near 100%, lower the threshold or restrict the facility class before scoring candidates.

---

## Current limitations

- **Population data:** Population is the WorldPop 1 km density product converted to people per cell via density x geodesic cell area. This corrects the v0.1 density-as-count error, but the underlying estimates remain modeled (not census counts) and 1 km cells locate population at cell centroids. WorldPop country pages document the products' own accuracy limits.
- **Population layer (v0.1 results):** All population figures in `docs/analyses/analysis-v0.1.md` predate the density-to-count correction and are provisional.
- **Hospital data:** OSM completeness varies by city. Retained facility counts should not be treated as a census of hospitals. Capacity, specialty, and quality are not modeled.
- **Road network:** Travel times reflect the modeled network only. Real time traffic, congestion, road closures, and emergency routing are not represented.
- **Candidate locations:** High scoring candidates are analysis locations. No parcel level feasibility check is performed.
- **Optimization scope:** Candidates are ranked by individual marginal coverage. Joint selection of multiple sites is not yet implemented (planned for v0.3, see the [Roadmap](#roadmap-v03-in-development)).

---

## Version history and documents

### v0.1 — initial prototype

First end-to-end implementation (data ingestion, candidate generation, hard constraints, drive-time coverage scoring, ranking and reporting), exercised across four Indian cities: Mysuru, Kolkata, Patna, Guwahati.

Key findings (their structure is unchanged in v0.2):

- A 15minute threshold is saturated in every city; a 5minute threshold exposes measurable gains in three of four (Mysuru only once the facility class is restricted to polygon-mapped hospitals).
- Results are stable across candidate grid spacings (Patna, Guwahati).
- Saturation is an informative result, not a failure — the pipeline reports it explicitly.

Limitations specific to v0.1: the population layer stored WorldPop density values (people/km²) as if they were counts, so **every absolute v0.1 population figure is provisional** (see the data note in `docs/analyses/analysis-v0.1.md`). The Mysuru "94.7% @ 5 min" headline corresponds to the restricted facility class (~19 polygon-mapped hospitals), not the full ~85-facility network; the reconciliation is in `docs/analyses/analysis-v0.2.md`. Capability gaps listed under "What v0.2 does not do" above all originate in v0.1 and are carried forward.

Full write-up: `docs/analyses/analysis-v0.1.md`.

### v0.2 — corrected population layer (current)

- Density-to-count conversion (`count = density × geodesic cell area`), country-agnostic WorldPop sourcing, per-city Parquet population caches (legacy v0.1 CSV caches are refused with instructions).
- Self-healing `meta.json`, one-command bootstrap with `--probe` / `--no-download`, slim road graphs, cleanup tool.
- All four v0.1 cities rerun on corrected weights, plus Kampala validated as the first non-India city (population within ~1% of the official figure).

Full write-up: `docs/analyses/analysis-v0.2.md` (includes the mechanical v0.1 → v0.2 reconciliation). Implementation log: `docs/logs/v0.2-log.md`.

## Roadmap: v0.3 (in development)

Planned next, on top of the stable v0.2 base:

1. **Joint multi-facility selection (MCLP)** — select a complementary set of k sites instead of a single best site: lazy greedy with the >= (1 - 1/e) approximation guarantee, benchmarked against an exact MILP solver (PuLP/CBC) on ~300-candidate instances.
2. **Accessibility add-ons** — an enhanced 2SFCA accessibility index (per-cell scores beyond the binary threshold metric, Gaussian decay over drive time) and uncertainty reporting (intervals on headline figures).
3. **API** — a thin FastAPI wrapper over the pipeline's result dict, exposing joint selection and accessibility scores.
4. **Interactive demo** — a Leaflet map consuming the API for public before/after comparisons, prioritizing Global-South benchmark cities (supersedes the earlier Streamlit idea).
5. **Harder data fetching** — Overpass mirror failover with fast-fail timeouts and combined forbidden-zone queries, for unstable-network environments.

v0.3 is work in progress: none of the above is part of the committed code yet, and no figure or command in this README reflects it.

---

## Repository layout

```
backend/
  config.py      CityConfig: per city settings, auto UTM CRS, data paths
  data/          download + population crop, density-to-count conversion & Parquet cache
  gis/           boundary + hospital loading with OSM curation
  routing/       road graph loading (slim Parquet format)
  candidates/    candidate site generation
  constraints/   hard filters (existing facilities, roads, forbidden zones)
  objectives/    scoring (coverage.py) + accessibility measurement (baseline.py)
  optimizers/    ranking + result export
  pipeline.py    end to end orchestration
  main.py        CLI entry point

data/
  raw/<city>/    boundary.gpkg, roads_nodes.parquet + roads_edges.parquet (slim graph;
                 legacy cities may still carry roads.graphml), hospitals.gpkg,
                 forbidden.gpkg, population.parquet, meta.json
  raw/worldpop/<ISO3>/  shared national 1 km density raster (deletable once cached)
  processed/<city>/top_sites.gpkg

(prepare.py in backend/data/ is the bootstrap orchestrator behind the one-command flow.)

docs/
  analyses/analysis-v0.1.md    Experimental findings across Mysuru, Kolkata, Patna, Guwahati
  analyses/analysis-v0.2.md    Corrected-population rerun of the same four cities (people per cell)
  logs/v0.2-log.md             v0.2 implementation log (what was built, file by file)
```
