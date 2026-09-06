# ATLASIS

> **v0.1 — experimental prototype**
> Results in this version should not be used for real world planning decisions.
> See [Current Limitations](#current-limitations) and the full analysis in `docs/analysis-v0.1.md`.

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
- A corrected population count raster: the current pipeline uses a WorldPop population density product and treats density values as cell counts. This means all absolute population figures in v0.1 are provisional. See the data note in `docs/analysis-v0.1.md`.

---

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Downloading data

Run once per city before the first optimization.

```bash
python -m backend.data.download --city mysuru          # boundary, roads, hospitals
python -m backend.constraints.forbidden --city mysuru  # water / parks / cemeteries
python -m backend.data.population --city mysuru        # build population cache
```

Data lands in `data/raw/<city>/`. A `meta.json` there records the city's UTM zone, computed automatically from its location. Mysuru data is already cached in the repo.

The whole India WorldPop 1 km raster is downloaded once into `data/raw/india/ind_pop_1km.tif` (~18 MB), shared across all cities, then cropped to the city boundary and cached as a small CSV (`data/raw/<city>/population.csv`). The raster can be deleted once all target cities have caches.

## Running the optimization

```bash
python -m backend.main --city mysuru
python -m backend.main --city bengaluru --place "Bengaluru, Karnataka, India"
python -m backend.main --city mysuru --threshold 300   # 5minute threshold
```

Flags:

| Flag | Default | Description |
|---|---|---|
| `--city` | required | Built in slugs: mysuru, bengaluru, hyderabad, pune, chennai, kolkata |
| `--place` | none | Any geocodable name, for cities not in the built in registry |
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

Findings across four cities (Mysuru, Kolkata, Patna, Guwahati) showed that a 15minute threshold was saturated in every case, while a 5minute threshold produced measurable gains in all four. Full results are in `docs/analysis-v0.1.md`.

### The 0.0 min artifact

Population comes from 1 km WorldPop cells whose population is modeled at the cell centroid. A cell whose centroid falls on a hospital node gets a travel time of 0.0 minutes. The run reports how many people this affects. It is a data resolution artifact, not a bug.

### OSM hospital curation

`amenity=hospital` in OSM includes clinics, diagnostic centers, dental practices, and occasionally unrelated facilities. The loader in `backend/gis/hospitals.py` keeps polygon mapped facilities and filters point facilities through an editable blocklist. Review and tune the blocklist per city before interpreting results.

---

## Threshold selection

| Lever | Effect | Mysuru example |
|---|---|---|
| Lower the threshold | Stricter access standard | 5min threshold: 94.7% baseline coverage, best candidate adds +28,771 modeled people |
| Restrict the facility class | Only major hospitals count as served | 20 polygon mapped hospitals vs. all 85 retained |
| Model capacity | Crowded hospitals do not serve their full catchment | Requires bed/patient load data, not yet implemented |

Always run the baseline first and check coverage at the chosen threshold. If coverage is near 100%, lower the threshold or restrict the facility class before scoring candidates.

---

## Current limitations

- **Population data:** The current raster is a population density product treated as population counts. All absolute figures are provisional until replaced with a population count raster.
- **Hospital data:** OSM completeness varies by city. Retained facility counts should not be treated as a census of hospitals. Capacity, specialty, and quality are not modeled.
- **Road network:** Travel times reflect the modeled network only. Real time traffic, congestion, road closures, and emergency routing are not represented.
- **Candidate locations:** High scoring candidates are analysis locations. No parcel level feasibility check is performed.
- **Optimization scope:** Candidates are ranked by individual marginal coverage. Joint selection of multiple sites is not yet implemented.

---

## Repository layout

```
backend/
  config.py      CityConfig: per city settings, auto UTM CRS, data paths
  data/          download + population crop & cache
  gis/           boundary + hospital loading with OSM curation
  routing/       road graph loading
  candidates/    candidate site generation
  constraints/   hard filters (existing facilities, roads, forbidden zones)
  objectives/    scoring (coverage.py) + accessibility measurement (baseline.py)
  optimizers/    ranking + result export
  pipeline.py    end to end orchestration
  main.py        CLI entry point

data/
  raw/<city>/    boundary.gpkg, roads.graphml, hospitals.gpkg, forbidden.gpkg,
                 population.csv, meta.json
  raw/india/     shared whole India population raster
  processed/<city>/top_sites.gpkg

docs/
  analysis-v0.1.md    Experimental findings across Mysuru, Kolkata, Patna, Guwahati
```