# ATLASIS

> **v0.1 — experimental prototype**
> Results in this version should not be used for real world planning decisions.
> See [Current Limitations](#current-limitations) and the full analyses in `docs/analyses/`.

ATLASIS recommends where to place new hospitals in a city so that the maximum number of people can reach one within a fixed drive time (default: 15 minutes). It runs entirely on free data: OpenStreetMap road networks via OSMnx and WorldPop population rasters.

---

## What v0.1 does

1. **Data ingestion** — city boundary, drivable road graph, existing hospitals (`amenity=hospital` from OSM), forbidden zones (water, parks, cemeteries), and a WorldPop population raster.
2. **Candidate generation** — a grid of possible sites inside the city boundary.
3. **Hard constraints** — drops sites too close to an existing hospital, too far from a road, or inside forbidden zones.
4. **Scoring** — each candidate is scored by the population it would *newly* bring within the travel time threshold of a hospital. Uses road network drive time, not straight line distance.
5. **Ranking and reporting** — reports the top candidates, then compares accessibility before and after adding the best site, including population weighted min/median/mean/max drive times.

## What v0.1 does not do

- Multi facility joint optimization (candidates are ranked independently, not selected as a set).
- Hospital capacity or bed availability (a small clinic counts the same as a large hospital).
- Real time or time of day traffic.
- Parcel level feasibility (land ownership, zoning, cost).

> **v0.2 population layer note:** the population layer is now country agnostic and converts WorldPop density values to people per cell (density x geodesic cell area). All v0.1 population figures predate this correction and remain provisional; see the data note in `docs/analyses/analysis-v0.1.md`.

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

Population comes from 1 km WorldPop cells whose population is modeled at the cell centroid. A cell whose centroid falls on a hospital node gets a travel time of 0.0 minutes. The run reports how many people this affects. It is a data resolution artifact, not a bug.

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
- **Optimization scope:** Candidates are ranked by individual marginal coverage. Joint selection of multiple sites is not yet implemented.

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
```