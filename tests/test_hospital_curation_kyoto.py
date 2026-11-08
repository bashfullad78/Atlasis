"""Kyoto test: curation is language-independent by construction, basically a sanity test.

Names in this file are built from
Unicode escapes at runtime, and a source scan asserts
``backend/gis/hospitals.py`` stays ASCII only. Language knowledge lives in
the per-city exclude lists (user maintained verdicts), never in business logic.
"""
import tokenize

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from backend.gis.hospitals import _curate, _has_structural_evidence

# Japanese for "hospital" and "clinic", built from Unicode escapes so this

JP_HOSPITAL = "".join(map(chr, (0x533B, 0x9662)))          # iryou in
JP_CLINIC = "".join(map(chr, (0x8A3A, 0x7642, 0x6240)))    # shinryou jo
JP_CLINIC_WITH_NUM = f"\u7b2c\u4e8c{JP_CLINIC}"            # "No. 2" + clinic


def _frame(rows, crs="EPSG:32632"):
    """rows: dicts with name + optional healthcare/beds/building tag values."""
    defaults = {"healthcare": None, "beds": None, "building": None}
    recs = [{**defaults, **r} for r in rows]
    return gpd.GeoDataFrame(
        recs, geometry=[Point(0.0, float(i)) for i in range(len(recs))], crs=crs)



# static guarantee: no non English name hardcoded


def test_curation_module_is_ascii_only():
    """No hardcoded non-English name can exist in the logic (Kyoto finding).

    Comments and docstrings included: any non-English name knowledge in the
    module would make the curation city/language-specific by construction.
    """
    path = "backend/gis/hospitals.py"
    with open(path, "rb") as f:
        raw = f.read()
    try:
        raw.decode("ascii")
    except UnicodeDecodeError as e:
        pytest.fail(
            f"{path} must be ASCII-only; move any non-English name to a "
            f"per-city exclude list or a test built from unicode escapes: {e}")


def test_blocklist_and_excludes_are_english_substrings():
    """Heuristic lists must be lowercase ASCII substrings (match semantics)."""
    from backend.gis.hospitals import HOSPITAL_EXCLUDE_NAMES, HOSPITAL_NAME_BLOCKLIST

    for lst, label in ((HOSPITAL_NAME_BLOCKLIST, "blocklist"),
                       (HOSPITAL_EXCLUDE_NAMES, "exclude")):
        for entry in lst:
            assert isinstance(entry, str), f"{label} entries must be strings"
            assert entry == entry.lower() and entry.isascii(), \
                f"{label} entries must be lowercase ASCII substrings: {entry!r}"



# dynamic guarantee: evidence beats names in any language


def test_japanese_hospital_point_with_beds_is_kept():
    """A mapped bed count is deliberate, checkable, language-free evidence.

    Japanese names cannot match the English blocklist, so the "no evidence"
    rows are kept too -- the kept set is reviewable, not censored by an inert
    blocklist (that is the documented Kyoto semantics).
    """
    gdf = _frame([
        {"name": JP_HOSPITAL, "beds": 700},
        {"name": JP_CLINIC_WITH_NUM, "beds": 0},   # explicit negative
        {"name": JP_HOSPITAL + JP_CLINIC},          # no evidence, mixed name
    ])
    kept = _curate(gdf)
    assert kept["name"].tolist() == [
        JP_HOSPITAL, JP_CLINIC_WITH_NUM, JP_HOSPITAL + JP_CLINIC]


def test_japanese_clinic_rescue_aligns_with_evidence_not_name():
    """When a Japanese-named facility maps beds, the rescue is available.

    The rescue mask must track evidence (beds / polygon), never the name --
    contrast the English "City Dental Clinic" row, which has no evidence and
    no rescue, and is dropped.
    """
    gdf = _frame([
        {"name": JP_CLINIC, "beds": 12},
        {"name": "City Dental Clinic"},      # English hit, no evidence
        {"name": JP_HOSPITAL, "beds": 5},
    ])
    rescue, review = _has_structural_evidence(
        gdf, gdf.geometry.apply(lambda g: g.geom_type in ("Polygon", "MultiPolygon")))
    assert review == ["healthcare", "beds", "building"]
    assert rescue.tolist() == [True, False, True]

    kept = _curate(gdf)
    assert kept["name"].tolist() == [JP_CLINIC, JP_HOSPITAL]


