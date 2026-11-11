"""Job execution backend for the web API: in-memory store, single
worker thread, queue cap 1, per-IP soft rate limit.

The worker runs acquisition -> pipeline -> payload and writes state back through
``snapshot``-safe plain dicts. Every failure inside a job becomes
``status="failed"`` with a message string — the poll endpoint never
escalates a job's own error to an HTTP 500.

Heavy imports (pipeline/graph machinery) happen lazily inside the worker so
importing this module — and therefore the FastAPI app — stays cheap. Tests monkeypatch the ``_RUN_PIPELINE`` /
``_GRAPH_LOADER`` / ``_HOSPITAL_LOADER`` indirections to stay offline.

Caps: threshold in {300, 600, 900} s, k <= 5,
one running job, one queued job (further submits get RateLimitError),
3 submissions per IP per 10 min (soft limit; a job takes ~5 min at most).
"""
import collections
import re
import threading
import time
import uuid

ALLOWED_THRESHOLDS = {300, 600, 900}
MAX_SITES = 5
QUEUE_CAP = 1
IP_JOBS_PER_WINDOW = 3
IP_WINDOW_S = 600
ACQUISITION_FALLBACK_S = 240  # restart acquisition with skip_forbidden past this

_STAGE_LABELS = {
    "boundary": "loading boundary",
    "hospitals": "loading existing facilities",
    "roads": "loading road graph",
    "candidates": "generating candidate sites",
    "filters": "applying hard constraints",
    "coverage": "scoring candidates",
    "selection": "joint MCLP selection",
    "accessibility": "accessibility before/after",
    "twosfca": "2SFCA index",
    "acquire": "fetching city data",
    "acquire_fallback": "fetching city data (skipping forbidden zones)",
    "iso": "computing isochrones",
}

_LOCK = threading.Condition()
_JOBS = {}                       # job_id -> state dict (worker mutates, pollers read)
_QUEUE = collections.deque()     # job_ids waiting (<= QUEUE_CAP)
_IP_SUBMITS = collections.defaultdict(collections.deque)  # ip -> timestamps
_WORKER_THREAD = None

# Offline-test indirections; lazily bound to real implementations in the worker.
_RUN_PIPELINE = None
_GRAPH_LOADER = None    # cfg -> (graph, graph_rev) travel-time stamped
_HOSPITAL_LOADER = None  # cfg -> hospitals GeoDataFrame


class BadRequestError(ValueError):
    """Invalid job parameters (mapped to HTTP 400)."""


class RateLimitError(Exception):
    """Per-IP window exceeded (HTTP 429)."""


class QueueFullError(Exception):
    """One job is running and one queued already (HTTP 503)."""


def _validate(body):
    """Job parameters with caps.

    Returns (place, country, threshold, num_sites, city_slug). Exactly one of
    ``city`` (a registered/existing data-dir slug) or ``place`` (a geocodable
    string) is required: ``city`` reuses an existing cache directory as-is,
    while ``place`` derives a fresh slug from the string and therefore never
    hits an existing cache ("Freiburg im Breisgau, Germany" -> a dir that has
    nothing to do with data/raw/freiburg).
    """
    if not isinstance(body, dict):
        raise BadRequestError("Request body must be a JSON object.")
    city = body.get("city")
    place = body.get("place")
    if city is not None:
        if not isinstance(city, str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9_-]*", city.strip()):
            raise BadRequestError(
                "'city' must be a registered lowercase slug, e.g. 'testville'.")
        city = city.strip()
        if place is None:
            place = city
    if not isinstance(place, str) or not place.strip() or len(place) > 160:
        raise BadRequestError("'place' must be a 1-160 char place string.")
    country = body.get("country")
    if country is not None and (not isinstance(country, str)
                                or not re.fullmatch(r"[A-Za-z]{3}", country)):
        raise BadRequestError("'country' must be an ISO 3166-1 alpha-3 code.")
    threshold = body.get("threshold", 900)
    if threshold not in ALLOWED_THRESHOLDS:
        raise BadRequestError(
            f"'threshold' must be one of {sorted(ALLOWED_THRESHOLDS)} seconds.")
    num_sites = body.get("num_sites", 1)
    if not isinstance(num_sites, int) or not 1 <= num_sites <= MAX_SITES:
        raise BadRequestError(f"'num_sites' must be an integer 1..{MAX_SITES}.")
    slug = city if city is not None else _slug_for(place.strip())
    return place.strip(), country, threshold, num_sites, slug


