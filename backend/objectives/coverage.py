"""Candidate scoring: population that a site would *newly* bring within the
drive time threshold, given facilities that already exist (marginal coverage,
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
    """Snap geometries to their nearest graph node, deduplicated (order kept).

    One vectorized query: ox.distance.nearest_nodes rebuilds its KD-tree per
    call, so looping per geometry costs ~300 ms per point on a 100k-node
    graph vs ~0.2 ms vectorized.
    """
    if not isinstance(geometries, gpd.GeoSeries):
        geometries = gpd.GeoSeries(geometries)
    nodes = ox.distance.nearest_nodes(
        graph, geometries.x.values, geometries.y.values)
    return list(dict.fromkeys(nodes))


def population_by_node(pop_gdf, graph):
    """Aggregate population by nearest graph node (one vectorized snap)."""
    nodes = ox.distance.nearest_nodes(
        graph, pop_gdf.geometry.x.values, pop_gdf.geometry.y.values)
    node_pop = {}
    for node, pop in zip(nodes, pop_gdf["population"].astype(float)):
        node_pop[node] = node_pop.get(node, 0.0) + pop
    return node_pop


def reverse_with_travel_times(graph):
    """Reversed graph for person->facility queries; ``travel_time`` stamped.

    Shared by every objective stage so the per-run O(V+E) reverse copy and
    speed stamping happen once. ``copy=False`` shares edge data with the
    original graph (stages only read).
    """
    return add_travel_times(graph).reverse(copy=False)


def _reachable_from(graph_rev, source_node, threshold):
    """Nodes from which ``source_node`` is reachable within ``threshold`` seconds.

    Runs on the reversed graph so paths model person -> facility travel.
    """
    return nx.single_source_dijkstra_path_length(
        graph_rev, source_node, cutoff=threshold, weight="travel_time"
    )


def score_type_label(threshold):
    """The score_type string recorded on scored candidates."""
    return (
        f"population_new_within_{threshold // 60}min"
        if threshold % 60 == 0
        else f"population_new_within_{threshold}s"
    )


def score_all_candidates_population(candidates, pop_gdf, graph, hospitals=None,
                                    threshold=TRAVEL_TIME_THRESHOLD):
    """Score sites by NEW people within the threshold; adds score/score_total/score_type.

    Legacy per-candidate path: one Dijkstra per candidate. The pipeline now
    scores from the MCLP coverage matrix instead (same numbers, one pass);
    this function is kept as the reference implementation that the matrix
    path is regression-tested against.
    """
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
    scored["score_type"] = score_type_label(threshold)
    return scored


def score_from_matrix(inst, threshold=None):
    """Derive the per-candidate scores from an MCLP coverage matrix.

    The marginal score of candidate j is exactly its coverage row dotted with
    the population weights: ``score = A[j] @ w_new`` and
    ``score_total = A[j] @ w_total``. One matrix pass replaces the
    per-candidate Dijkstra loop; equality with
    ``score_all_candidates_population`` is regression-tested
    (tests/test_mclp.py::test_matrix_scoring_matches_legacy).
    """
    scored = inst.candidates.copy()
    scored["score"] = inst.A @ inst.w_new
    scored["score_total"] = inst.A @ inst.w_total
    scored["score_type"] = score_type_label(
        inst.threshold if threshold is None else threshold)
    return scored
