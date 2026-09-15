# ATLASIS v0.1 Analysis

## Purpose

This document records the current experimental findings from ATLASIS across selected Indian cities.

The analysis evaluates whether additional hospital locations can improve population coverage under road network travel time constraints. It also examines how sensitive the results are to the candidate grid resolution.

The results in this document are experimental and should not be interpreted as final estimates of real world hospital accessibility or population coverage.

---

## Important Data Note

ATLASIS currently uses WorldPop gridded population data as its population demand layer.

WorldPop provides modeled gridded population estimates rather than direct census observations. These datasets are designed to distribute population estimates spatially and can be aggregated over custom areas such as hospital catchments. WorldPop also notes that gridded estimates do not replace a full census.

There is also an important implementation issue in the current version of ATLASIS:

The current India population raster is a WorldPop **population-density** product. Its values represent people per square kilometre, while the ATLASIS pipeline currently treats each raster value as the population count of the corresponding cell. WorldPop distinguishes density products from population-count products, where count products represent the estimated number of people in each grid cell.

Therefore:

* The current population totals should be treated as **modeled raster values used by the experiment**, not as authoritative city populations.
* The current absolute population gain figures are provisional.
* The population layer should be corrected to use a population count raster before final results are reported.
* The optimization and road network methodology can remain largely unchanged after this correction because the population layer is consumed as spatial demand data.

> **v0.2 status:** this correction is now implemented. The population layer converts WorldPop density values to people per cell (density x geodesic cell area on the WGS84 grid) and is country agnostic (any geocodable city, per-country raster shared and cached per city as Parquet). All population figures in this document were produced by v0.1 and remain provisional; rerun the pipeline before quoting absolute numbers. The corrected rerun of these four cities is in `analysis-v0.2.md`; the mapping between the two documents' figures (scale factor per city, and why Mysuru's 94.7% reflects the polygon-restricted facility class) is documented there.

The findings below are therefore primarily relevant for evaluating the behavior of the optimization pipeline, threshold selection, candidate stability, and differences between cities.

---

# City Analysis

## 1. Mysuru

### Dataset and network

* Population cells: approximately 1 km resolution
* Existing hospitals after OSM curation: approximately 85
* Study boundary: approximately 86 km²
* Hospital density: approximately 1 facility/km²
* Primary analysis threshold: 15 minutes
* Stricter analysis threshold: 5 minutes

### 15minute analysis

The 15minute threshold was effectively saturated.

The existing hospital network covered 100% of the modeled population under the current model. Additional candidate sites therefore produced no marginal population gain.

This does not mean that healthcare access in Mysuru is universally adequate. It means that the definition used by ATLASIS, namely reaching an existing hospital within 15 minutes by the modeled road network, does not identify a meaningful additional coverage opportunity in this study area.

### 5minute analysis

The 5minute threshold produced a more discriminating result.

> **v0.2 note:** these two figures correspond to the **restricted facility
> class** (the ~20 polygon mapped hospitals), not the full 85-facility curated
> network, which is saturated even at 5 minutes. See `analysis-v0.2.md` for
> both configurations under corrected weights (93.5% / +24,140 restricted;
> 100.0% / +0 unrestricted).

* Baseline coverage: approximately 94.7%
* Best single candidate gain: approximately 28,771 modeled people
* The 5minute threshold therefore exposed an accessibility gap that was hidden by the 15minute threshold.

### Interpretation

Mysuru demonstrates that the selected travel time threshold has a large effect on the optimization objective.

A 15minute threshold is too permissive for this particular hospital network and study boundary. A 5minute threshold produces meaningful differentiation between candidate locations.

### Limitation

The result depends on the current OSM hospital inventory and the definition of which facilities are considered hospitals. It also uses a coarse population grid, which limits spatial precision.

---

## 2. Kolkata

### Dataset and network

* Hospitals retained after curation: 189
* Mistagged facilities removed: 21
* Duplicate facilities removed: 2
* Road network nodes: 41,512
* Road network edges: 105,547
* Final candidates after spatial constraints: 314
* Population cells: 250
* Modeled population represented by the current raster: approximately 7.64 million

The modeled population figure should not be interpreted as the official population of Kolkata. It is the sum of the population values represented inside the selected study boundary and current WorldPop raster.

### 15minute analysis

The 15minute threshold was fully saturated.

* Baseline coverage: 100.0%
* Modeled population covered: approximately 7.64 million
* Best candidate gain: 0
* Optimized coverage: 100.0%

The result indicates that the existing hospital network already covers the modeled population under the 15minute definition.

