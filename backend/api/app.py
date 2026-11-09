"""Jobs API submit + poll, in-memory state.

Run: python -m backend.api.app        (0.0.0.0:7860)

Endpoints:
  POST /api/jobs    {place, country?, threshold?, num_sites?} -> 202 {job_id}
                    errors: 400 validation, 429 queue/rate limit
  GET  /api/jobs/{id} -> {status, stage, elapsed_s, result?, error?, comparable}
  GET  /api/health    -> {"ok": true} 
  GET  /               -> web/ static UI 

Everything runs on the module-global store in backend.api.runner. Importing
this module is cheap — the pipeline/graph machinery stays lazy until the
worker touches it.
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.api import runner

app = FastAPI(title="ATLASIS jobs API")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/jobs")
async def create_job(request: Request):
    body = await request.json()
    ip = request.client.host if request.client else "unknown"
    try:
        job_id = runner.submit_job(body, ip=ip)
    except runner.BadRequestError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except runner.RateLimitError as e:
        return JSONResponse({"error": str(e)}, status_code=429)
    except runner.QueueFullError as e:  # one running + one queued already
        return JSONResponse({"error": str(e)}, status_code=429)
    return JSONResponse({"job_id": job_id, "status": "queued"},
                        status_code=202)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    snap = runner.snapshot(job_id)
    if snap is None:
        return JSONResponse({"error": "unknown job id"}, status_code=404)
    return snap


# UI: serve web/ at "/" (index.html + app.js). Mounted last so every
# /api route above wins the match first.
import os as _os  # noqa: E402

_WEB_DIR = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__)))), "web")
if _os.path.isdir(_WEB_DIR):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=7860)  # conventional app port
