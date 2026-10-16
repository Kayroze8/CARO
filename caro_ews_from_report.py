"""
caro_ews_from_report.py
=======================
Pull the CARO trigger data (CARO_ews) out of a full company report JSON.

The CARO API's response gets stored inside the wider company report under

    m_output -> bir -> AUDIT_REPORT_v2

and downstream consumers want the triggers back in the same shape the
`/caro/triggers` endpoint returns, without calling the API again. That is
what `extract_caro_ews` does.

    from caro_ews_from_report import extract_caro_ews

    status, result, error_msg = extract_caro_ews("U15319DL1987PLC027698.json")
    if status == "Success":
        for filing in result:
            print(filing["source_pdf"], filing["worst_color_code"])
    else:
        print(error_msg)

Accepts an already-parsed dict, a JSON string, a file path, or an open file
object, so it drops into a pipeline at whatever point the data happens to be.

Why this file is defensive
--------------------------
Two things about real stored reports forced more care than a plain lookup:

1. `AUDIT_REPORT_v2` is sometimes written as a JSON OBJECT (`{...}`) when it
   holds what is really a LIST of filing entries. The first entry's fields
   land directly inside the object and every later entry follows as a bare
   `}, {` sibling, which is not valid JSON — `json.load` fails on the whole
   report. `load_report` repairs exactly that pattern rather than giving up,
   because the data itself is intact and recoverable.

2. `metadata` is frequently not stored alongside `result`. Everything here
   therefore degrades gracefully when it is missing, and the summary counts
   are recomputed from the clause data rather than read from a field that
   may not exist.

Neither is worked around silently: both are logged at WARNING level, and
`extract_caro_ews_detailed()` returns the same notes in a `warnings` list for
callers that need to act on them programmatically.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Optional, Union

logger = logging.getLogger(__name__)

__all__ = [
    "extract_caro_ews",
    "extract_caro_ews_detailed",
    "load_report",
    "iter_ews_clauses",
    "worst_color",
    "ReportFormatError",
]

# Severity order, worst first. Matches caro/views.py so a filing summarised
# here and one summarised by the API agree.
_COLOR_ORDER = ("red", "amber", "yellow", "green")

# The key the CARO response is stored under inside the wider report.
_REPORT_KEY = "AUDIT_REPORT_V2"

# Where to look for it. Path segments are matched case-insensitively (see
# _dig), so these cover m_output/M_OUTPUT, bir/BIR and AUDIT_REPORT_V2 vs
# AUDIT_REPORT_v2 without needing a variant for each combination — stored
# reports differ on all three. If none match, the whole document is searched
# for CARO_ews instead.
_DEFAULT_PATHS = (
    ("m_output", "bir", _REPORT_KEY),
    ("bir", _REPORT_KEY),
    (_REPORT_KEY,),
)

_FILING_ID_CIN = re.compile(r"^([A-Za-z0-9]+)_\d+$")


class ReportFormatError(ValueError):
    """The input could not be read as a company report JSON."""


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def load_report(source: Union[str, Path, dict, Any]) -> tuple:
    """Return (report_dict, warnings).

    Handles a dict (returned as-is), a Path or path-like string, a raw JSON
    string, or an open file object. Malformed `AUDIT_REPORT_v2` blocks are
    repaired; the warning list records it.
    """
    warnings: list = []

    if isinstance(source, dict):
        return source, warnings

    if hasattr(source, "read"):
        raw = source.read()
    elif isinstance(source, Path):
        raw = source.read_text(encoding="utf-8")
    elif isinstance(source, str):
        # A path if it looks like one and exists; otherwise treat as JSON text.
        looks_like_json = source.lstrip()[:1] in ("{", "[")
        if not looks_like_json:
            p = Path(source)
            if not p.exists():
                raise ReportFormatError(f"No such file: {source}")
            raw = p.read_text(encoding="utf-8")
        else:
            raw = source
    else:
        raise ReportFormatError(
            f"Unsupported input type {type(source).__name__}; pass a dict, "
            "path, JSON string, or file object.")

    try:
        return json.loads(raw), warnings
    except json.JSONDecodeError as first_error:
        repaired = _repair_object_written_as_list(raw)
        if repaired is None:
            raise ReportFormatError(
                f"Could not parse report JSON: {first_error}") from first_error
        report, note = repaired
        warnings.append(note)
        return report, warnings


def _repair_object_written_as_list(raw: str):
    """Repair `"KEY": { <entry0 fields>, }, { <entry1> }, ...`.

    The writer opened a list with `{` instead of `[`, so entry 0's closing
    brace terminates the key early and every later entry is a syntax error.
    The entries themselves are valid JSON objects, so they are decoded one at
    a time and re-emitted as a proper array.

    Returns (report_dict, warning) or None if the damage is something else.
    """
    for match in re.finditer(r'"(AUDIT_REPORT_V2)"', raw, re.IGNORECASE):
        key = match.group(1)
        pos = match.start()
        try:
            open_brace = raw.index("{", match.end())
        except ValueError:
            continue

        decoder = json.JSONDecoder()
        entries, cursor = [], open_brace
        try:
            while True:
                obj, end = decoder.raw_decode(raw, cursor)
                entries.append(obj)
                nxt = _skip_ws(raw, end)
                if nxt < len(raw) and raw[nxt] == ",":
                    after = _skip_ws(raw, nxt + 1)
                    if after < len(raw) and raw[after] == "{":
                        cursor = after
                        continue
                break
        except (json.JSONDecodeError, ValueError):
            continue

        if len(entries) < 2:
            # Only one entry: the object form parses fine on its own, so this
            # is not the bug we are looking for.
            continue

        # The same writer also drops the comma that should separate this key
        # from the next one — the last entry's brace was doing double duty as
        # both the entry's close and the block's close. Restore it when the
        # next token is another key rather than the end of the object.
        tail_at = _skip_ws(raw, end)
        separator = ""
        if tail_at < len(raw) and raw[tail_at] == '"':
            separator = ","

        patched = raw[:open_brace] + json.dumps(entries) + separator + raw[end:]
        try:
            report = json.loads(patched)
        except json.JSONDecodeError:
            continue
        return report, (
            f"{key} was stored as a JSON object but contains {len(entries)} "
            f"filing entries; parsed as a list. The writer should emit a JSON "
            f"array here — the stored report is not valid JSON as written.")
    return None


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    return i


# ---------------------------------------------------------------------------
# locating the CARO payload
# ---------------------------------------------------------------------------
def _dig(node: Any, path: Iterable[str]) -> tuple:
    """Walk `path` through nested dicts, matching keys case-insensitively.

    Returns (value, actual_path) so the caller can report the casing the
    report really used, or (None, None) if the path does not resolve.
    """
    actual = []
    for key in path:
        if not isinstance(node, dict):
            return None, None
        if key in node:                       # exact match first
            actual.append(key)
            node = node[key]
            continue
        lowered = key.lower()
        for k in node:
            if isinstance(k, str) and k.lower() == lowered:
                actual.append(k)
                node = node[k]
                break
        else:
            return None, None
    return node, actual


def _entries_from(node: Any) -> list:
    """Normalise whatever sits at AUDIT_REPORT_v2 into a list of filing dicts.

    Tolerates all the shapes seen in stored reports: the API envelope
    ({status, result, metadata}), a bare result list, a single filing dict,
    or a dict keyed by filing id.
    """
    if node is None:
        return []
    if isinstance(node, list):
        return [e for e in node if isinstance(e, dict)]
    if isinstance(node, dict):
        if isinstance(node.get("result"), list):
            return [e for e in node["result"] if isinstance(e, dict)]
        if "CARO_ews" in node or "CARO_clauses" in node or "filing_id" in node:
            return [node]
        # dict-of-filings keyed by id
        vals = [v for v in node.values() if isinstance(v, dict)]
        if vals and all(("CARO_ews" in v or "CARO_clauses" in v) for v in vals):
            return vals
    return []


def _find_anywhere(node: Any, found: list) -> None:
    """Depth-first search for any dict carrying CARO_ews.

    The fallback when the expected path is absent — report layouts vary, and
    an inability to find a well-known key should not mean losing data that is
    demonstrably present.
    """
    if isinstance(node, dict):
        if "CARO_ews" in node:
            found.append(node)
            return  # don't descend into the clause records themselves
        for v in node.values():
            _find_anywhere(v, found)
    elif isinstance(node, list):
        for v in node:
            _find_anywhere(v, found)


def _locate(report: dict, paths: Iterable[Iterable[str]]) -> tuple:
    for path in paths:
        node, actual = _dig(report, path)
        entries = _entries_from(node)
        if entries:
            return entries, "->".join(actual or path), []
    found: list = []
    _find_anywhere(report, found)
    if found:
        return found, None, [
            "AUDIT_REPORT_v2 not found at the expected path; located "
            f"{len(found)} filing entrie(s) by searching the document."]
    return [], None, []


# ---------------------------------------------------------------------------
# summarising
# ---------------------------------------------------------------------------
def worst_color(ews: list) -> Optional[str]:
    """Worst colour across a filing's clauses, or None if there are none."""
    seen = {(e.get("score_ready_fields") or {}).get("color_code")
            for e in ews or [] if isinstance(e, dict)}
    for c in _COLOR_ORDER:
        if c in seen:
            return c
    return None