### 5minute analysis

The 5minute threshold produced a measurable optimization result.

* Baseline coverage: 98.9%
* Optimized coverage: 99.6%
* Improvement: approximately 0.7 percentage points
* Modeled population gained: approximately 53,852
* Maximum modeled travel time: reduced from approximately 10.8 to 7.5 minutes

The median and mean travel times changed only slightly. The main improvement therefore comes from reducing the uncovered population rather than changing travel times for the already covered majority.

### Interpretation

Kolkata shows the same threshold effect observed in Mysuru, but the 5minute result is more pronounced.

The 15minute objective provides almost no differentiation because the existing hospital network already covers the modeled population. A stricter emergency access threshold produces a measurable placement problem.

### Limitation

The current modeled population total is sensitive to the study boundary, raster year, raster resolution, and population data methodology. It should not be compared directly with a published Kolkata population figure without matching those definitions.

---

## 3. Patna

### Dataset and network

* Hospitals retained after curation: 173
* Mistagged facilities removed: 8
* Duplicate facilities removed: 1
* Road network nodes: 23,318
* Road network edges: 56,253
* Population cells: 181
* Current modeled population: approximately 2.70 million

Approximately 11,812 modeled people had no usable road network path to any hospital and were excluded from the travel time calculations above.

### 15 minute analysis

The 15 minute threshold was almost saturated.

* Baseline coverage: approximately 99.6%
* Modeled population covered: approximately 2.684 million
* Best candidate gain: 0
* Optimized coverage: unchanged

This indicates that the 15 minute objective is not sufficiently discriminating for the current Patna study area.

### 5 minute analysis

The 5 minute threshold produced a measurable improvement.

* Baseline coverage: approximately 98.6%
* Optimized coverage: approximately 99.4%
* Improvement: approximately 0.8 percentage points
* Modeled population gained: approximately 22,071
* Maximum modeled travel time: reduced from approximately 7.9 to 5.8 minutes

### Grid resolution sensitivity

Patna was tested with candidate spacings of:

* 250 m
* 500 m
* 1,000 m
* 2,000 m

The same approximate population gain was obtained at all four resolutions for the best 5 minute candidate.

The high scoring region also remained geographically consistent across the tested resolutions.

This indicates that the result is not an artifact of one particular candidate grid spacing.

### Interpretation

Patna provides a grid sensitivity test for the candidate search.

The 15 minute objective is saturated, while the 5 minute objective produces a measurable improvement. The result remains stable when the candidate grid resolution changes substantially.

### Limitation

The grid sensitivity test evaluates stability of the candidate search, not proof that the identified location is globally optimal. Candidate sites are still generated from a discrete grid and are subject to the current spatial constraints.

---

## 4. Guwahati

### Dataset and network

* Hospitals retained after curation: 24
* Duplicate facilities removed: 2
* Mistagged facilities removed: 0
* Population cells: 99
* Current modeled population: approximately 631,495
* Final candidates: 185 at 500 m spacing

### 15minute analysis

The 15minute threshold was saturated.

* Baseline coverage: 100.0%
* Best candidate gain: 0
* Optimized coverage: 100.0%

The median travel time was approximately 2.1 minutes, with a maximum of approximately 11.5 minutes.

### 5minute analysis

Guwahati produced the largest relative improvement among the current city experiments.

* Baseline coverage: approximately 85.4%
* Optimized coverage: approximately 89.6%
* Improvement: approximately 4.2 percentage points
* Modeled population gained: approximately 26,795
* Median travel time: approximately 2.1 to 2.0 minutes
* Mean travel time: approximately 2.7 to 2.5 minutes
* Maximum travel time: remained approximately 11.5 minutes

The improvement corresponds to approximately 4.2% of the modeled population.

### Gridresolution sensitivity

Guwahati was tested at:

* 250 m
* 500 m
* 1,000 m

The same best 5minute population gain was obtained at all three resolutions.

The highest scoring area also remained geographically consistent across the tested resolutions.

At 10 minutes, only a small additional improvement remained available. At 15 and 30 minutes, the objective was already saturated.

### Interpretation

Guwahati illustrates the effect of travel time threshold selection.

The 15 minute definition produces complete modeled coverage, while the 5 minute definition leaves a substantial accessibility gap. The result also remains stable across candidate grid resolutions.

### Limitation

Only 24 hospital facilities were retained in the current OSM based inventory. This makes facility classification and OSM completeness particularly important when interpreting the result.

---

# Cross City Findings