def test_japanese_polygon_footprint_is_kept():
    """Polygon footprints were the v0.1 language-free channel; still open."""
    gdf = _frame([
        {"name": JP_HOSPITAL},
        {"name": "City Dental Clinic"},
    ])
    gdf.loc[0, "geometry"] = box(0.0, 0.0, 30.0, 30.0)
    kept = _curate(gdf)
    assert len(kept) == 1
    assert kept["name"].iloc[0] == JP_HOSPITAL


def test_kyoto_like_city_needs_no_per_city_tuning():
    """End-to-end Kyoto scenario: mixed Japanese points + polygons.

    A fresh Kyoto cache (Japanese names, mixed evidence) must curate
    identically to the same cache transliterated into *non-blocklisted*
    English names: the verdict depends only on evidence, never on language.
    (Names that DO match the English blocklist are the one asymmetric case --
    asserted separately below.)
    """
    def rows(lang):
        hosp, clinic = (JP_HOSPITAL, JP_CLINIC) if lang == "jp" \
            else ("General Hospital", "Municipal Infirmary")
        return [
            {"name": hosp, "beds": 400},            # evidence: rescue
            {"name": clinic},                        # inert in both languages
            {"name": f"{hosp} Annex", "beds": 0},    # explicit negative
        ]

    jp = _curate(_frame(rows("jp")))
    en = _curate(_frame(rows("en")))
    assert len(jp) == len(en) == 3
    assert jp["name"].tolist() == [JP_HOSPITAL, JP_CLINIC,
                                   JP_HOSPITAL + " Annex"]
    assert en["name"].tolist() == ["General Hospital", "Municipal Infirmary",
                                   "General Hospital Annex"]


def test_english_blocklist_reach_is_the_only_asymmetry():
    """The sole language asymmetry: names the English blocklist can match.

    Same evidence pattern as the Kyoto scenario, but with English names that
    trip the blocklist: those points drop unless evidence rescues them. This
    is the documented behavior (blocklist = a heuristic over English
    substrings), not city-specific logic.
    """
    gdf = _frame([
        {"name": "Second Clinic", "beds": 0},        # fires, no rescue -> drop
        {"name": "Dental Clinic", "beds": 5},        # fires, beds rescue
        {"name": "City Pharmacy"},                   # fires, no rescue -> drop
    ])
    kept = _curate(gdf)
    assert kept["name"].tolist() == ["Dental Clinic"]


def test_duplicate_dedup_is_language_blind():
    """Same-name dedup works on any script; different names never collapse."""
    gdf = _frame([
        {"name": JP_HOSPITAL, "beds": 1},
        {"name": JP_HOSPITAL, "beds": 1},
        {"name": JP_CLINIC, "beds": 1},   # different name, same node area
    ])
    gdf.loc[1, "geometry"] = Point(50.0, 0.0)  # ~50 m away
    kept = _curate(gdf)
    assert kept["name"].tolist() == [JP_HOSPITAL, JP_CLINIC]



# existing caches keep documented counts (85 / 32 / 9 / 27)
# krakow 26 -> 27: fresh 2026-10-28 fetch gained one facility vs the
# pre-wipe cache (OSM is live; curation semantics unchanged — dedup still
# drops 1 of 28 raw features).


CITY_EXPECTED = {"mysuru": 85, "heidelberg": 32, "freiburg": 9, "krakow": 27}


@pytest.mark.parametrize("slug,expected", sorted(CITY_EXPECTED.items()))
def test_real_cache_counts_unchanged(slug, expected):
    """Documented curated counts survive the hardened semantics exactly.

    Skips where the gitignored data/raw cache is absent:
    the pin is a war -cache regression guard.
    """
    from pathlib import Path

    from backend.config import city_config
    from backend.gis.hospitals import load_hospitals

    cache = Path("data/raw") / slug / "hospitals.gpkg"
    if not cache.exists():
        pytest.skip(f"real cache absent: {cache} (fresh checkout / CI)")
    hospitals = load_hospitals(city_config(slug))
    assert len(hospitals) == expected, (
        f"{slug}: curated count drifted from the documented {expected}")
