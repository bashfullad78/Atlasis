# Drive-time external validation (v0.4)

First external check of the modeled drive times against a real router,
closing README limitation "#2 modeled times not yet validated".

## Method

- `scripts/validate_drive_times.py --city <slug> [--pairs N]`
- Samples N seeded OD pairs of road-graph nodes inside the city boundary,
  computes the modeled time (edge `length / posted maxspeed`, the exact edge
  model the coverage matrices use) and the same pair's car-route duration on
  the public OSRM demo router (`router.project-osrm.org`).
- 1 s courtesy pacing between calls. Full per-pair rows are kept in
  `docs/analyses/validate-<slug>.json`; run it anywhere the network allows.

## Freiburg result (50 pairs, 2026-10-22)

| metric | value |
| --- | --- |
| pairs validated | 50 |
| MAE | 175.1 s (~2.9 min, **24.5%** of OSRM time) |
| median abs err | 177.1 s |
| bias (OSRM − modeled) | +175.1 s, **every pair positive** (min +15.9 s, max +357.7 s) |

Read: **our model is systematically more optimistic** than OSRM's car
profile, by ~1–4.5 min on city-scale trips, never slower. Expected sources:
OSRM applies the car profile's speed modifiers and traffic-light penalties,
while we only use posted maxspeed (and a default when the tag is missing).
The effect compounds over long routes: model error grows with trip length.

## Kraków result (50 pairs, 2026-10-28)

| metric | value |
| --- | --- |
| pairs validated | 50 |
| MAE | 199.8 s (~3.3 min, **24.2%** of OSRM time) |
| median abs err | 186.6 s |
| bias (OSRM − modeled) | +199.8 s, **every pair positive** (min +24.5 s, max +383.7 s) |

Read: reproduces Freiburg almost exactly — same sign, same relative size
(24.2% vs 24.5%). Trip-length compounding confirmed: mean bias +60.0 s on
the 4 pairs OSRM routes in <5 min vs +243.1 s on the 34 pairs ≥10 min.
Second tag-rich European city, same one-directional optimism.

## Guwahati result (50 pairs, 2026-10-28)

| metric | value |
| --- | --- |
| pairs validated | 50 |
| MAE | 119.6 s (~2.0 min, 19.5% mean per-pair relative error) |
| median abs err | 98.5 s |
| bias (OSRM − modeled) | **+15.9 s only** — sign-mixed: 29 positive / **21 negative** (min −456.1 s, max +295.5 s) |
| median trip | OSRM 651.7 s vs modeled 759.3 s (median pair favors OSRM) |

Read: the European pattern does **not** transfer. Errors are wide but
bidirectional and cancel (+119.6 s mean |err| against +15.9 s net), so there
is no scale factor to apply; ≥10 min mean bias is +21.2 s (vs +243.1 s in
Kraków) — no trip-length compounding either. Expected cause: maxspeed
tagging. India's roads rarely carry `maxspeed`, so most edges fall back to
the flat `DEFAULT_SPEED_KMH = 40` while OSRM derives speeds from road class;
in tag-rich Germany/Poland we use real posted speeds but still skip OSRM's
intersection/urban penalties — one-directional optimism there,
class-vs-flat scatter here.

## Kampala result (50 pairs, 2026-10-28)

| metric | value |
| --- | --- |
| pairs validated | 50 |
| MAE | 85.1 s (~1.4 min, 12.5% mean per-pair relative error) |
| median abs err | 53.4 s (median relative 7.4% — most accurate city of the four) |
| bias (OSRM − modeled) | **−40.2 s** — sign-mixed, tilted negative: 18 positive / **32 negative** (min −377.9 s, max +136.7 s) |

