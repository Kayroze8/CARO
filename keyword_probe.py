#!/usr/bin/env python3
"""
keyword_probe.py
=================
Run the keyword-signature clause locator (caro/annexure_keywords.py)
directly against a PDF and see what it recovers.

    python keyword_probe.py FILING.pdf
    python keyword_probe.py FILING.pdf --compare      # also run the two
                                                        # parsers already in
                                                        # the pipeline, side
                                                        # by side
    python keyword_probe.py *.pdf                      # summary table

This does NOT touch the main pipeline. It reuses caro_clause_probe.py's text
recovery (embedded text, OCR, and the vector-outline fallback) so a filing
that the pipeline currently mis-reads as near-empty is read correctly here
too — the point is to see what the KEYWORD LOCATOR does with good text, not
to re-diagnose text extraction.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = os.environ.get("CARO_PIPELINE_DIR") or str(Path(__file__).resolve().parent)
sys.path.insert(0, _ROOT)

from caro.logging_setup import configure_logging          # noqa: E402
from caro.annexure_keywords import extract_with_rescue, coverage_report  # noqa: E402
from caro.extract import detect_caro_order_year           # noqa: E402
from caro_clause_probe import recover_text                # noqa: E402


def probe(path: str, compare: bool = False) -> dict:
    name = Path(path).name
    print("=" * 78)
    print(name[:76])
    print("=" * 78)

    text, page_report = recover_text(path, verbose=True)
    ocred = sum(1 for _, m, _ in page_report if m.startswith("OCR"))
    print(f"\nTEXT RECOVERY   {len(text):,} chars  ({ocred} page(s) OCR'd)")

    order = detect_caro_order_year(text)
    print(f"CARO EDITION    {order or 'not detected (assuming 2020 for this probe)'}")
    if order is not None and int(order) < 2020:
        print("                NOTE: this locator's tables are CARO 2020 only; "
              "results on an earlier-edition filing are not meaningful.")

    units = extract_with_rescue(text)
    cov = coverage_report(units)

    print(f"\nPARSER + KEYWORD RESCUE   {cov['main_found']}/21 main   "
          f"{cov['sub_found']}/39 sub")
    if cov["main_missing"]:
        print(f"   missing main: {', '.join('(' + m + ')' for m in cov['main_missing'])}")
    if cov["sub_missing"]:
        print(f"   missing sub : {', '.join(cov['sub_missing'])}")

    print("\nPER CLAUSE")
    for u in units:
        have = {su["id"] for su in u.source_sub_units}
        subs_str = f"{len(have)}/{len(u.official_subs)}"
        preview = " ".join((u.own_text or "").split())[:64]
        print(f"   {u.main_ref:>8}  subs={subs_str:>5}  {preview}")

    if compare:
        from caro.annexure import parse_annexure
        from caro.annexure_unmarked import parse_unmarked_annexure
        marker_units = parse_annexure(text) or []
        try:
            unmarked_units = parse_unmarked_annexure(text) or []
        except Exception:
            unmarked_units = []
        best_existing = max(len(marker_units), len(unmarked_units))
        print("\nCOMPARISON")
        print(f"   marker-based alone      : {len(marker_units)} main clause(s)")
        print(f"   keyword-anchored alone  : {len(unmarked_units)} main clause(s)")
        print(f"   + this module's rescue  : {len(units)} main clause(s)"
              f"   ({len(units) - best_existing:+d} vs the better of the two above)")

    print()
    return {"file": name, "chars": len(text), "order": order,
            "main": cov["main_found"], "sub": cov["sub_found"],
            "missing_main": cov["main_missing"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--compare", action="store_true",
                    help="also run the two existing parsers for comparison")
    args = ap.parse_args()

    configure_logging(run_name="keyword_probe")
    rows = [probe(p, compare=args.compare) for p in args.pdfs]

    if len(rows) > 1:
        print("=" * 78)
        print("SUMMARY")
        print("=" * 78)
        print(f"{'file':<28} {'order':>6} {'main':>8} {'sub':>8}   missing")
        for r in rows:
            print(f"{r['file'][:28]:<28} {str(r['order']):>6} "
                  f"{r['main']:>5}/21 {r['sub']:>5}/39   "
                  f"{','.join(r['missing_main']) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