def _red_flag_ids(ews: list, stored: Optional[list] = None) -> list:
    """Clause ids whose score_ready_fields.red_flag is true.

    `red_flag` can be absent from a stored report: the API omits it from the
    response when CARO_OMIT_ENABLED=true (see caro/views.py). It is NOT safe to
    infer it from color_code -- red also covers severity_hint == "high" -- so
    when the key is missing on every clause the ids the API computed BEFORE
    dropping it (`stored`, the filing's red_flag_clause_ids) are used instead.
    When red_flag is present it is used exactly as before.
    """
    ews = [e for e in ews or [] if isinstance(e, dict)]
    if stored is not None and ews and not any(
            "red_flag" in (e.get("score_ready_fields") or {}) for e in ews):
        return [i for i in stored if i]
    return [e["main_clause_id"] for e in ews
            if (e.get("score_ready_fields") or {}).get("red_flag")
            and e.get("main_clause_id")]


def iter_ews_clauses(triggers: dict):
    """Flatten the result into (source_pdf, clause_record) pairs.

    Convenience for callers that want every clause across every filing year
    without nesting two loops. Identified by source_pdf, which names the
    filing document the clause came from.
    """
    for filing in triggers.get("result", []) or []:
        pdf = filing.get("source_pdf")
        for clause in filing.get("CARO_ews", []) or []:
            yield pdf, clause


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------
def extract_caro_ews(
    source: Union[str, Path, dict, Any],
    cin: Optional[str] = None,
    paths: Optional[Iterable[Iterable[str]]] = None,
) -> tuple:
    """Extract the CARO trigger data from a full company report JSON.

        status, result, error_msg = extract_caro_ews("report.json")

    Returns a 3-tuple, matching the convention used by run.main():

        status     "Success" | "Failed"
        result     [ {source_pdf, CARO_ews,
                      worst_color_code, red_flag_clause_ids}, ... ]
        error_msg  None on success, otherwise why nothing was extracted

    `status` is "Failed" only when no trigger data could be found at all —
    an empty CARO_ews on a filing where CARO did not apply is a legitimate
    result, not a failure.

    Non-fatal problems (a repaired JSON block, a CIN that does not match the
    report it is stored in) do NOT set error_msg, because a caller testing
    `if error_msg:` would then treat a usable result as a failure. They are
    logged at WARNING level instead, and are available programmatically from
    extract_caro_ews_detailed() if a caller needs to act on them — the CIN
    mismatch in particular is worth checking, since it means the triggers may
    describe a different company.

    Args:
        source: dict, path, JSON string, or file object holding the report.
        cin:    expected CIN, cross-checked against the report.
        paths:  override the locations searched for the CARO payload.
    """
    detail = extract_caro_ews_detailed(source, cin=cin, paths=paths)
    return detail["status"], detail["result"], detail["error_msg"]