def _slug_for(place):
    """Deterministic data-dir slug from a place string."""
    slug = re.sub(r"-{2,}", "-",
                  re.sub(r"[^a-z0-9]+", "-", place.lower())).strip("-")[:60].rstrip("-")
    if not slug:
        raise BadRequestError(f"Could not derive a usable city slug from {place!r}.")
    return slug


def submit_job(body, ip="test"):
    """Validate + enqueue; returns job_id. Raises the three error classes."""
    global _WORKER_THREAD
    place, country, threshold, num_sites, slug = _validate(body)
    now = time.time()
    with _LOCK:
        submits = _IP_SUBMITS[ip]
        while submits and now - submits[0] > IP_WINDOW_S:
            submits.popleft()
        if len(submits) >= IP_JOBS_PER_WINDOW:
            raise RateLimitError(
                "Rate limit: try again a bit later (jobs take minutes each).")
        if len(_QUEUE) >= QUEUE_CAP:
            raise QueueFullError(
                " queuing one job after the current one is supported.")
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "job_id": job_id, "place": place, "ip": ip,
            "params": {"place": place, "city": slug, "country": country,
                       "threshold": threshold, "num_sites": num_sites},
            "status": "queued", "stage": None, "result": None, "error": None,
            "comparable": True,
            "created_at": now, "started_at": None, "finished_at": None,
            "_t0_mono": None, "_t1_mono": None,
        }
        submits.append(now)
        _QUEUE.append(job_id)
        if _WORKER_THREAD is None or not _WORKER_THREAD.is_alive():
            _WORKER_THREAD = threading.Thread(
                target=_worker_loop, name="atlasis-worker", daemon=True)
            _WORKER_THREAD.start()
        _LOCK.notify()
    return job_id


