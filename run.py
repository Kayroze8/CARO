#!/usr/bin/env python3
"""
run.py — CLI for the CARO auditor-report extraction pipeline.

Single file
-----------
python run.py FILE.pdf --backend vllm --model Qwen/Qwen3-8B \
       --base-url http://localhost:9001/v1 --no-guided -o out.json

Whole folder (batch) — one JSON per input + a _summary.json
-----------------------------------------------------------
python run.py pdfs/ --backend vllm --model Qwen/Qwen3-8B \
       --base-url http://localhost:9001/v1 --no-guided --out-dir results/

You can also pass several files / shell globs:
python run.py a.pdf b.pdf "pdfs/*2024*.pdf" --backend mock --out-dir results/

By CIN (downloads + extracts the filing bundle from S3 first)
---------------------------------------------------------------
python run.py --cin U12345MH2020PLC123456 -o out.json
# reads S3 + LLM server config from .env — see .env.example

Or programmatically, e.g. from another service:
    from run import main
    result, status, error_msg = main("U12345MH2020PLC123456")
    # status is "Success" or "Failed"; error_msg is None on success
"""
import argparse
import glob
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()  # populates AWS_*, BUCKET_NAME, LLM_* etc. from .env if present

from caro.llm import make_client
from caro.runner import process_one
from caro.cin_pipeline import process_cin, default_client_kwargs
from caro import perf
from caro.sources import SourceNotConfiguredError
from caro.logging_setup import configure_logging, get_logger

logger = get_logger(__name__)


def _expand(paths: list[str]) -> list[str]:
    """Expand directories to the PDFs inside them; pass files/globs through."""
    files: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for ext in ("*.pdf", "*.PDF"):
                files.extend(sorted(glob.glob(os.path.join(p, ext))))
        elif any(ch in p for ch in "*?[]"):
            files.extend(sorted(glob.glob(p)))
        else:
            files.append(p)
    # de-dup preserving order
    seen, out = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _safe_name(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in base)[:150]


