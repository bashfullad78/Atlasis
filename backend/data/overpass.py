"""Overpass hardening: mirror failover chain + fast-fail HTTP timeouts.

The public Overpass instances shed load under pressure: TCP connects to
``overpass-api.de`` hang (or the request stalls) for minutes while mirrors
such as ``overpass.kumi.systems`` answer fine. v0.2 spent three working
windows on Heidelberg's forbidden step because of this. This module gives
every Overpass-backed step:

1. **Mirror failover** — ``overpass_call(fn, ...)`` probes each mirror's
   ``/status`` endpoint with a short timeout and runs ``fn`` on the first
   healthy one. Any exception during ``fn`` fails over to the next mirror;
   the last-good mirror is remembered and tried first on later calls, so a
   flaky primary doesn't re-impose its probe cost on every download step.
2. **Fast-fail timeouts** — osmnx's ``requests_timeout`` feeds both the HTTP
   client and the ``[timeout:...]`` string sent to the server, so setting it
   to 45 s would also abort legitimate big-city road queries server-side.
   We decouple the two: the client gets ``(connect, read)`` timeouts (a dead
   mirror fails in ~10 s instead of hanging ~180 s) while the server-side
   query budget stays at ``SERVER_TIMEOUT_S``.

Mirrors can be overridden with ``ATLASIS_OVERPASS_MIRRORS`` (comma-separated
base URLs, e.g. ``https://overpass.kumi.systems/api,https://overpass-api.de/api``).
"""
import os
import threading

import osmnx as ox
import requests

PRIMARY_MIRROR = "https://overpass-api.de/api"
FALLBACK_MIRRORS = ["https://overpass.kumi.systems/api"]

CONNECT_TIMEOUT_S = 10   # dead mirror: TCP connect gives up here, not at 180 s
READ_TIMEOUT_S = 170     # live mirror: full budget for slow-but-working queries
SERVER_TIMEOUT_S = 180   # server-side [timeout:...] for the query itself
STATUS_PROBE_TIMEOUT_S = (5, 10)

_overpass_mirrors_cache = None
_last_good = None          # last mirror that served a successful call
_lock = threading.Lock()


def overpass_mirrors():
    """Mirror chain, primary first, from settings or the env override."""
    global _overpass_mirrors_cache
    with _lock:
        if _overpass_mirrors_cache is None:
            env = os.environ.get("ATLASIS_OVERPASS_MIRRORS")
            if env:
                mirrors = [m.strip() for m in env.split(",") if m.strip()]
            else:
                mirrors = [PRIMARY_MIRROR] + FALLBACK_MIRRORS
            _overpass_mirrors_cache = mirrors
        return list(_overpass_mirrors_cache)


def apply_timeouts():
    """Fast-fail client timeouts without shrinking the server-side budget.

    osmnx formats ``requests_timeout`` into the ``[timeout:...]`` query
    string, so a scalar 45 there would cap *server-side* query time too.
    Instead: a (connect, read) tuple for the HTTP client (requests accepts
    tuples) and a hardcoded server budget in ``overpass_settings`` whose
    ``{timeout}`` slot is left unused.
    """
    ox.settings.requests_timeout = (CONNECT_TIMEOUT_S, READ_TIMEOUT_S)
    ox.settings.overpass_settings = (
        f"[out:json][timeout:{SERVER_TIMEOUT_S}]{{maxsize}}"
    )


def probe_mirror(base_url):
    """True if the mirror's status endpoint answers quickly.

    A mirror that cannot serve ``/status`` within ~10 s will not serve a
    data query either, so this rejects dead mirrors in seconds instead of
    osmnx's default pause-then-hang behavior.
    """
    url = base_url.rstrip("/") + "/status"
    try:
        r = requests.get(url, timeout=STATUS_PROBE_TIMEOUT_S,
                         headers={"User-Agent": "atlasis/0.3"})
        return r.status_code == 200 and bool(r.text.strip())
    except requests.RequestException as e:
        print(f"  status probe failed for {base_url}: {e}")
        return False


def overpass_call(fn, *args, **kwargs):
    """Run ``fn`` (any osmnx Overpass call) with mirror failover.

    Probes mirrors in order, runs ``fn`` on the first healthy one, and fails
    over on any exception. The last-good mirror is remembered (module state)
    and tried first on subsequent calls within the same session.

    Returns whatever ``fn`` returns; raises ConnectionError if every mirror
    fails.
    """
    global _last_good
    mirrors = overpass_mirrors()
    if _last_good in mirrors:
        mirrors = [_last_good] + [m for m in mirrors if m != _last_good]

    apply_timeouts()
    last_err = None
    for mirror in mirrors:
        try:
            healthy = probe_mirror(mirror)
        except Exception as e:  # probe problems are failover signals
            healthy, err = False, e
        else:
            err = None if healthy else IOError(f"status probe unhealthy for {mirror}")
        if not healthy:
            last_err = err
            print(f"  ✗ Overpass mirror unavailable: {mirror}")
            continue
        ox.settings.overpass_url = mirror
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            print(f"  ✗ Overpass request failed on {mirror}: {e}")
            continue
        _last_good = mirror
        return result
    raise ConnectionError(
        f"All Overpass mirrors failed ({len(mirrors)} tried). "
        f"Last error: {last_err}"
    )
