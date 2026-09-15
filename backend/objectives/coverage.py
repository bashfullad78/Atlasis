"""Candidate scoring: population that a site would *newly* bring within the
drive-time threshold, given facilities that already exist (marginal coverage,
so already-served areas don't win)."""
import geopandas as gpd
import networkx as nx
import osmnx as ox

TRAVEL_TIME_THRESHOLD = 15 * 60  # seconds
DEFAULT_SPEED_KMH = 40           # when OSM has no maxspeed tag


def add_travel_times(graph):
    """Add a ``travel_time`` (seconds) attribute to every edge. Idempotent."""
    for _u, _v, data in graph.edges(data=True):
        length = data.get("length", 50)
        speed = data.get("maxspeed", DEFAULT_SPEED_KMH)
        if isinstance(speed, (list, tuple)):
            speed = speed[0]
        if isinstance(speed, str):
            try:
                speed = float(speed.split()[0])  # "50", "50 mph", "30; 50"
            except (ValueError, IndexError):
                speed = DEFAULT_SPEED_KMH
        if not speed:
            speed = DEFAULT_SPEED_KMH
        data["travel_time"] = length / (speed * 1000 / 3600)
    return graph


def nearest_nodes_unique(geometries, graph):
    """Snap geometries to their nearest graph node, deduplicated."""
    seen, nodes = set(), []
    for geom in geometries:
        node = ox.distance.nearest_nodes(graph, geom.x, geom.y)
        if node not in seen:
            seen.add(node)
            nodes.append(node)
    return nodes


def population_by_node(pop_gdf, graph):
    """Aggregate population by nearest graph node.

    Snapping is vectorized (one KD-tree query for all cells): the 100 m
    count raster yields ~10^4-10^5 cells per city, too many for per-row
    queries.
    """
    nodes = ox.distance.nearest_nodes(
        graph, pop_gdf.geometry.x.values, pop_gdf.geometry.y.values
    )
    pops = pop_gdf["population"].astype(float).values
    node_pop = {}
    for node, pop in zip(nodes, pops):
        node_pop[node] = node_pop.get(node, 0.0) + pop
    return node_pop


def _reachable_from(graph_rev, source_node, threshold):
    """Nodes from which ``source_node`` is reachable within ``threshold`` seconds.

    Runs on the reversed graph so paths model person -> facility travel.
    """
    return nx.single_source_dijkstra_path_length(
        graph_rev, source_node, cutoff=threshold, weight="travel_time"
    )


def score_all_candidates_population(candidates, pop_gdf, graph, hospitals=None,
                                    threshold=TRAVEL_TIME_THRESHOLD):
    """Score sites by NEW people within the threshold; adds score/score_total/score_type."""
    graph = add_travel_times(graph)
    graph_rev = graph.reverse(copy=True)

    if not isinstance(candidates, gpd.GeoDataFrame):
        candidates = gpd.GeoDataFrame(candidates, geometry="geometry", crs=graph.graph["crs"])

    node_pop = population_by_node(pop_gdf, graph_rev)

    already_covered = set()
    if hospitals is not None and len(hospitals):
        hospital_nodes = nearest_nodes_unique(hospitals.geometry, graph_rev)
        already_covered = set(
            nx.multi_source_dijkstra_path_length(
                graph_rev, hospital_nodes, cutoff=threshold, weight="travel_time"
            )
        )

    # Snap each candidate to its nearest node WITHOUT dedup: two grid sites can
    # share a node, and each candidate must keep its own score. Vectorized:
    # one KD-tree query for the whole grid.
    candidate_nodes = ox.distance.nearest_nodes(
        graph_rev, candidates.geometry.x.values, candidates.geometry.y.values
    )
    print(f"Scoring {len(candidates)} candidates by marginal {threshold / 60:.0f}-min drive coverage...")

    scores, totals = [], []
    for node in candidate_nodes:
        reachable = _reachable_from(graph_rev, node, threshold)
        total = sum(p for n, p in node_pop.items() if n in reachable)
        new = sum(p for n, p in node_pop.items() if n in reachable and n not in already_covered)
        totals.append(total)
        scores.append(new)

    scored = candidates.copy()
    scored["score"] = scores
    scored["score_total"] = totals
    scored["score_type"] = (
        f"population_new_within_{threshold // 60}min"
        if threshold % 60 == 0
        else f"population_new_within_{threshold}s"
    )
    return scored
