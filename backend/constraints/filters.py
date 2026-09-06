"""Hard constraints: distance from existing facilities and roads, forbidden zones."""
import geopandas as gpd
import osmnx as ox

from backend.config import CityConfig
from backend.constraints.forbidden import filter_forbidden


def filter_too_close_to_existing(candidates, hospitals, cfg: CityConfig):
    hospital_union = hospitals.union_all()
    mask = candidates.geometry.apply(
        lambda p: p.distance(hospital_union) >= cfg.min_dist_existing_m
    )
    filtered = candidates.loc[mask].copy()
    print(f"After proximity filter: {len(filtered)} / {len(candidates)} candidates remain")
    return filtered


def filter_far_from_roads(candidates, graph, cfg: CityConfig):
    nodes, _ = ox.graph_to_gdfs(graph)
    nodes = nodes.to_crs(candidates.crs)
    road_union = nodes.union_all()
    mask = candidates.geometry.apply(
        lambda p: p.distance(road_union) <= cfg.min_dist_road_m
    )
    filtered = candidates.loc[mask].copy()
    print(f"After road filter: {len(filtered)} / {len(candidates)} candidates remain")
    return filtered


def apply_all_filters(candidates, hospitals, graph, cfg: CityConfig):
    candidates = filter_too_close_to_existing(candidates, hospitals, cfg)
    candidates = filter_far_from_roads(candidates, graph, cfg)
    candidates = filter_forbidden(candidates, cfg)
    return gpd.GeoDataFrame(candidates.reset_index(drop=True), crs=candidates.crs)
