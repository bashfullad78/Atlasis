import osmnx as ox
import os

from backend.config import CityConfig


def load_graph(cfg: CityConfig):
    """Drivable road graph, projected to the city's UTM zone."""
    if not os.path.exists(cfg.roads_path):
        raise FileNotFoundError("Road graph not found. Run download first.")
    return ox.project_graph(ox.load_graphml(cfg.roads_path), to_crs=cfg.crs)


if __name__ == "__main__":
    from backend.config import city_config

    G = load_graph(city_config())
    print(f"Nodes: {len(G.nodes)}, Edges: {len(G.edges)}")
