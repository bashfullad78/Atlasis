"""Tests for hospital curation: structural (language-independent) signals must
override name heuristics; explicit excludes stay absolute; the printout must
be honest when a cache carries no reviewable tags. All synthetic, offline."""
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from backend.gis.hospitals import _curate, _has_structural_evidence


def _frame(rows):
    """rows: dicts with name + optional healthcare/beds/building tag values."""
    defaults = {"healthcare": None, "beds": None, "building": None}
    recs = [{**defaults, **r} for r in rows]
    return gpd.GeoDataFrame(
        recs, geometry=[Point(0.0, float(i)) for i in range(len(recs))],
        crs="EPSG:32632")


def test_beds_override_any_language_name():
    """A mapped bed count is deliberate, checkable evidence — language-free."""
    gdf = _frame([
        {"name": "Praxisklinik 2000", "beds": 120},
    ])
    kept = _curate(gdf)
    assert len(kept) == 1


def test_name_blocked_point_without_evidence_still_drops():
    gdf = _frame([
        {"name": "City Dental Clinic"},          # blocklisted, no evidence
        {"name": "Real Hospital", "beds": 50},   # control
    ])
    kept = _curate(gdf)
    assert kept["name"].tolist() == ["Real Hospital"]


def test_explicit_exclude_beats_physical_evidence():
    """A manual per-city verdict is absolute, even for a bed-mapped facility."""
    gdf = _frame([
        {"name": "RheumaCare", "beds": 30},
    ])
    kept = _curate(gdf)
    assert len(kept) == 0


def test_generic_healthcare_tag_does_not_rescue_documented_curation():
    """Regression (Mysuru): healthcare=hospital is applied broadly by mappers
    (also on dental/day-care clinics), so it must NOT override the blocklist —
    otherwise documented v0.1 curated counts change silently."""
    gdf = _frame([
        {"name": "Srinivasa Polyclinic & Day Care", "healthcare": "hospital"},
        {"name": "Balaji Elite Dental", "healthcare": "hospital"},
    ])
    kept = _curate(gdf)
    assert len(kept) == 0


def test_unnamed_point_is_kept_regardless_of_building_tag():
    """Name heuristics cannot fire on empty names; generic building=yes is
    not inpatient evidence, but there is no name claim to contradict."""
    gdf = _frame([
        {"name": None, "building": "hospital"},
    ])
    kept = _curate(gdf)
    assert len(kept) == 1


def test_beds_zero_is_not_evidence():
    """beds=0 is an explicit negative; only beds>0 rescues."""
    gdf = _frame([
        {"name": "A Clinic", "beds": 0},
        {"name": "B Clinic", "beds": 5},
    ])
    kept = _curate(gdf)
    assert kept["name"].tolist() == ["B Clinic"]


def test_evidence_mask_reports_available_columns():
    gdf = _frame([{"name": "x", "beds": 12}])
    rescue, review = _has_structural_evidence(
        gdf, gdf.geometry.apply(lambda g: g.geom_type in ("Polygon", "MultiPolygon")))
    assert review == ["healthcare", "beds", "building"]
    assert rescue.tolist() == [True]        # beds > 0
    # healthcare/building alone do not rescue a point
    tagged = _frame([{"name": "x", "healthcare": "hospital", "building": "yes"}])
    rescue, review = _has_structural_evidence(
        tagged, pd.Series([False], index=tagged.index))
    assert rescue.tolist() == [False]
    assert review == ["healthcare", "beds", "building"]
    # a frame without the columns reports none available
    bare = gdf[["name", "geometry"]]
    rescue, review = _has_structural_evidence(
        bare, pd.Series([False], index=bare.index))
    assert review == []
    assert rescue.tolist() == [False]


def test_cache_without_structural_columns_keeps_legacy_behaviour(capsys):
    """Old-style cache (name+geometry only): nothing new drops, warning prints."""
    gdf = _frame([
        {"name": "Stadt Klinik"},       # not in the English blocklist -> kept
        {"name": "City Clinic"},        # English hit, no evidence -> dropped
    ])
    bare = gdf[["name", "geometry"]]
    kept = _curate(bare)
    assert kept["name"].tolist() == ["Stadt Klinik"]
    out = capsys.readouterr().out
    assert "no beds/structural tags" in out


def test_english_blocklist_still_drops_unstructured_points(capsys):
    """v0.1 Indian-city behavior is unchanged when no structural tags exist."""
    gdf = _frame([
        {"name": "Apollo Pharmacy"},     # blocklisted
        {"name": "City General Hospital"},
    ])
    bare = gdf[["name", "geometry"]]
    kept = _curate(bare)
    assert kept["name"].tolist() == ["City General Hospital"]
    assert "dropped 1 mis-tagged" in capsys.readouterr().out


def test_near_duplicate_dedup_still_applies():
    gdf = _frame([
        {"name": "Same Name", "beds": 10},
        {"name": "Same Name", "beds": 10},   # < 200 m away (same point family)
    ])
    gdf.loc[1, "geometry"] = Point(50.0, 0.0)  # ~50 m apart at EPSG:32632 scale
    kept = _curate(gdf)
    assert len(kept) == 1