def extract_caro_ews_detailed(
    source: Union[str, Path, dict, Any],
    cin: Optional[str] = None,
    paths: Optional[Iterable[Iterable[str]]] = None,
) -> dict:
    """Same extraction, but returns everything that was learned along the way.

    Use this when the extra context matters:

        {status, result, error_msg, cin, years_found, years_succeeded,
         clause_count, worst_color_code, red_flag_clause_ids,
         source_path, warnings}
    """
    report, warnings = load_report(source)
    if not isinstance(report, dict):
        raise ReportFormatError(
            f"Expected a JSON object at the top level, got "
            f"{type(report).__name__}.")

    entries, found_at, locate_warnings = _locate(
        report, paths or _DEFAULT_PATHS)
    warnings.extend(locate_warnings)

    results, total_clauses = [], 0
    for entry in entries:
        ews = entry.get("CARO_ews")
        if not isinstance(ews, list):
            ews = []
        total_clauses += len(ews)
        results.append({
            "source_pdf": entry.get("source_pdf"),
            "Remarks": entry.get("Remarks"),        # null unless no XBRL / AOC-4 XBRL for the year
            "CARO_ews": ews,
            # Recomputed rather than copied: a stored summary can be stale if
            # the clause data was edited, and recomputing is cheap.
            "worst_color_code": worst_color(ews),
            "red_flag_clause_ids": _red_flag_ids(
                ews, entry.get("red_flag_clause_ids")
                if isinstance(entry.get("red_flag_clause_ids"), list) else None),
        })

    resolved_cin, cin_warning = _resolve_cin(report, entries, cin)
    if cin_warning:
        warnings.append(cin_warning)

    all_ews = [c for _, c in iter_ews_clauses({"result": results})]
    has_data = any(r["CARO_ews"] for r in results)

    # Surface non-fatal problems where an operator will actually see them.
    # They are deliberately kept out of error_msg (see extract_caro_ews).
    for w in warnings:
        logger.warning("%s", w)

    error_msg = None if results else (
        "No CARO_ews data found in this report. Checked "
        + ", ".join("->".join(p) for p in (paths or _DEFAULT_PATHS))
        + " and searched the whole document.")

    return {
        "status": "Success" if results else "Failed",
        "result": results,
        "error_msg": error_msg,
        # Everything below is extra context for callers that want it.
        "cin": resolved_cin,
        "years_found": len(results),
        "years_succeeded": sum(1 for r in results if r["CARO_ews"]),
        "clause_count": total_clauses,
        # Company-level roll-up across every filing year, so a caller can
        # colour a single row without walking the whole structure.
        "worst_color_code": worst_color(all_ews),
        "red_flag_clause_ids": sorted({i for r in results
                                       for i in r["red_flag_clause_ids"]}),
        "source_path": found_at,
        "warnings": warnings,
    }