def main(cin: str = None, **overrides):
    """CLI entry point. Also callable programmatically, and always returns
    a (result, status, error_msg) 3-tuple:

        from run import main
        result, status, error_msg = main("U12345MH2020PLC123456")
        # status: "Success" or "Failed"
        # error_msg: None on success, a short reason string otherwise
        # result: the full JSON-serializable dict (or, in batch/folder
        #   mode, the list of per-file summary dicts)

        result, status, error_msg = main("U12345...", batch_size=5, think=True)

    Any argparse option can be overridden by keyword this way (use the
    attribute name, e.g. `base_url` not `--base-url`).

    --cin mode never leaves files on disk: the downloaded archive and its
    extracted contents live in a temp dir that's deleted as soon as this
    call returns (pass keep_files=True to persist them instead — see
    cin_pipeline.process_cin).
    """
    ap = argparse.ArgumentParser(
        description="Extract & summarise CARO auditor clauses from MCA XBRL renders / auditor PDFs.")
    ap.add_argument("paths", nargs="*", default=[],
                    help="one or more files, shell globs, or a folder of PDFs "
                         "(omit this if using --cin)")
    ap.add_argument("--cin", default=None,
                    help="Company/LLP CIN — locates the filing bundle from the "
                         "configured source (local folder or S3; see --source) and "
                         "runs the pipeline on every financial-statements PDF found, "
                         "one per filing year.")
    ap.add_argument("--source", default=None, choices=["local", "s3"],
                    help="where filings come from. Default: $CIN_SOURCE, else local "
                         "when $CIN_FOLDER_PATH is set, else s3.")
    ap.add_argument("--max-years", type=int, default=None, metavar="N",
                    help="[--cin] process only the N most recent FINANCIAL "
                         "YEARS. Counts distinct years, not documents: a year "
                         "filed twice consumes one slot, keeping the later "
                         "filing. Default: $CIN_MAX_YEARS, else all. 0 = all.")
    ap.add_argument("--cin-folder", default=None,
                    help="[--source local] root folder holding extracted CIN "
                         "folders; overrides $CIN_FOLDER_PATH for this run.")
    ap.add_argument("--keep-files", action="store_true",
                    help="[--cin, S3 source only] keep the downloaded zip + extracted "
                         "folder afterwards (default: deleted once processing "
                         "finishes). No effect on the local source, which never "
                         "copies or deletes anything.")
    ap.add_argument("--backend", default=None,
                    choices=["ollama", "openai", "vllm", "transformers", "mock"],
                    help="default: ollama for file input; $LLM_BACKEND (else vllm) for --cin")
    ap.add_argument("--model", default=None)
    ap.add_argument("--base-url", default=None, help="for openai/vllm backend")
    ap.add_argument("--host", default=None, help="for ollama backend (default http://localhost:11434)")
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--think", action="store_true",
                    help="enable Qwen3 reasoning (default OFF; off is faster & "
                         "avoids JSON truncation for this extraction task)")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="generation budget per batch (openai/vllm backend)")
    ap.add_argument("--no-guided", action="store_true",
                    help="disable vLLM guided_json structured output")
    ap.add_argument("-o", "--out", default=None,
                    help="output JSON path for a SINGLE input, or a --cin run (default stdout)")
    ap.add_argument("--out-dir", default=None,
                    help="directory for batch output (one <name>.json per input + _summary.json)")
    ap.add_argument("--no-log", action="store_true",
                    help="disable logging entirely (no logs/ folder, no file, no console "
                         "log lines) — same as LOG_DISABLE=true in .env. The final JSON "
                         "result is still printed/written as normal.")

    if cin is not None or overrides:
        # Programmatic call: start from defaults, then apply cin/overrides
        # instead of parsing sys.argv (so this is safe to call from a web
        # server, task queue, etc. without argv interfering).
        args = ap.parse_args([])
        if cin is not None:
            args.cin = cin
        for k, v in overrides.items():
            if not hasattr(args, k):
                raise TypeError(f"main() got an unexpected keyword argument {k!r}")
            setattr(args, k, v)
    else:
        args = ap.parse_args()

    if not args.paths and not args.cin:
        ap.error("provide file(s)/folder(s), or --cin <CIN>")

    run_name = f"cin_{args.cin.strip().upper()}" if args.cin else "files"
    configure_logging(run_name=run_name, disable=args.no_log or None)

    # ------------------------------------------------------------------
    # --cin mode: fetch the filing bundle from S3, then process every
    # XBRL financial-statements PDF found (one call per filing year).
    # ------------------------------------------------------------------
    if args.cin:
        env_backend, kw = default_client_kwargs()  # LLM_* from .env
        backend = args.backend or env_backend
        if args.model:
            kw["model"] = args.model
        if backend in ("openai", "vllm"):
            if args.base_url:
                kw["base_url"] = args.base_url
            if args.think:
                kw["enable_thinking"] = True
            if args.max_tokens is not None:
                kw["max_tokens"] = args.max_tokens
            if args.no_guided:
                kw["guided_json"] = False
        elif backend == "ollama":
            if args.host:
                kw["host"] = args.host
            if args.think:
                kw["think"] = True
        elif backend == "transformers":
            if args.think:
                kw["enable_thinking"] = True

        client = make_client(backend, **kw)
        batch_size = (args.batch_size if args.batch_size is not None
                      else int(os.environ.get("LLM_BATCH_SIZE", "3")))

        perf.run_begin("cli_" + args.cin.strip().upper())        # 6-Oct: step timing report
        try:
            result = process_cin(args.cin, client=client, batch_size=batch_size,
                                  keep_files=args.keep_files,
                                  source=args.source,
                                  folder_path=args.cin_folder,
                                  max_years=args.max_years)
        except (FileNotFoundError, SourceNotConfiguredError) as e:
            # CIN not locatable in the configured source, no processable
            # filings (LLP bundle), or the source is misconfigured -> clean
            # failure payload instead of a stack trace.
            result = {
                "status": "Failed",
                "result": [],
                "metadata": {
                    "cin": args.cin.strip().upper(),
                    "error": str(e),
                    "years_found": 0,
                    "years_succeeded": 0,
                    "filings": [],
                },
            }

        text = json.dumps(result, indent=2, ensure_ascii=False)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
            logger.info(f"wrote {args.out}")
        else:
            print(text)

        perf.note_from_result(result)
        _pf = perf.run_end()
        if _pf:
            logger.info("step timing report: %s", _pf)
        status = result.get("status", "Failed")
        error_msg = (result.get("metadata") or {}).get("error")
        return result, status, error_msg

    # ------------------------------------------------------------------
    # file / folder mode (back-compatible with the original CLI)
    # ------------------------------------------------------------------
    backend = args.backend or "ollama"
    kw = {}
    if args.model:
        kw["model"] = args.model
    if backend == "ollama":
        if args.host:
            kw["host"] = args.host
        kw["think"] = args.think
    if backend in ("openai", "vllm"):
        if args.base_url:
            kw["base_url"] = args.base_url
        kw["enable_thinking"] = args.think
        kw["max_tokens"] = args.max_tokens if args.max_tokens is not None else 4096
        kw["guided_json"] = not args.no_guided
    if backend == "transformers":
        kw["enable_thinking"] = args.think

    files = _expand(args.paths)
    if not files:
        logger.error("No input files matched.")
        sys.exit(1)

    # build the client ONCE and reuse it across files (keeps the vLLM prefix
    # cache warm for the shared system prompt -> much faster on a folder).
    client = make_client(backend, **kw)
    batch_size = args.batch_size if args.batch_size is not None else 3

    # ---- single-file mode (back-compatible) ----
    perf.run_begin("cli_files")                                  # 6-Oct: step timing report
    if len(files) == 1 and not args.out_dir:
        with perf.filing_scope(files[0]):
            result = process_one(files[0], client, batch_size)
            perf.note_filing(status=result.get("status"))
        _pf = perf.run_end()
        if _pf:
            logger.info("step timing report: %s", _pf)
        text = json.dumps(result, indent=2, ensure_ascii=False)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
            logger.info(f"wrote {args.out}")
        else:
            print(text)

        status = result.get("status", "Failed")
        error_msg = None
        if status != "Success":
            m = result.get("metadata", {}) or {}
            r = result.get("result", {}) or {}
            error_msg = m.get("error") or r.get("caro_exempt_reason")
        return result, status, error_msg

    # ---- batch mode ----
    out_dir = args.out_dir or "caro_out"
    os.makedirs(out_dir, exist_ok=True)
    summary = []
    logger.info(f"[batch] {len(files)} file(s) -> {out_dir}/")
    for i, path in enumerate(files, 1):
        logger.info(f"===== [{i}/{len(files)}] {os.path.basename(path)} =====")
        with perf.filing_scope(path):
            result = process_one(path, client, batch_size)
            perf.note_filing(status=result.get("status"))
        out_path = os.path.join(out_dir, _safe_name(path) + ".json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        m = result.get("metadata", {})
        res = result.get("result", {})
        n = len(res["CARO_clauses"]) if isinstance(res, dict) and "CARO_clauses" in res else 0
        summary.append({
            "file": os.path.basename(path),
            "output": os.path.basename(out_path),
            "status": result.get("status"),
            "company_name": m.get("company_name"),
            "financial_year": m.get("financial_year"),
            "caro_applicable": m.get("caro_applicable"),
            "report_qualified": m.get("report_qualified"),
            "clause_count": n,
            "document_red_flag": m.get("document_red_flag"),
            "source_format": m.get("source_format"),
            "caro_exempt_reason": m.get("caro_exempt_reason"),
        })

    with open(os.path.join(out_dir, "_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    _pf = perf.run_end(out_dir)                                  # 6-Oct: step timing report
    if _pf:
        logger.info("step timing report: %s", _pf)

    # summary table -> log (console + file)
    logger.info("================ SUMMARY ================")
    logger.info(f"{'STATUS':8s} {'CLS':>3s} {'APPL':>5s} {'QUAL':>5s}  FILE")
    for s in summary:
        logger.info(f"{str(s['status']):8s} {s['clause_count']:>3d} "
                    f"{str(s['caro_applicable']):>5s} {str(s['report_qualified']):>5s}  {s['file']}")
    ok = sum(1 for s in summary if s["status"] == "Success")
    logger.info(f"[done] {ok}/{len(summary)} succeeded. Per-file JSON + _summary.json in {out_dir}/")

    overall_status = "Success" if summary and ok == len(summary) else "Failed"
    error_msg = None
    if overall_status != "Success":
        failed = [s["file"] for s in summary if s["status"] != "Success"]
        error_msg = f"{len(failed)}/{len(summary)} file(s) failed: {', '.join(failed)}"
    return summary, overall_status, error_msg


# ---------------------------------------------------------------------------
# Section entry points
# ---------------------------------------------------------------------------
# main() returns the complete result (CARO_clauses + CARO_ews + CARO_quality)
# and is unchanged. The two functions below return ONE section each, matching
# how the output is consumed downstream: clauses go to the BIR section,
# triggers go to the trigger section.
#
# Both are backed by the same extraction and the same result cache the API
# uses, so calling main_clauses() and then main_ews() for the same CIN runs
# the pipeline ONCE — the second call projects the stored result. That also
# guarantees the two sections describe the same run rather than two
# independent passes that could classify a borderline clause differently.
def _run_cin_section(cin: str, project, section: str,
                     refresh: bool = False, **overrides) -> tuple:
    """Shared body: extract once (or reuse cache), then project."""
    from caro.views import clauses_view, ews_view  # noqa: F401
    from caro import result_cache

    cin = (cin or "").strip().upper()
    if not cin:
        return {"cin": cin, "section": section, "status": "Failed",
                "error": "cin must not be empty"}, "Failed", "cin must not be empty"

    configure_logging(run_name=f"cin_{cin}", disable=overrides.pop("no_log", None))

    env_backend, kw = default_client_kwargs()
    backend = overrides.pop("backend", None) or env_backend
    for k in ("model", "base_url", "host", "max_tokens"):
        v = overrides.pop(k, None)
        if v is not None:
            kw[k] = v
    client = make_client(backend, **kw)

    def _extract():
        return process_cin(cin, client=client,
                           batch_size=overrides.pop("batch_size", None),
                           include_nonxbrl=overrides.pop("include_nonxbrl", None),
                           source=overrides.pop("source", None),
                           folder_path=overrides.pop("folder_path", None),
                           keep_files=overrides.pop("keep_files", None))

    fp = result_cache.source_fingerprint(
        cin, source=overrides.get("source"),
        folder_path=overrides.get("folder_path"))

    try:
        full, from_cache = result_cache.get_or_run(
            cin, _extract, refresh=refresh, fingerprint=fp)
    except (FileNotFoundError, SourceNotConfiguredError) as e:
        result = {"cin": cin, "section": section, "status": "Failed",
                  "error": str(e), "years_found": 0, "years_succeeded": 0,
                  "filings": []}
        return result, "Failed", str(e)

    result = project(full)
    result["from_cache"] = from_cache
    return result, result.get("status", "Failed"), result.get("error")


def main_clauses(cin: str, refresh: bool = False, **overrides) -> tuple:
    """BIR section. Returns (result, status, error_msg) where result carries
    CARO_clauses only.

        from run import main_clauses
        result, status, error_msg = main_clauses("U21000PN2017PTC169319")
    """
    from caro.views import clauses_view
    return _run_cin_section(cin, clauses_view, "BIR", refresh=refresh, **overrides)


def main_ews(cin: str, refresh: bool = False, **overrides) -> tuple:
    """Trigger section. Returns (result, status, error_msg) where result
    carries CARO_ews only, plus a worst_color_code and the list of red-flagged
    clause ids per filing.

        from run import main_ews
        result, status, error_msg = main_ews("U21000PN2017PTC169319")

    Call this after main_clauses() for the same CIN and the extraction is
    reused rather than repeated (see caro/result_cache.py).
    """
    from caro.views import ews_view
    return _run_cin_section(cin, ews_view, "TRIGGER", refresh=refresh, **overrides)


if __name__ == "__main__":
    main()