## 1. Travel time threshold strongly affects the result

Across the four cities, a 15minute threshold generally produced either complete or almost complete modeled coverage.

| City     | 15minute result |         5minute result |
| -------- | ---------------: | ----------------------: |
| Mysuru   |  100.0%, no gain | 94.7% baseline, +28,771 |
| Kolkata  |  100.0%, no gain | 98.9% baseline, +53,852 |
| Patna    |   99.6%, no gain | 98.6% baseline, +22,071 |
| Guwahati |  100.0%, no gain | 85.4% baseline, +26,795 |

A single generous travel time threshold is not sufficient for distinguishing areas with potential accessibility gaps. The 5minute threshold produces differentiation across all four cities in the current experiments.

---

## 2. Saturation is an informative result

A zero gain result does not necessarily indicate a failure of the optimization.

When every modeled population cell is already within the selected travel time threshold of an existing hospital, there is no marginal population for a new facility to cover under that objective.

ATLASIS should therefore report saturation explicitly rather than presenting a forced candidate recommendation.

---

## 3. Candidate grid resolution appears reasonably stable

Patna and Guwahati were tested across multiple candidate grid spacings.

The high scoring regions remained broadly consistent, and the best population gains were unchanged across the tested resolutions.

This provides some evidence that the observed results are not caused solely by the exact placement of a single candidate grid.

It does not establish global optimality.

---

## 4. The population layer is the main current data limitation

The current population layer is modeled rather than directly observed.

There are two separate considerations:

1. **Model uncertainty:** WorldPop estimates population distributions using statistical and geospatial modeling. These estimates are relevant for spatial analysis but are not equivalent to a complete census.
2. **Current implementation:** ATLASIS currently uses a population density raster while treating its values as population counts. This must be corrected before final population totals and gains are reported as population counts.

The second issue is an implementation problem, while the first is an inherent limitation of using modeled population data.

---

# Current Limitations

## Population data

* The current population raster should be replaced with a population count product.
* Current absolute population totals and population gain figures are provisional.
* The raster has approximately 1 km spatial resolution, so population is represented at a relatively coarse spatial scale.
* Raster cell centroids are used to represent population demand locations.
* A cell whose centroid falls within an existing hospital's catchment can therefore be treated as already served even though the actual population is distributed throughout the cell.

## Hospital data

Hospital locations are obtained from OpenStreetMap and curated using the project's facility filters.

OSM completeness and tagging quality can vary between cities. The number of retained facilities therefore should not be interpreted as a complete census of hospitals.

The current model also treats retained facilities primarily as locations. It does not yet model:

* Hospital capacity
* Bed availability
* Emergency department capacity
* Service quality
* Waiting times
* Congestion
* Specialty availability
* Referral patterns

A hospital with limited capacity is currently treated as capable of serving its modeled catchment in the same way as a larger facility.

## Road network

Travel times are calculated using the available road network and the current travel time assumptions.

The model does not currently represent:

* Real time traffic
* Time of day congestion
* Road closures
* Temporary construction
* Weather effects
* Emergency vehicle specific routing

The results should therefore be interpreted as modeled network accessibility rather than observed ambulance travel times.

## Candidate locations

Candidate sites are generated from a spatial grid and filtered using the current constraints.

The system does not yet perform parcel level feasibility checks such as:

* Land ownership
* Zoning
* Building availability
* Land cost
* Existing structures
* Environmental restrictions beyond the configured spatial filters

A high scoring candidate therefore represents a candidate analysis location, not a confirmed construction site.

## Optimization scope

The current optimization ranks individual candidate locations by marginal population coverage.

It does not yet solve a full multi facility allocation problem where several new hospitals are selected jointly while accounting for overlap between their catchments.

The current ranking should therefore be interpreted as a single site recommendation system rather than a complete hospital network design tool.

---

# Overall Assessment

The current experiments demonstrate that ATLASIS can integrate hospital locations with a road network, generate spatially constrained candidate locations, calculate network based travel times, measure marginal population coverage, identify when an objective is already saturated, and compare accessibility under different travel time thresholds.

The consistent finding across all four cities is that a 5minute accessibility threshold produces differentiation where a 15minute threshold does not. Patna and Guwahati also show that the 5minute results remain stable across multiple candidate grid resolutions.

The next methodological priority is to correct the population demand layer by using a population count raster rather than interpreting population density values as cell counts. After that correction, the city experiments should be rerun before the population figures are used as final quantitative results.

The current version should be considered a functional experimental prototype rather than a validated healthcare planning model.