#!/usr/bin/env python3
"""
run_batch.py
============
Process a list of CINs one at a time and write one JSON result per CIN.

    python run_batch.py cins.txt --out-dir results/

Input
-----
A file with one CIN per line. Blank lines and lines starting with '#' are
ignored, so the same file can carry comments. A CIN already surrounded by
whitespace is fine. Example:

    # Q3 review batch
    U74899DL1994PLC061080
    U15510MH1988PTC045769

Output
------
For each CIN, <out-dir>/<CIN>.json — the exact document the API returns.
A run manifest is written to <out-dir>/_run_summary.json and a one-line-per-CIN
progress log to the console.

Timing
------
Every filing's own extraction time is measured HERE, by wrapping the
pipeline's process_one() — the pipeline itself never times a single filing,
only the whole run_pipeline() call implicitly. The wrapper adds no behaviour;
it starts a clock, calls the real function, stops the clock, and records the
elapsed seconds keyed by the file's path.

After process_cin() returns, those per-file times are matched onto
metadata.filings by source_path (present on every filing entry) and written
into each per-CIN JSON as filing_seconds. The CIN's own total — including
locating and downloading the bundle, not just the sum of its filings — is
still processing_seconds, as before. _run_summary.json additionally carries
a flattened per_file_timings list across the whole run, and a slowest_files
top-10, so a slow filing anywhere in a large batch is easy to find without
opening every CIN's JSON.

Why it is shaped this way
-------------------------
- The LLM client is built ONCE and reused across every CIN. Rebuilding it per
  CIN would re-establish the connection each time and, on the transformers
  backend, reload weights — minutes of waste on a long list.
- Each CIN is independent: one CIN failing (not found, no filings, an LLM
  outage mid-run) does not stop the batch. Its error is recorded and the run
  continues. process_cin already declines to raise for per-filing problems;
  this adds the same guarantee at the CIN level.
- A CIN whose output file already exists is SKIPPED by default, so a batch
  interrupted after 300 of 500 CINs resumes from 301 rather than repeating
  work. Pass --overwrite to force a full re-run.
- Configuration (source, folder path, year limit, backend) comes from .env,
  exactly as the API and CLI read it, so a batch behaves identically to a
  single request. Flags below override .env per run.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# Import from the package the same way the API does.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv                       # noqa: E402
load_dotenv()

from caro.cin_pipeline import process_cin, default_client_kwargs  # noqa: E402
from caro.llm import make_client                     # noqa: E402
from caro.logging_setup import configure_logging, get_logger  # noqa: E402

logger = get_logger("run_batch")


# ---------------------------------------------------------------------------
# per-file timing
# ---------------------------------------------------------------------------
# Filled in by _install_filing_timer() and read after each process_cin() call.
# Keyed by the exact path string process_one() was called with, which is the
# same value cin_pipeline puts in metadata.filings[i]["source_path"] — that
# is what lets the two be matched up afterward with no other plumbing.
_filing_times: dict[str, float] = {}


def _install_filing_timer():
    """Wrap caro.cin_pipeline's process_one with a timer, in place.

    cin_pipeline.py does `from .runner import process_one` at import time, so
    the name it calls is a module-level reference inside caro.cin_pipeline —
    patching caro.runner.process_one alone would not affect it. Patching that
    reference directly, here, means the pipeline package itself needs no
    changes: this file is self-contained and can be dropped in on its own.
    """
    import caro.cin_pipeline as _cp

    original = _cp.process_one

    def timed_process_one(path, client, batch_size):
        t0 = time.time()
        try:
            return original(path, client, batch_size)
        finally:
            _filing_times[str(path)] = round(time.time() - t0, 2)

    _cp.process_one = timed_process_one


def _attach_filing_timings(result: dict) -> dict:
    """Merge the recorded per-file times into result.metadata.filings, and
    return the per-file rows (for the run-wide summary) alongside the total."""
    rows = []
    filings = (result.get("metadata") or {}).get("filings") or []
    for f in filings:
        secs = _filing_times.get(f.get("source_path"))
        f["filing_seconds"] = secs           # None if somehow not captured
        rows.append({
            "source_pdf": f.get("source_pdf"),
            "financial_year": f.get("financial_year"),
            "status": f.get("status"),
            "filing_seconds": secs,
        })
    return rows


def read_cins(path: str) -> list[str]:
    """One CIN per line; blanks and '#' comments ignored; de-duplicated but
    order preserved."""
    seen, out = set(), []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        cin = raw.strip()
        if not cin or cin.startswith("#"):
            continue
        cin = cin.upper()
        if cin not in seen:
            seen.add(cin)
            out.append(cin)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-process a list of CINs.")
    ap.add_argument("cin_file", help="File with one CIN per line.")
    ap.add_argument("--out-dir", default="results",
                    help="Directory for per-CIN JSON output (default: results).")
    ap.add_argument("--overwrite", action="store_true",
                    help="Re-process CINs whose output file already exists. "
                         "Default: skip them, so an interrupted run resumes.")
    ap.add_argument("--max-years", type=int, default=None,
                    help="Override CIN_MAX_YEARS for this run (0 = all years).")
    ap.add_argument("--source", choices=["local", "s3"], default=None,
                    help="Override CIN_SOURCE for this run.")
    ap.add_argument("--folder-path", default=None,
                    help="Override CIN_FOLDER_PATH for this run.")
    ap.add_argument("--batch-size", type=int, default=None,
                    help="Override LLM_BATCH_SIZE for this run.")
    args = ap.parse_args()

    configure_logging(run_name="batch")
    _install_filing_timer()

    cins = read_cins(args.cin_file)
    if not cins:
        print("No CINs found in", args.cin_file, file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build the LLM client once and reuse it for every CIN.
    backend, kw = default_client_kwargs()
    if args.batch_size is not None:
        kw = {**kw}      # do not mutate the shared dict
    try:
        client = make_client(backend, **kw)
        client_error = None
    except Exception as e:
        # Don't abort: mock/offline runs and misconfigured servers should still
        # produce a manifest saying so, rather than a bare traceback.
        client, client_error = None, str(e)
        logger.error("LLM client init failed: %s", e)

    print(f"batch: {len(cins)} CIN(s) -> {out_dir}/   backend={backend}")

    manifest = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "backend": backend,
        "total": len(cins),
        "results": [],
        # Every filing across the whole run, flattened, for a quick scan of
        # what took long anywhere in the batch without opening each CIN's
        # JSON individually.
        "per_file_timings": [],
    }
    ok = failed = skipped = 0
    t0 = time.time()

    for i, cin in enumerate(cins, start=1):
        out_path = out_dir / f"{cin}.json"
        # Resume skips only SUCCESSFUL prior output. A CIN that previously
        # failed — a transient LLM outage, a bundle not yet synced — is
        # retried, so a resumed run heals earlier failures instead of
        # freezing them. --overwrite forces every CIN to re-run.
        if out_path.exists() and not args.overwrite:
            try:
                prev = json.loads(out_path.read_text(encoding="utf-8"))
                prev_ok = prev.get("status") == "Success"
            except Exception:
                prev_ok = False
            if prev_ok:
                print(f"[{i:>4}/{len(cins)}] {cin}  SKIP (done)")
                skipped += 1
                manifest["results"].append({"cin": cin, "status": "Skipped",
                                            "file": out_path.name})
                continue

        started = time.time()
        _filing_times.clear()        # only this CIN's filings, not a prior one's
        try:
            result = process_cin(
                cin,
                client=client,
                batch_size=args.batch_size,
                source=args.source,
                folder_path=args.folder_path,
                max_years=args.max_years,
            )
        except Exception as e:
            # process_cin does not raise for per-filing problems, but a CIN
            # that cannot be located, or a genuine bug, could. Contain it so
            # one bad CIN never ends the batch.
            logger.exception("[%s] unhandled error", cin)
            result = {
                "status": "Failed",
                "result": [],
                "metadata": {"cin": cin, "error": str(e),
                             "years_found": 0, "years_succeeded": 0,
                             "filings": []},
            }

        secs = round(time.time() - started, 1)
        result.setdefault("metadata", {})["processing_seconds"] = secs
        file_rows = _attach_filing_timings(result)
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                            encoding="utf-8")

        md = result.get("metadata", {})
        status = result.get("status", "Failed")
        years = md.get("years_succeeded", 0)
        found = md.get("years_found", 0)
        if status == "Success":
            ok += 1
        else:
            failed += 1
        print(f"[{i:>4}/{len(cins)}] {cin}  {status:7s} "
              f"{years}/{found} years  {secs:>6.1f}s")
        for r in file_rows:
            fs = r["filing_seconds"]
            print(f"           - {str(r['source_pdf'])[:60]:60s} "
                  f"{r['status']:7s} {('%.1fs' % fs) if fs is not None else '  n/a':>7s}")

        manifest["results"].append({
            "cin": cin, "status": status,
            "years_found": found, "years_succeeded": years,
            "error": md.get("error"),
            "processing_seconds": secs, "file": out_path.name,
            "filing_seconds_total": round(sum(
                r["filing_seconds"] for r in file_rows
                if r["filing_seconds"] is not None), 2) if file_rows else None,
        })
        for r in file_rows:
            manifest["per_file_timings"].append({"cin": cin, **r})

    manifest.update({
        "finished": datetime.now().isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.time() - t0, 1),
        "succeeded": ok, "failed": failed, "skipped": skipped,
        "client_error": client_error,
    })
    # Slowest 10 individual filings across the whole run — the first place to
    # look when a batch takes longer than expected.
    manifest["slowest_files"] = sorted(
        (r for r in manifest["per_file_timings"] if r["filing_seconds"] is not None),
        key=lambda r: r["filing_seconds"], reverse=True)[:10]

    (out_dir / "_run_summary.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\ndone in {manifest['elapsed_seconds']}s  "
          f"success={ok}  failed={failed}  skipped={skipped}")
    print(f"per-CIN JSON in {out_dir}/, run summary in "
          f"{out_dir}/_run_summary.json")
    # Non-zero exit if anything failed, so a scheduler can notice.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

