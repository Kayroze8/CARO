#!/usr/bin/env python3
"""
caro_clause_probe.py
====================
Standalone diagnostic for CARO clause detection on a single PDF.

    python caro_clause_probe.py FILING.pdf
    python caro_clause_probe.py FILING.pdf --text        # dump recovered text
    python caro_clause_probe.py *.pdf                    # compare several

Run it directly on a filing to see, in one screen, how many of the official
clauses and sub-clauses were recovered and — when the answer is "not all" —
which stage lost them.

This file is deliberately SEPARATE from the pipeline and changes nothing in
it. It imports the pipeline's own annexure parser and clause catalogue, so
what it reports about clause detection is exactly what the pipeline would do.
The one thing it does differently is text recovery, for the reason below.

--------------------------------------------------------------------------
The defect this was written to diagnose
--------------------------------------------------------------------------
Some XBRL auditor's-report PDFs carry their text as VECTOR OUTLINES: the
glyphs were converted to drawn paths, so the page contains

    0 extractable characters, 0 images, and several thousand curves

pdfplumber correctly reports no text. The pipeline's OCR gate then declines
to rasterise, because it requires a sizeable IMAGE on the page before paying
for OCR — a guard that exists to stop it re-OCRing clean native pages. A
vector-outline page satisfies neither branch, so the whole document comes
back with ~5 characters and zero clauses, silently.

Measured on the supplied samples: three of five files extracted 3, 10 and 7
characters respectively. Rasterising those same pages and running OCR
recovers the full annexure text cleanly.

So this probe adds ONE rule to the page decision: a page with no usable text
and no image, but substantial drawn content, is rasterised too. Everything
downstream is the pipeline's own code.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import unicodedata
from pathlib import Path

# Import the pipeline itself. Run this from the caro_pipeline root, or set
# CARO_PIPELINE_DIR to point at it.
import os
_ROOT = os.environ.get("CARO_PIPELINE_DIR") or str(Path(__file__).resolve().parent)
sys.path.insert(0, _ROOT)

from caro.logging_setup import configure_logging          # noqa: E402
from caro.annexure import parse_annexure                  # noqa: E402
from caro.annexure_unmarked import parse_unmarked_annexure  # noqa: E402
from caro.extract import detect_caro_order_year           # noqa: E402
from caro.caro_structure import (                         # noqa: E402
    ROMAN_SEQUENCE, official_subclauses_for,
)

# How much of a page must be drawn content before a text-less, image-less
# page is treated as "there is something here, OCR it". Vector-outline pages
# carry thousands of curves; a genuinely blank separator page carries none.
MIN_VECTOR_OBJECTS = 200


# ---------------------------------------------------------------------------
# text recovery
# ---------------------------------------------------------------------------
def _ocr_page_image(img) -> str:
    import pytesseract
    return pytesseract.image_to_string(img, config="--psm 6") or ""


def recover_text(path: str, dpi: int = 200, verbose: bool = True) -> tuple:
    """Return (text, per_page_report).

    Per page: use the embedded text when there is enough of it, otherwise
    rasterise and OCR. The vector-outline case is what the pipeline currently
    misses, and each page's reason is reported so the cause is visible rather
    than inferred.
    """
    import pdfplumber
    from pdf2image import convert_from_path

    report, chunks = [], []
    with pdfplumber.open(path) as pdf:
        n_pages = len(pdf.pages)
        page_info = []
        for i, p in enumerate(pdf.pages):
            text = p.extract_text() or ""
            n_img = len(p.images or [])
            n_vec = len(p.curves or []) + len(p.rects or [])
            page_area = (float(p.width) * float(p.height)) or 1.0
            img_area = 0.0
            for im in (p.images or []):
                w = float(im.get("x1", 0)) - float(im.get("x0", 0))
                h = float(im.get("y1", 0)) - float(im.get("y0", 0))
                img_area += abs(w * h)
            page_info.append({
                "i": i, "text": text, "n_img": n_img, "n_vec": n_vec,
                "img_frac": img_area / page_area,
            })

    need_ocr = []
    for pi in page_info:
        has_text = len(pi["text"].strip()) >= 180
        if has_text:
            pi["mode"] = "embedded text"
            continue
        if pi["img_frac"] > 0.20:
            pi["mode"] = "OCR (scanned image)"
            need_ocr.append(pi["i"])
        elif pi["n_vec"] >= MIN_VECTOR_OBJECTS:
            # THE CASE THE PIPELINE MISSES: text drawn as vector outlines.
            pi["mode"] = f"OCR (vector outlines, {pi['n_vec']} objects)"
            need_ocr.append(pi["i"])
        else:
            pi["mode"] = "blank / no content"

    ocr_text = {}
    if need_ocr:
        if verbose:
            print(f"  rasterising {len(need_ocr)} of {n_pages} page(s) at {dpi} DPI…",
                  file=sys.stderr)
        for idx in need_ocr:
            imgs = convert_from_path(path, dpi=dpi,
                                     first_page=idx + 1, last_page=idx + 1)
            ocr_text[idx] = _ocr_page_image(imgs[0]) if imgs else ""

    for pi in page_info:
        chunks.append(ocr_text.get(pi["i"], pi["text"]))
        report.append((pi["i"] + 1, pi["mode"],
                       len((ocr_text.get(pi["i"], pi["text"]) or "").strip())))

    return unicodedata.normalize("NFKC", "\n".join(chunks)), report


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def expected_totals(order_year) -> tuple:
    """(main clauses, sub-clauses) the applicable Order defines."""
    if order_year is not None and int(order_year) < 2020:
        from caro.caro_structure_2016 import (
            ROMAN_SEQUENCE_2016, official_subclauses_for_2016)
        return (len(ROMAN_SEQUENCE_2016),
                sum(len(official_subclauses_for_2016(r))
                    for r in ROMAN_SEQUENCE_2016))
    return (len(ROMAN_SEQUENCE),
            sum(len(official_subclauses_for(r)) for r in ROMAN_SEQUENCE))


def probe(path: str, show_text: bool = False, dpi: int = 200) -> dict:
    name = Path(path).name
    print("=" * 78)
    print(name[:76])
    print("=" * 78)

    text, page_report = recover_text(path, dpi=dpi)
    embedded = sum(1 for _, m, _ in page_report if m == "embedded text")
    ocred = sum(1 for _, m, _ in page_report if m.startswith("OCR"))
    blank = sum(1 for _, m, _ in page_report if m.startswith("blank"))

    print(f"\nTEXT RECOVERY   {len(text):,} chars  "
          f"({embedded} page(s) embedded, {ocred} OCR'd, {blank} blank)")
    for pno, mode, chars in page_report:
        print(f"   page {pno:>3}  {mode:<38} {chars:>6} chars")

    order = detect_caro_order_year(text)
    exp_main, exp_sub = expected_totals(order)

    # Replicate the pipeline's parser selection, so the count reported here is
    # the count the pipeline would produce. The marker-based parser runs
    # first; when it recovers implausibly few clauses (a real annexure carries
    # up to 21), the keyword-anchored parser competes and the richer result
    # wins. Which one won is reported, because it points straight at the
    # numbering convention the filing uses.
    marker_units = parse_annexure(text) or []
    unmarked_units = []
    if len(marker_units) < 8:
        try:
            unmarked_units = parse_unmarked_annexure(text) or []
        except Exception as e:
            print(f"   (unmarked parser raised {type(e).__name__}: {e})")
    units = (unmarked_units if len(unmarked_units) > len(marker_units)
             else marker_units)
    winner = ("marker-based" if units is marker_units else "keyword-anchored")

    # The parsers attach the CARO 2020 catalogue by default, because they run
    # before the Order edition is known. extract_document() re-attaches the
    # correct one afterwards; do the same here or a CARO 2016 filing reports
    # 2020's 39 sub-clauses and the totals look wrong.
    if order is not None and int(order) < 2020:
        from caro.caro_structure import catalogue_for_order
        seq, titles, high_impact, subs_for = catalogue_for_order(order)
        kept = []
        for u in units:
            if u.main_id not in set(seq):
                continue
            u.title = titles.get(u.main_id, u.title)
            u.official_subs = subs_for(u.main_id)
            kept.append(u)
        units = kept

    got_main = len(units)
    got_sub = sum(len(u.official_subs) for u in units)

    print(f"\nCARO EDITION    {order or 'not detected'}"
          f"   (expects {exp_main} main + {exp_sub} sub = {exp_main + exp_sub})")
    print(f"CLAUSES FOUND   {got_main} main + {got_sub} sub = {got_main + got_sub}"
          f"   [parser: {winner}]")
    print(f"                  marker-based={len(marker_units)}  "
          f"keyword-anchored={len(unmarked_units)}")

    found = {u.main_id for u in units}
    seq = ROMAN_SEQUENCE
    if order is not None and int(order) < 2020:
        from caro.caro_structure_2016 import ROMAN_SEQUENCE_2016 as seq
    missing = [r for r in seq if r not in found]

    if missing:
        print(f"MISSING         {', '.join('(' + m + ')' for m in missing)}")
    else:
        print("MISSING         none — all main clauses recovered")

    print("\nPER CLAUSE")
    for u in units:
        head = re.sub(r"\s+", " ", (u.full_text or ""))[:58]
        print(f"   {u.main_ref:>8}  subs={len(u.official_subs):>2}  {head}")

    if show_text:
        out = Path(path).with_suffix(".recovered.txt")
        out.write_text(text, encoding="utf-8")
        print(f"\nrecovered text written to {out.name}")

    print()
    return {"file": name, "chars": len(text), "order": order, "parser": winner,
            "main": got_main, "sub": got_sub,
            "expected_main": exp_main, "expected_sub": exp_sub,
            "missing": missing, "ocr_pages": ocred}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--text", action="store_true",
                    help="also write the recovered text next to each PDF")
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args()

    configure_logging(run_name="clause_probe")
    rows = [probe(p, show_text=args.text, dpi=args.dpi) for p in args.pdfs]

    if len(rows) > 1:
        print("=" * 78)
        print("SUMMARY")
        print("=" * 78)
        print(f"{'file':<26} {'order':>6} {'chars':>8} {'found':>9} {'expected':>9}  status")
        for r in rows:
            got = f"{r['main']}+{r['sub']}"
            exp = f"{r['expected_main']}+{r['expected_sub']}"
            ok = "COMPLETE" if not r["missing"] else f"missing {len(r['missing'])}"
            print(f"{r['file'][:26]:<26} {str(r['order']):>6} {r['chars']:>8,} "
                  f"{got:>9} {exp:>9}  {ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