def _resolve_cin(report: dict, entries: list, expected: Optional[str]) -> tuple:
    """Best-effort CIN, plus a warning when sources disagree."""
    overview = report.get("overview") if isinstance(report, dict) else None
    report_cin = None
    if isinstance(overview, dict):
        report_cin = overview.get("CIN") or overview.get("cin")

    # filing_id is not emitted in the output, but when the stored entries
    # carry one it is the only place the CARO run's own CIN appears — so it is
    # still read here to catch a report holding another company's data.
    filing_cin = None
    for e in entries:
        m = _FILING_ID_CIN.match(str(e.get("filing_id") or ""))
        if m:
            filing_cin = m.group(1)
            break

    warning = None
    if report_cin and filing_cin and report_cin.upper() != filing_cin.upper():
        # Worth surfacing loudly: it usually means the CARO block was copied
        # from another company's run, and the triggers do not describe the
        # company this report is about.
        warning = (
            f"CIN mismatch: report overview says {report_cin}, but the stored "
            f"CARO filings are for {filing_cin}. The trigger data may belong "
            f"to a different company.")

    resolved = expected or report_cin or filing_cin
    if expected and report_cin and expected.upper() != report_cin.upper():
        warning = ((warning + " ") if warning else "") + (
            f"Requested CIN {expected} does not match report CIN {report_cin}.")
    return resolved, warning


if __name__ == "__main__":  # simple CLI: python caro_ews_from_report.py report.json
    import sys
    if len(sys.argv) < 2:
        print("usage: python caro_ews_from_report.py <report.json>", file=sys.stderr)
        raise SystemExit(2)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    status, result, error_msg = extract_caro_ews(sys.argv[1])
    if status != "Success":
        print(error_msg, file=sys.stderr)
        raise SystemExit(1)
    try:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        print()
    except BrokenPipeError:
        # Normal when piping into head/less — exit quietly rather than dumping
        # a traceback the user did not cause.
        try:
            sys.stdout.close()
        except BrokenPipeError:
            pass
        raise SystemExit(0)