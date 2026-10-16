"""
api.py
======
HTTP API around the CARO extraction pipeline.

    uvicorn api:app --host 0.0.0.0 --port 8000

Two endpoints, one per consuming section:

    POST /caro/extract    -> BIR section      -> CARO_clauses only
    POST /caro/triggers   -> trigger section  -> CARO_ews only

Both take the same body:

    {
      "cin":           "U21000PN2017PTC169319",   # required
      "old_main_json": null,                      # accepted, not yet used
      "new_main_json": null,                      # accepted, not yet used
      "trigger_json":  null                       # accepted, not yet used
    }

Both arrays come from ONE extraction run (see views.py), so the two responses
always describe the same run rather than two independent passes. Whichever
endpoint is called first pays for extraction; the second is served from the
result cache within RESULT_CACHE_TTL. Calling only one endpoint is fine and
costs nothing extra.

On the three unused inputs
--------------------------
old_main_json, new_main_json and trigger_json are part of the agreed request
contract but are not consumed yet. They are accepted, validated only as
"any JSON or null", and echoed back under `received_inputs` so a caller can
confirm what arrived. Accepting them now means the callers can be built
against the final contract immediately, and wiring them up later is a
server-side change with no client impact. They are deliberately NOT stored
or logged in full — they may carry customer financial data, and this service
has no need to retain it.

LLM client lifetime
-------------------
The client is built once at startup and reused. Constructing it per request
would re-do connection setup and, for the transformers backend, reload model
weights. Requests are handled in FastAPI's threadpool (the endpoint is a
plain `def`), so a slow filing does not block the event loop.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header
from pydantic import BaseModel, Field, field_validator

load_dotenv()

from caro.cin_pipeline import process_cin, default_client_kwargs  # noqa: E402
from caro import perf                                    # noqa: E402
from caro.llm import make_client  # noqa: E402
from caro.logging_setup import configure_logging, get_logger  # noqa: E402
from caro.sources import resolve_source, SourceNotConfiguredError  # noqa: E402
from caro.views import clauses_view, ews_view, combined_view  # noqa: E402
from caro.output_check import check_output  # noqa: E402
from caro import result_cache  # noqa: E402

logger = get_logger(__name__)

_state: dict = {"client": None, "backend": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(run_name="api")
    backend, kw = default_client_kwargs()
    logger.info("starting API: llm_backend=%s source=%s", backend, resolve_source())
    _state["backend"] = backend
    try:
        _state["client"] = make_client(backend, **kw)
    except Exception as e:
        # Don't take the whole service down because the LLM server isn't up
        # yet — report it per request instead, and let /health show it.
        logger.error("LLM client init failed: %s", e)
        _state["client"] = None
        _state["client_error"] = str(e)
    yield
    _state.clear()


app = FastAPI(
    title="CARO 2020 Clause Extraction API",
    description="Extracts clause-level CARO 2020 data from a company's MCA filings.",
    version="1.3.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# request / response models
# ---------------------------------------------------------------------------
class ExtractRequest(BaseModel):
    cin: str = Field(..., description="Company Identification Number.",
                     examples=["U21000PN2017PTC169319"])
    old_main_json: Optional[Any] = Field(
        None, description="Reserved — accepted but not used in this version.")
    new_main_json: Optional[Any] = Field(
        None, description="Reserved — accepted but not used in this version.")
    trigger_json: Optional[Any] = Field(
        None, description="Reserved — accepted but not used in this version.")

    # per-call overrides; all optional, all default to the .env configuration
    batch_size: Optional[int] = Field(None, ge=1, le=21)
    include_nonxbrl: Optional[bool] = None
    source: Optional[str] = Field(None, pattern="^(local|s3)$")
    folder_path: Optional[str] = None
    max_years: Optional[int] = Field(
        None, ge=0, le=50,
        description="Process only the N most recent FINANCIAL YEARS for this "
                    "CIN. Overrides CIN_MAX_YEARS for this call; omit to use "
                    "the environment default; 0 means no limit. The count is "
                    "of distinct financial years, not documents: when a company "
                    "filed the same year twice, the more recently filed "
                    "document is kept and the other is skipped, so a slot is "
                    "never spent on a duplicate year.",
        examples=[5])
    refresh: Optional[bool] = Field(
        False, description="Ignore any cached extraction for this CIN and "
                           "re-run it. Use after the source documents change.")
    sections: Optional[list[str]] = Field(
        None,
        description="Which arrays to return: any of 'clauses', 'ews'. Overrides "
                    "the endpoint's own default, so POST /caro/extract with "
                    '{"sections": ["clauses", "ews"]} returns both from one '
                    "extraction. Omit to get the endpoint's default section.",
        examples=[["clauses", "ews"]])

    @field_validator("sections")
    @classmethod
    def _clean_sections(cls, v):
        if v is None:
            return None
        alias = {"clauses": "clauses", "caro_clauses": "clauses", "bir": "clauses",
                 "ews": "ews", "caro_ews": "ews", "trigger": "ews", "triggers": "ews"}
        out = []
        for item in v:
            key = alias.get(str(item).strip().lower())
            if key is None:
                raise ValueError(
                    f"unknown section {item!r}; use 'clauses' and/or 'ews'")
            if key not in out:
                out.append(key)
        if not out:
            raise ValueError("sections must not be empty")
        return out

    @field_validator("cin")
    @classmethod
    def _clean_cin(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if not v:
            raise ValueError("cin must not be empty")
        return v


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    """Liveness plus enough configuration detail to diagnose a bad deploy."""
    try:
        source = resolve_source()
        source_error = None
    except SourceNotConfiguredError as e:
        source, source_error = None, str(e)

    folder = os.environ.get("CIN_FOLDER_PATH")
    return {
        "status": "ok",
        "llm_backend": _state.get("backend"),
        "llm_ready": _state.get("client") is not None,
        "llm_error": _state.get("client_error"),
        "source": source,
        "source_error": source_error,
        "cin_folder_path": folder,
        "cin_folder_exists": os.path.isdir(folder) if folder else None,
    }


@app.post("/caro/extract")
def extract(req: ExtractRequest,
            x_perf_run_id: Optional[str] = Header(None)) -> dict:
    """BIR section: run the pipeline for one CIN and return CARO_clauses.

    Always returns HTTP 200 with a body carrying `status`. A CIN that cannot
    be located, or a bundle with no processable filings, is an expected
    business outcome — not an HTTP error — so callers should branch on
    `status` rather than on the status code.
    """
    return _run_section(req, clauses_view, "BIR", perf_run_id=x_perf_run_id)


@app.post("/caro/triggers")
def triggers(req: ExtractRequest,
             x_perf_run_id: Optional[str] = Header(None)) -> dict:
    """Trigger section: same run, returning CARO_ews instead of CARO_clauses.

    If /caro/extract was called for this CIN within RESULT_CACHE_TTL, this is
    served from that same extraction rather than re-running it — so the
    triggers are guaranteed to correspond to the clauses already delivered.
    """
    return _run_section(req, ews_view, "TRIGGER", perf_run_id=x_perf_run_id)


@app.post("/caro/all")
def all_sections(req: ExtractRequest,
                 x_perf_run_id: Optional[str] = Header(None)) -> dict:
    """Both sections in one call: CARO_clauses and CARO_ews together.

    Identical to calling /caro/extract and /caro/triggers, but the extraction
    runs once — so the two arrays are guaranteed to come from the same records
    and the expensive work (OCR + LLM) is not repeated.
    """
    return _run_section(req, combined_view, "ALL", perf_run_id=x_perf_run_id)


_VIEWS = {
    ("clauses",): (clauses_view, "BIR"),
    ("ews",): (ews_view, "TRIGGER"),
    ("clauses", "ews"): (combined_view, "ALL"),
}


def _resolve_view(req: ExtractRequest, project, section: str):
    """An explicit `sections` list overrides the endpoint's default view, so a
    caller can get both arrays from whichever URL it already posts to."""
    if not req.sections:
        return project, section
    key = tuple(sorted(req.sections))
    return _VIEWS.get(key, (combined_view, "ALL"))


def _run_section(req: ExtractRequest, project, section: str, perf_run_id: Optional[str] = None) -> dict:
    """Shared body for both endpoints: resolve client, extract (or reuse a
    cached extraction), then project to the requested section."""
    started = time.time()
    # part of a client batch (header X-Perf-Run-Id): one timing report for the
    # whole batch. The request body is unchanged.
    if perf_run_id:
        perf.run_join(perf_run_id)
        try:
            return _run_section_timed(req, project, section, started)
        finally:
            perf.detach()
    if not os.environ.get("CARO_PERF_DIR"):
        # no batch header and no report folder configured: nothing is written
        return _run_section_timed(req, project, section, started)
    _perf_run = perf.run_begin(f"api_{req.cin}_{section}")          # one report per request in CARO_PERF_DIR
    try:
        return _run_section_timed(req, project, section, started)
    finally:
        perf.run_end(run_id=_perf_run)


def _run_section_timed(req: ExtractRequest, project, section: str, started: float) -> dict:
    project, section = _resolve_view(req, project, section)
    # Log only the shape of the reserved inputs, never their contents.
    logger.info("[%s] %s section requested (old=%s new=%s trigger=%s)",
                req.cin, section,
                type(req.old_main_json).__name__ if req.old_main_json is not None else "none",
                type(req.new_main_json).__name__ if req.new_main_json is not None else "none",
                type(req.trigger_json).__name__ if req.trigger_json is not None else "none")

    client = _state.get("client")
    if client is None:
        # Retry once — the LLM server may have come up after this process did.
        try:
            backend, kw = default_client_kwargs()
            client = make_client(backend, **kw)
            _state["client"], _state["backend"] = client, backend
            _state.pop("client_error", None)
        except Exception as e:
            return _failed(req.cin, f"LLM backend unavailable: {e}", started, section, req)

    def _extract() -> dict:
        return process_cin(
            req.cin,
            client=client,
            batch_size=req.batch_size,
            include_nonxbrl=req.include_nonxbrl,
            source=req.source,
            folder_path=req.folder_path,
            max_years=req.max_years,
        )

    # Fingerprint the source documents so an entry is invalidated when the
    # files behind it change, not merely when the TTL lapses. Cheap for the
    # local source (a directory scan); None for S3, where checking would cost
    # a download.
    _perf_a1 = None
    with perf.for_cin(req.cin):
        _perf_a1 = perf.start("A1", part="cache fingerprint")
    fp = result_cache.source_fingerprint(
        req.cin, source=req.source, folder_path=req.folder_path,
        include_nonxbrl=req.include_nonxbrl
        if req.include_nonxbrl is not None else True,
        opts={"max_years": req.max_years,
              "batch_size": req.batch_size})

    perf.stop(_perf_a1)
    try:
        full, from_cache = result_cache.get_or_run(
            req.cin, _extract, refresh=bool(req.refresh), fingerprint=fp)
    except (FileNotFoundError, SourceNotConfiguredError) as e:
        perf.note_cin(req.cin, status="Failed", error=str(e))
        return _failed(req.cin, str(e), started, section, req)
    except Exception as e:  # unexpected, but never leak a stack trace to callers
        logger.exception("[%s] unhandled error", req.cin)
        perf.note_cin(req.cin, status="Failed", error=f"Unhandled error: {e}")
        return _failed(req.cin, f"Unhandled error: {e}", started, section, req)

    perf.note_from_result(full)
    perf.note_cin(req.cin, from_cache=from_cache)
    with perf.for_cin(req.cin):
        _perf_a1 = perf.start("A1", part="response build + schema check")
    out = project(full)
    # Run-level facts belong with the other descriptive fields, not beside the
    # clause arrays — `result` stays exactly what a renderer consumes.
    meta = out.setdefault("metadata", {})
    meta["processing_seconds"] = round(time.time() - started, 1)
    meta["from_cache"] = from_cache
    meta["received_inputs"] = _received(req)
    # Item 11 (2026-09-29): strict schema check of the finished response
    # (CARO_STRICT_SCHEMA=enforce | warn | off; see caro/output_check.py).
    out = check_output(out, req.cin)
    perf.stop(_perf_a1)
    logger.info("[%s] %s done: %s, %s/%s filings in %ss (cached=%s)",
                req.cin, section, out.get("status"), meta.get("years_succeeded"),
                meta.get("years_found"), meta["processing_seconds"], from_cache)
    return out


class PerfFinishRequest(BaseModel):
    run_id: str = Field(..., description="The run id the batch sent in the X-Perf-Run-Id header.")


@app.post("/caro/perf/finish")
def perf_finish(req: PerfFinishRequest):
    """Close a multi-request timing run and return its Excel report. Nothing is
    left on the server unless CARO_PERF_DIR is set."""
    import shutil, tempfile
    from fastapi.responses import FileResponse, JSONResponse
    from starlette.background import BackgroundTask
    rid = perf.known_run(req.run_id)
    # the report is only handed back to the caller (it saves it next to its JSON
    # files); a server copy is kept only when CARO_PERF_DIR is set
    keep = os.environ.get("CARO_PERF_DIR")
    tmp = None if keep else tempfile.mkdtemp(prefix="caro_perf_")
    path = perf.run_end(keep or tmp, run_id=rid) if rid else None
    if not path:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        return JSONResponse(status_code=404, content={
            "run_id": req.run_id, "report": None,
            "error": "no timing was recorded for this run id (unknown id, CARO_PERF=false, or every CIN came from the cache)"})
    return FileResponse(str(path), filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True) if tmp else None)


@app.post("/caro/cache/invalidate")
def cache_invalidate(cin: Optional[str] = None) -> dict:
    """Force the next request for a CIN (or all CINs) to re-extract. Useful
    after replacing source documents on disk mid-TTL."""
    removed = result_cache.invalidate(cin.strip().upper() if cin else None)
    return {"invalidated": removed, "cin": cin}


@app.get("/caro/cache/stats")
def cache_stats() -> dict:
    return result_cache.stats()


def _received(req: ExtractRequest) -> dict:
    """Confirm which optional inputs arrived, without echoing their content."""
    return {
        "cin": req.cin,
        "old_main_json": req.old_main_json is not None,
        "new_main_json": req.new_main_json is not None,
        "trigger_json": req.trigger_json is not None,
        "note": "old_main_json, new_main_json and trigger_json are accepted "
                "but not used in this version.",
    }


def _failed(cin: str, error: str, started: float, section: str,
            req: Optional[ExtractRequest] = None) -> dict:
    """Failure payload in the same status/result/metadata shape as success, so
    callers parse one structure regardless of outcome."""
    logger.warning("[%s] %s failed: %s", cin, section, error)
    meta = {
        "cin": cin,
        "section": section,
        "error": error,
        "years_found": 0,
        "years_succeeded": 0,
        "filings": [],
        "processing_seconds": round(time.time() - started, 1),
        "from_cache": False,
    }
    if req is not None:
        meta["received_inputs"] = _received(req)
    return {"status": "Failed", "result": [], "metadata": meta}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app",
                host=os.environ.get("API_HOST", "0.0.0.0"),
                port=int(os.environ.get("API_PORT", "8000")),
                reload=False)