Read: same tag-sparse profile as Guwahati (Uganda barely tags `maxspeed`,
edges fall back to flat 40), but the tilt flips **pessimistic** — the model
is slower than OSRM overall (median trip 849.6 s vs 776.5 s). And the sign
flips with trip length: +51.8 s mean on the 3 short pairs (<5 min,
residential — OSRM's class speed drops below our flat 40) vs −68.8 s on the
37 long pairs (≥10 min, arterials — OSRM's class speeds exceed 40). That
length-dependent sign flip is the flat-default-vs-class-speed mechanism
showing directly.

## Cross-city summary (2026-10-28)

| city | MAE (s) | rel | bias (s) | sign | profile |
| --- | --- | --- | --- | --- | --- |
| Freiburg | 175.1 | 24.5% | +175.1 | 50/0 | tag-rich, DE |
| Kraków | 199.8 | 24.2% | +199.8 | 50/0 | tag-rich, PL |
| Guwahati | 119.6 | 19.5% | +15.9 | 29/21 | tag-sparse, IN |
| Kampala | 85.1 | 12.5% | −40.2 | 18/32 | tag-sparse, UG |

## Consequence for thresholds

In tag-rich cities (Freiburg, Kraków): a 900 s (15 min) guarantee computed
by the model behaves like roughly a 15–19 min real-world guarantee —
cross-mode/policy interpretations should carry this systematic ~20–25% scale
factor. In tag-sparse cities (Guwahati, Kampala): no systematic offset exists to
correct — accuracy is actually better (median per-pair error 13.9% / 7.4%)
but the sign is unguaranteed: neutral-slightly-optimistic in Guwahati,
pessimistic in Kampala (long trips), with Guwahati tails running to −69%.
One scale factor cannot describe both, so per-pair guarantees there are
weaker in variance and in direction, not in average. Headline coverage percentages stay internally consistent either
way (every scenario shares the model). This is a standing calibration note,
not a bug in the optimization itself; per-OSM-tag speed profiles (e.g.,
urban penalty) are the obvious next lever if absolute calibration matters —
and missing-tag fallback (`DEFAULT_SPEED_KMH`) is the Guwahati lever.

## Tag coverage per city (2026-11-07)

The tag-rich/tag-sparse labels above are measured, not guessed: share of
road edges whose travel time comes from a mapped OSM `maxspeed` rather than
the flat `DEFAULT_SPEED_KMH = 40` fallback — the exact parse the coverage
matrices use.

| city | edges | mapped maxspeed | ... by length | OSRM bias (s) |
| --- | ---: | ---: | ---: | ---: |
| Freiburg | 8,706 | 81.5% | 79.8% | +175.1 |
| Heidelberg | 6,666 | 80.6% | 84.4% | -- |
| Kraków | 21,669 | 64.3% | 63.6% | +199.8 |
| Kampala | 40,513 | 5.1% | 5.6% | −40.2 |
| Guwahati | 12,219 | 2.9% | 3.1% | +15.9 |

Read: the split is an order of magnitude wide (64–82 % vs 2.9–5.1 %) and
aligns with the bias structure — the two tag-rich cities carry the large
one-sided optimism, the two tag-sparse cities a near-zero net bias whose
sign is unguaranteed. Where few edges carry `maxspeed`, the model is
essentially a constant-speed model (Guwahati: 97 % of edges at flat 40),
which is why no scale factor applies there. Heidelberg is unvalidated
(no OSRM run) but sits with the tag-rich group on coverage.

Reproduction (offline, cached graphs only):

```bash
venv/bin/python scripts/tag_coverage.py
```

## Cross-city uncertainty re-run (2026-11-08)

One protocol, three cached cities, all re-generated for the AGILE paper's
uncertainty table: `venv/bin/python -m backend.main --city <city>
--threshold 300 --num-sites 5 --mode both --uncertainty --n-boot 200
--seed 0` (Freb: `--no-download`), delete-1 jackknife over populated 1 km
clusters + seeded column and cell bootstraps (200 draws, seed 0).

| city | best-single gain | delete-1 | column boot | cell boot |
| --- | ---: | --- | --- | --- |
| Freiburg | 19,968 | [18,676, 19,968] | [16,148, 28,750] | [25,043, 40,904] |
| Heidelberg | 12,523 | [9,501, 12,523] | [7,072, 22,418] | [11,143, 28,083] |
| Kraków | 42,433 | [39,463, 42,433] | [33,735, 61,937] | [38,460, 63,881] |

(in people; gain = best single site's newly covered population at the
5-minute threshold)

Read: the deterministic check only ever moves a point estimate down by
its own cluster (Freiburg −6.5 %, Heidelberg −24 %, Kraków −7 %) — the
headline sign survives every delete-1. Column bootstraps widen
symmetrically; cell bootstraps skew high (resampling favors denser
cells). Kraków's fresh run (73.4 % → 91.8 %, gap 0.00 %) matches the
documented v0.3 numbers (73.5 % → 91.7 %) to 0.1 pp; its gain is
+42,433 vs the documented +40,751 because the re-fetched forbidden
cache (see below) trims the candidate pool slightly differently.
Jackknife SEs match the v0.3 run to the digit (22,684).

Kraków's forbidden zones were missing from the cache (Overpass had
failed during the Oct 28 window) and the combined query still trips a
413 on overpass-api.de, so the tag set was re-fetched as four disjoint
2×2 clips of the boundary polygon with per-chunk retry, writing the
identical `forbidden.gpkg`/meta format (3,867 features, 17.1 km² = 5 %
of the city). The fetch helper is `scripts/fetch_forbidden_chunked.py`
(a dev-box one-off next to `run_restricted_mysuru.py` — kept for the
next cache gap, not part of any pipeline run). Kumi/private.coffee
mirrors were unreachable from the dev box that window; overpass-api.de
served the chunk queries.

## Reproduction

```bash
venv/bin/python scripts/validate_drive_times.py --city freiburg --pairs 50
```

Rerun per city as road caches land; full per-pair rows are written to
`docs/analyses/validate-<slug>.json` (freiburg, krakow, guwahati, kampala
done — the latter three on 2026-10-28). `testville` is synthetic and
meaningless for this purpose (its streets are fake), so it is excluded.