def snapshot(job_id):
    """Poller-safe job snapshot (returns a copy), or None for unknown ids."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        snap = dict(job)
        snap["params"] = dict(job["params"])
        if snap["result"] is not None:
            snap["result"] = dict(snap["result"])
        for k in [k for k in snap if k.startswith("_")]:
            snap.pop(k)
        # monotonic: the container clock can be NTP-corrected mid-run
        if job["_t1_mono"] is not None:
            snap["elapsed_s"] = round(job["_t1_mono"] - job["_t0_mono"], 1)
        elif job["_t0_mono"] is not None:
            snap["elapsed_s"] = round(time.monotonic() - job["_t0_mono"], 1)
        else:  # never waited long enough to matter at queue depth 1
            snap["elapsed_s"] = 0.0
        return snap


def _worker_loop():
    while True:
        with _LOCK:
            if not _QUEUE:
                _LOCK.wait()
                continue
            job_id = _QUEUE.popleft()
            job = _JOBS[job_id]
            job["status"] = "running"
            job["started_at"] = time.time()
            job["_t0_mono"] = time.monotonic()
            params, comparable_box = dict(job["params"]), job

        try:
            _run_job(params, comparable_box)
        except Exception as e:  # noqa: BLE001 - every failure is job state
            with _LOCK:
                comparable_box["status"] = "failed"
                comparable_box["error"] = f"{type(e).__name__}: {e}"
                comparable_box["finished_at"] = time.time()
                comparable_box["_t1_mono"] = time.monotonic()


def _progress_fn(job):
    def progress(stage):
        label = _STAGE_LABELS.get(stage, stage)
        with _LOCK:
            if job["status"] == "running":
                job["stage"] = label
    return progress


def _bind_loaders():
    """Bind the lazy heavy loaders (worker start / offline exporters)."""
    global _RUN_PIPELINE, _GRAPH_LOADER, _HOSPITAL_LOADER
    if _RUN_PIPELINE is None:
        from backend.pipeline import run_pipeline as _rp
        _RUN_PIPELINE = _rp
    if _GRAPH_LOADER is None:
        from backend.objectives.coverage import (add_travel_times,
                                                 reverse_with_travel_times)
        from backend.routing.graph import load_graph

        def _graph(cfg):
            graph = add_travel_times(load_graph(cfg))
            return graph, reverse_with_travel_times(graph)
        _GRAPH_LOADER = _graph
    if _HOSPITAL_LOADER is None:
        from backend.gis.hospitals import load_hospitals
        _HOSPITAL_LOADER = load_hospitals


def result_payload(cfg, params, res):
    """Full API result payload (stats + sites + isochrones) from a
    run_pipeline return dict. Shared by the worker and the gallery exporter
    so both serve byte-identical shapes."""
    from backend.config import utc_now_iso

    payload = _build_payload(cfg, params, res)
    sel = res["selection"]
    # Isochrone layers: reverse multi-source Dijkstra + reachable-edge scan
    # (same derivation as fig4 in the render script), baseline vs joint set.
    added = [sel["candidates"].iloc[j].geometry for j in sel["selected"]]
    payload["isochrones"] = {
        "baseline": _isochrone_features(cfg, params["threshold"], added=()),
        "joint": _isochrone_features(cfg, params["threshold"], added=added),
    }
    payload["comparable"] = True
    payload["generated_at"] = utc_now_iso()
    return payload


def _run_job(params, job):
    from backend.config import city_config
    from backend.data import prepare

    with _LOCK:
        job["stage"] = _STAGE_LABELS["acquire"]

    # Lazy heavy imports + test indirections.
    _bind_loaders()

    slug = params["city"]  # data-dir slug: either the caller's city slug or _slug_for(place)
    cfg = city_config(slug, place=params["place"], country=params["country"])
    cfg.place = params["place"]
    progress = _progress_fn(job)

    with _LOCK:
        job["stage"] = "fetching city data"
    t0 = time.perf_counter()
    skip_forbidden = False
    try:
        prepare.ensure_data(cfg, skip_forbidden=False)
    except Exception as e:  # noqa: BLE001 - fallback is the designed path
        if not isinstance(e, (ConnectionError, TimeoutError, OSError)) \
                or time.perf_counter() - t0 <= ACQUISITION_FALLBACK_S:
            raise
        with _LOCK:
            job["comparable"] = False
            job["stage"] = _STAGE_LABELS["acquire_fallback"]
        print(f"[jobs] acquisition slow/failed after "
              f"{time.perf_counter() - t0:.0f}s ({type(e).__name__}); "
              f"retrying with skip_forbidden (payload NOT comparable)")
        skip_forbidden = True
        prepare.ensure_data(cfg, skip_forbidden=True)

    res = _RUN_PIPELINE(cfg, threshold=params["threshold"],
                        num_sites=params["num_sites"], mode="greedy",
                        skip_forbidden=skip_forbidden, progress=progress)
    with _LOCK:
        job["stage"] = _STAGE_LABELS["iso"]
    payload = result_payload(cfg, params, res)
    payload["comparable"] = job["comparable"]
    with _LOCK:
        job["result"] = payload
        job["status"] = "done"
        job["stage"] = None
        job["finished_at"] = time.time()
        job["_t1_mono"] = time.monotonic()


def _build_payload(cfg, params, res):
    """API result payload from a run_pipeline return dict. Sites come back in
    WGS84 lat/lng; the joint set carries greedy gains/objective when they exist."""
    from pyproj import Transformer

    from backend.config import load_meta, newest_data_time

    t = Transformer.from_crs(cfg.crs, "EPSG:4326", always_xy=True)
    scored = res["selection"]["candidates"]
    mclp = res["selection"].get("solver_results") or {}
    greedy = mclp.get("greedy", {})
    gains = greedy.get("gains") or []
    joint = []
    for r, j in enumerate(res["selection"]["selected"], start=1):
        geom = scored.iloc[j].geometry
        lng, lat = t.transform(geom.x, geom.y)
        entry = {"rank": r, "lat": round(float(lat), 6),
                 "lng": round(float(lng), 6)}
        if r - 1 < len(gains):
            entry["gain_people"] = round(float(gains[r - 1]))
        if greedy.get("objective") is not None:
            entry["objective_people"] = round(float(greedy["objective"]))
        joint.append(entry)

    ranked = []
    for _, row in res["top_sites"].iterrows():
        lng, lat = t.transform(row.geometry.x, row.geometry.y)
        ranked.append({"rank": int(row["rank"]),
                       "lat": round(float(lat), 6), "lng": round(float(lng), 6),
                       "gain_people": int(row["score"]),
                       "score_type": row["score_type"]})

    return {
        "comparable": True,  # overwritten by _run_job with the real flag
        "params": params,
        "baseline_pct": _py(res["baseline"].get("coverage_pct")),
        "optimized_single_pct": _py(res["optimized"].get("coverage_pct")),
        "joint_pct": (_py(res["optimized_set"].get("coverage_pct"))
                      if res.get("optimized_set") else None),
        "newly_covered_people": _py(res["comparison"].get("newly_covered_population")),
        "total_population": _py(res["baseline"].get("total_population")),
        "top_sites": ranked,
        "sites": joint,
        "data_stamps": {k: v for k, v in load_meta(cfg).items()
                        if k.startswith("fetched_")},
        "data_as_of": _iso(newest_data_time(cfg)),
    }


def _isochrone_features(cfg, threshold, added=()):
    """Reachable-road isochrone as GeoJSON: reverse multi-source Dijkstra
    (person -> facility) from all facility nodes, then every edge whose
    min(d(u), d(v)) + travel_time(u,v) <= threshold. Same derivation as fig4
    of the render script. COORDS in WGS84 [lng, lat]; this is the API's
    money payload — consider encoding/clip optimizations in a later chunk.
    """
    import networkx as nx
    from pyproj import Transformer

    graph, graph_rev = _GRAPH_LOADER(cfg)
    hospitals = _HOSPITAL_LOADER(cfg)
    geoms = list(hospitals.geometry) + list(added)
    fac_nodes = _nearest_nodes(graph_rev, geoms)
    dist = nx.multi_source_dijkstra_path_length(
        graph_rev, list(set(fac_nodes)), cutoff=threshold, weight="travel_time")
    t = Transformer.from_crs(graph_rev.graph["crs"], "EPSG:4326", always_xy=True)
    inf = float("inf")
    feats = []
    for u, v, data in graph.edges(data=True):
        tt = data.get("travel_time", inf)
        if tt is inf:
            continue
        d = min(dist.get(u, inf), dist.get(v, inf))
        if d is inf or d + tt > threshold:
            continue
        x1, y1 = graph.nodes[u]["x"], graph.nodes[u]["y"]
        x2, y2 = graph.nodes[v]["x"], graph.nodes[v]["y"]
        (lng1, lat1), (lng2, lat2) = t.transform(x1, y1), t.transform(x2, y2)
        feats.append({"type": "Feature",
                      "properties": {},
                      "geometry": {"type": "LineString",
                                   "coordinates": [[lng1, lat1], [lng2, lat2]]}})
    return {"type": "FeatureCollection", "features": feats}


def _nearest_nodes(graph_rev, geoms):
    """One vectorized snap (tests monkeypatch ox.distance.nearest_nodes)."""
    import geopandas as gpd
    import osmnx as ox

    if not isinstance(geoms, gpd.GeoSeries):
        geoms = gpd.GeoSeries(geoms)
    return list(ox.distance.nearest_nodes(
        graph_rev, geoms.x.values, geoms.y.values))


def _py(v):
    return None if v is None else round(float(v), 2)


def _iso(dt):
    return None if dt is None else dt.isoformat(timespec="seconds")
