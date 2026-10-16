"""
Phase 2 (Option A: Call 1 extract and map -> Call 2 check and classify) offline checks.
PATCH 09-Oct-2026 (Phase 2, Option A).

    python tests/run_phase2_cases.py              # the offline checks below
    python tests/run_phase2_cases.py --replay ...  # plumbing replay on saved single-call runs (REPLAY MODE)

No model / GPU / network needed. Synthetic fixtures only (no client data), plus the
annexure texts already in the repository.

  A  CA clause map (caro/caro_clause_map.json): units, order, high-impact and red-flag lists
     equal the pipeline's catalogue; corrections to the Word file; subject hints
  B  rule registry: every paragraph of today's prompt placed in Call 1 / Call 2 / dropped
  C  output shapes of the two calls
  D  single-call request byte-identical to the 08-Oct golden request; OPS-1
  E  copy-loop detector
  F  every fallback path + the test mode (staged_nofallback) + old chat(system, user) clients
  G  end to end: final JSON keys identical to the single flow, strict schema, labels identical
     when the model answers the same, rules / roll-ups / amounts / flags still by code
  H  mapping check: moves applied only when verified; question + answer kept together
  J  a REAL filing with off-by-one printed numbering (Jsons Foundry): a correct model, and a
     model whose Call 1 maps by printed number (Call 2 must move the text back by subject)
  K  Remarks: only the existing Remarks (no new sentence)
  I  result cache per flow, perf step names, audit keys, switch default
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update(CARO_WRITE_FILES="false", CARO_PERF="false", CARO_AUDIT_ENABLED="false")
os.environ.pop("CARO_LLM_FLOW", None)
logging.disable(logging.CRITICAL)

from caro import pipeline as pl, extract as ex, llm, staged_llm as st   # noqa: E402
# v5 (14-Oct): the shipped config freezes the prompts (these switches OFF); the suite still proves each feature, so
# it runs with them ON and checks the shipped values separately (test V5-0).
_SHIPPED = dict(st.PATCH_SWITCHES)
_V5_FROZEN = ("AMT_PROMPT", "XWALK_LAYOUT", "SUBJ_2016", "ONE_UNIT", "P2_PROMPT", "P4", "P15", "P18", "P7B")
# v6 (14-Oct trial): every recheck note added after the 13-Oct run is OFF in the shipped code (they change the
# recheck request); the tests still run them ON so each note stays tested for a later live trial
_V6_FROZEN = ("F35", "P1", "P2_NOTE", "P6", "P7C", "P7D", "P7E", "P8", "P13", "P16", "GUARD_INCOMPLETE_NOTE")
for _k in _V5_FROZEN + _V6_FROZEN:
    st.PATCH_SWITCHES[_k] = True
# PATCH 15-Oct-2026 (v9): production is two-call only (V9_TWO_CALL_ONLY, shipped True). The suite still proves the
# single-call fallback machinery kept for rollback (F1-F6), so it runs with the switch OFF; V9-0 checks the shipped value.
st.PATCH_SWITCHES["V9_TWO_CALL_ONLY"] = False
from caro import post_rules as pr, mca_table as MT, audit, perf, result_cache          # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   <- {detail}"))


CALL1, CALL2 = st.CALL1_ROLE, st.CALL2_ROLE


def call2_payload(user):
    """The STEP 1 RESULT list inside a Call 2 user message."""
    start = user.index("):\n", user.index("STEP 1 RESULT")) + 3
    return json.JSONDecoder().raw_decode(user[start:])[0]


# ---------------------------------------------------------------------------
# REPLAY MODE -- run on the company machine, on saved single-call outputs:
#   python tests/run_phase2_cases.py --replay --text-dir logs/extracted_text \
#          --llm-dir logs/extracted_llm_out --out phase2_replay.csv
# For every filing with a text dump and a saved single-call reply:
#   SINGLE : the saved reply replayed through today's code (CARO_LLM_FLOW=single)
#   STAGED : the SAME model judgements handed to the REAL Option A code
#            (Call 1 answer = the saved reply's texts; Call 2 answer = "fits" + the saved
#             label, quote and explanation for every unit).
# Any difference is caused by the Option A plumbing, not by the model. Every difference is
# listed in the CSV. No model is called.
# ---------------------------------------------------------------------------
import argparse   # noqa: E402
import csv        # noqa: E402


class ReplayClient:
    """Answers the single call and Call 1 with the saved reply, and Call 2 with the saved
    reply's own judgements."""

    def __init__(self, raw, reask_raw):
        from caro.llm import parse_reply
        from caro.pipeline import _extract_records
        self.raw, self.reask_raw, self.n = raw, reask_raw, 0
        self.max_tokens = 8000
        self.units = {}
        try:
            recs = _extract_records(parse_reply(raw)[0])
            if reask_raw:
                from caro.pipeline import _merge_reask, _missing_units
                from caro.caro_structure import catalogue_for_order
                recs2 = _extract_records(parse_reply(reask_raw)[0])
                seq = {str(r.get("main_clause_id")).lower().strip("()") for r in recs}
                oy = 2020 if (seq & {"xvii", "xviii", "xix", "xx", "xxi"}) else 2016
                mm, ms = _missing_units(recs, catalogue_for_order(oy))
                _merge_reask(recs, recs2, mm, ms)
        except Exception:                                 # noqa: BLE001
            recs = []
        self.recs = recs
        from caro.post_rules import _unit_id
        for r in recs:
            if not isinstance(r, dict):
                continue
            mid = re.sub(r"[^a-z]", "", str(r.get("main_clause_id") or "").lower().replace("clause", ""))
            self.units.setdefault(mid, r)
            for s in r.get("sub_clauses") or []:
                if isinstance(s, dict):
                    self.units.setdefault(_unit_id(s, "sub", mid).lower(), s)

    def chat(self, system, user, schema=None):
        if system.startswith(CALL2):
            return self._call2(user)
        if system.startswith(CALL1):
            return json.dumps({"clauses": self.recs})
        self.n += 1
        return self.raw if self.n == 1 else (self.reask_raw or self.raw)

    def _ans(self, uid, key):
        r = self.units.get(uid.lower())
        if r is None:
            return {key: uid, "mapping": "no_text"}
        return {key: uid, "mapping": "fits", "move": [], "classification": r.get("classification"),
                "evidence_text": r.get("evidence_text") or "-", "explanation_text": r.get("explanation_text") or ""}

    def _call2(self, user):
        out = []
        for cl in call2_payload(user):
            subs = [self._ans(s["sub_clause_id"], "sub_clause_id") for s in cl.get("sub_clauses") or []]
            out.append(dict(self._ans(cl["main_clause_id"], "main_clause_id"), sub_clauses=subs))
        return json.dumps({"clauses": out, "unplaced": []})


def _rows(result):
    out = {}
    ews = {}
    for c in result["result"]["CARO_ews"]:
        ews[c["main_clause_id"]] = c["score_ready_fields"]
        for s in c["sub_clause_ews"]:
            ews[s["sub_clause_id"]] = s["score_ready_fields"]
    for c in result["result"]["CARO_clauses"]:
        for u, uid in [(c, c["main_clause_id"])] + [(s, s["sub_clause_id"]) for s in c["sub_clauses"]]:
            e = ews.get(uid, {})
            out[uid] = {"label": u["classification"], "amount": u["normalized_fields"].get("amount_total"),
                        "red": e.get("red_flag"), "review": e.get("review_required_flag"),
                        "source": u["normalized_fields"].get("source_text"), "explanation": u.get("explanation_text")}
    return out


def run_one(text_path: Path, llm_dir: Path):
    t = text_path.read_text(encoding="utf-8")
    head = t.split("\n", 1)[0] if t.startswith("###") else ""
    caro_text = t.split("\n", 2)[2] if t.startswith("###") else t
    stem = text_path.stem
    raw_p = llm_dir / f"{stem}.txt"
    if not raw_p.exists():
        return None
    raw = raw_p.read_text(encoding="utf-8")
    rk = llm_dir / f"{stem}__reask.txt"
    reask = rk.read_text(encoding="utf-8") if rk.exists() else None
    cin = stem.split("__")[0] if re.match(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}__", stem) else None
    m = re.search(r"FY:\s*(\d{4})-(\d{2})", head)
    fy_end = int(m.group(1)) + 1 if m else None
    ids = set(re.findall(r'"main_clause_id"\s*:\s*"\(?([ivx]+)', raw))
    oy = 2020 if ids & {"xvii", "xviii", "xix", "xx", "xxi"} else 2016
    kind = "xml_mode_b" if re.search(r"mode_b|xml_mode_b|xbrl keys", head, re.I) else None
    res = {}
    for flow in ("single", "staged"):
        os.environ["CARO_LLM_FLOW"] = flow
        doc = ex.ExtractedDocument(source_path=stem, company_name=stem, cin=cin,
                                   financial_year=f"31/03/{fy_end}" if fy_end else None, statement_type="standalone",
                                   caro_applicable=True, report_qualified=None, section_text="",
                                   explanatory_full_text=None, document_text="", caro_order_year=oy)
        doc.caro_text_source_kind = kind
        ct = pl._prepare_llm_input(doc, caro_text)
        ictx = pl._input_ctx(doc, ct)
        recs = pl.run_full_caro_extraction(ReplayClient(raw, reask), ct, stem, "", order_year=doc.caro_order_year,
                                           input_ctx=ictx)
        doc.llm_notes = ictx.get("llm_notes")
        res[flow] = (pl.assemble_document(doc, recs), ictx.get("llm_notes") or {})
    os.environ.pop("CARO_LLM_FLOW", None)
    return res


def replay_main(argv=None):
    ap = argparse.ArgumentParser(description="Phase 2 (Option A) plumbing replay on saved single-call replies")
    ap.add_argument("--text-dir", default="logs/extracted_text")
    ap.add_argument("--llm-dir", default="logs/extracted_llm_out")
    ap.add_argument("--out", default="phase2_replay.csv")
    a = ap.parse_args(argv)
    logging.disable(logging.CRITICAL)
    tdir, ldir = Path(a.text_dir), Path(a.llm_dir)
    rows, tot = [], {"filings": 0, "units": 0, "label": 0, "amount": 0, "red": 0, "review": 0, "fallback": 0,
                     "schema_failures": 0}
    for tp in sorted(tdir.glob("*.txt")):
        if "__stage" in tp.stem or "__reask" in tp.stem or "__attempt" in tp.stem or "__extraction" in tp.stem:
            continue
        try:
            res = run_one(tp, ldir)
        except Exception as e:                          # noqa: BLE001
            print(f"[skip] {tp.name}: {type(e).__name__}: {e}")
            continue
        if not res:
            continue
        tot["filings"] += 1
        (rs, _n1), (rt, nt) = res["single"], res["staged"]
        if nt.get("stage_fallback"):
            tot["fallback"] += 1
            print(f"[fallback] {tp.stem}: {nt['stage_fallback']}")
        from schema import Caro
        for item in rt["result"]["CARO_clauses"]:
            try:
                Caro.MainClause.model_validate(item)
            except Exception:                           # noqa: BLE001
                tot["schema_failures"] += 1
        a_, b_ = _rows(rs), _rows(rt)
        for uid in a_:
            tot["units"] += 1
            x, y = a_[uid], b_.get(uid, {})
            diff = [k for k in ("label", "amount", "red", "review") if x.get(k) != y.get(k)]
            for k in diff:
                tot[k] += 1
            if diff or x.get("source") != y.get("source"):
                rows.append({"filing": tp.stem, "unit": uid, "changed": "+".join(diff) or "source_text_only",
                             "single_label": x["label"], "staged_label": y.get("label"),
                             "single_amount": x["amount"], "staged_amount": y.get("amount"),
                             "single_red": x["red"], "staged_red": y.get("red"),
                             "single_review": x["review"], "staged_review": y.get("review"),
                             "single_source": (x["source"] or "")[:300], "staged_source": (y.get("source") or "")[:300],
                             "fallback": nt.get("stage_fallback") or ""})
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["filing"])
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(tot, indent=1))
    print(f"differences written to {a.out} ({len(rows)} rows; 'source_text_only' rows are display-only)")


if "--replay" in sys.argv:
    replay_main([x for x in sys.argv[1:] if x != "--replay"])
    sys.exit(0)


# ---------------------------------------------------------------------------
# V9 (15-Oct-2026): fixes 1-4. Real filing wording; every rule has a counter-case that must NOT change.
# Runs on its own with:  python tests/run_phase2_cases.py --v9-only
# ---------------------------------------------------------------------------
def v9_cases():
    import os as _os
    from caro import post_rules as P9, pipeline as PL9, staged_llm as S9
    sw = dict(S9.PATCH_SWITCHES)
    for k in sw:
        if k.startswith("V9_"):
            S9.PATCH_SWITCHES[k] = True

    check("V9-0 shipped: every V9 switch is ON (two-call only, fixes 1-4)",
          all(_SHIPPED.get(k) is True for k in _SHIPPED if k.startswith("V9_")) and "V9_TWO_CALL_ONLY" in _SHIPPED,
          {k: _SHIPPED.get(k) for k in _SHIPPED if k.startswith("V9_")})
    saved_env = _os.environ.get("CARO_LLM_FLOW")
    got = {}
    for v in (None, "staged", "staged_nofallback", "single", "xyz"):
        _os.environ.pop("CARO_LLM_FLOW", None)
        if v:
            _os.environ["CARO_LLM_FLOW"] = v
        got[v] = S9.llm_flow()
    if saved_env is None:
        _os.environ.pop("CARO_LLM_FLOW", None)
    else:
        _os.environ["CARO_LLM_FLOW"] = saved_env
    check("V9-1 two-call only: unset / staged / unknown -> no fallback; single only when asked (rollback)",
          got == {None: "staged_nofallback", "staged": "staged_nofallback", "staged_nofallback": "staged_nofallback",
                  "single": "single", "xyz": "staged_nofallback"}, got)

    # ---- Fix 1: printed question (Eleanor 2023-24 table layout)
    qa_w = ("ix (b) Whether the company is a declared wilful defaulter by any bank or financial institution or other lender; "
            "According to the information and explanations given to us and on the basis of our examination of the records of "
            "the Company, the Company has not been declared a willful defaulter by any bank or financial institution or "
            "government or government authority.")
    qa_c = ("(xvii) Cash Losses Whether the company has incurred cash losses in the financial year and in the immediately "
            "preceding financial year, if so, state the amount of cash losses; The Company has not incurred cash losses in "
            "the current year and preceding financial year.")
    check("V9-2 Fix 1: the question sentence alone used to trigger (wilful defaulter, cash loss)",
          bool(P9.R_WILFUL.hit(qa_w)) and bool(P9.R_CASH_LOSS.hit(qa_c)))
    check("V9-3 Fix 1: with the question stripped neither triggers",
          not P9.R_WILFUL.hit(P9.strip_printed_questions(qa_w)) and not P9.R_CASH_LOSS.hit(P9.strip_printed_questions(qa_c)))
    rec = {"main_clause_id": "ix", "sub_clauses": [{"sub_clause_id": "ix_b", "classification": "clean", "_staged": True,
           "evidence_text": qa_w, "normalized_fields": {"source_text": qa_w}}]}
    P9.apply_adverse_triggers([rec], 2020, {"ix"})
    check("V9-4 Fix 1 end to end: Eleanor (ix)(b) stays Favourable", rec["sub_clauses"][0]["classification"] == "clean",
          rec["sub_clauses"][0].get("severity_escalated_by_rule"))
    real_ans = ("(b) Whether the dues have not been deposited on account of dispute; The following dues have not been "
                "deposited on account of dispute: Income Tax Act, Income tax demand Rs. 2,81,900, AY 2018-19, Forum where "
                "dispute is pending: CIT (Appeals).")
    check("V9-5 Fix 1 counter-case: a real disputed-dues answer after the question still triggers (Atharv layout)",
          bool(P9.R_DISPUTED.hit(P9.strip_printed_questions(real_ans))))
    we = "We have examined whether the company has incurred cash losses of Rs. 12,40,000 during the year."
    check("V9-6 Fix 1 counter-case: an auditor's own 'we ... whether' sentence is not stripped",
          "12,40,000" in P9.strip_printed_questions(we))

    # ---- Fix 2: (vii) passage (Committed Ispat 2022-23; Starlite 2020-21 style; K.R. Foods / Rama counter-cases)
    committed = ("vii.\n\na) According to the information and explanations given to us and the records of the company examined "
                 "by us, in our opinion, the undisputed statutory dues including income-tax, goods and services Tax, and any "
                 "other statutory dues, as applicable, have generally been regularly deposited with the appropriate "
                 "authorities.\n\nb) According to the information and explanations given to us, there were following amounts "
                 "payable in respect thereof were in arrears as at the last day of the financial year for a period of more "
                 "than six months from the date they become payable.\n\nAccording to the information and explanations given "
                 "to us and the records of the company examined by us, there are no statutory dues relating to GST, Provident "
                 "Fund, Employees State Insurance, income-Tax, Duty of Customs or Cess or other statutory dues, which have not "
                 "been deposited with the appropriate authorities on account of any dispute.\n\nS. No.\n\nYear\n\nAmount "
                 "Outstanding as on 31.3.2023\n\n1.\n\nAY-2007-08\n\n33,83,106/-\n\n2.\n\nAY-2011-12\n\n69,290/-\n\n"
                 "(viii) In our opinion there are no transactions not recorded in books of accounts which have been "
                 "surrendered or disclosed as income during the year.")
    a_txt = committed[committed.find("a) According"):committed.find("b) According")].strip()
    b_txt = committed[committed.find("According to the information and explanations given to us and the records of the company examined by us, there are no statutory"):committed.find("S. No.")].strip()

    def vii(a_cls, a_src, b_cls, b_src):
        return {"main_clause_id": "vii", "classification": "clean", "normalized_fields": {"source_text": a_src},
                "sub_clauses": [{"sub_clause_id": "vii_a", "classification": a_cls, "_staged": True, "evidence_text": a_src,
                                 "evidence_check": "valid", "normalized_fields": {"source_text": a_src}},
                                {"sub_clause_id": "vii_b", "classification": b_cls, "_staged": True, "evidence_text": b_src,
                                 "evidence_check": "valid", "normalized_fields": {"source_text": b_src}}]}
    r = vii("qualified", a_txt, "clean", b_txt)
    P9.apply_adverse_triggers([r], 2020, {"vii"}, caro_text=committed)
    check("V9-7 Fix 2: Committed 2022-23 (vii)(a) arrears listed under 'b)' -> Adverse",
          r["sub_clauses"][0]["classification"] == "adverse", r["sub_clauses"][0].get("v9_vii_passage"))
    check("V9-8 Fix 2: Committed 2022-23 (vii)(b) 'no disputed dues' stays Favourable",
          r["sub_clauses"][1]["classification"] == "clean", r["sub_clauses"][1].get("severity_escalated_by_rule"))
    starlite = ("(vii) (a) Undisputed statutory dues including Income-tax, Customs Duty, Goods and Service Tax, cess and other "
                "material statutory dues have generally been regularly deposited with the appropriate authorities.\n(b) "
                "According to the information and explanations given to us, no undisputed amounts payable in respect of "
                "income-tax, customs duty, TDS, Goods and Service Tax were in arrears as at 31 March 2021 for a period of "
                "more than six months from the date they became payable.\n(c) According to the information and explanations "
                "given to us, the dues which have not been deposited on account of any dispute are as follows:\nName of the "
                "statute Nature of dues Amount (Rs. in lakhs) Period Forum where the dispute is pending\nAPGST Act Sales tax "
                "24.27 1996-97 High Court of Telangana\n(viii) There are no transactions not recorded in the books of account.")
    sa = starlite[starlite.find("(a) Undisputed"):starlite.find("(b) According")].strip()
    sb = starlite[starlite.find("(b) According"):starlite.find("(c) According")].strip()
    r = vii("qualified", sa, "clean", sb)
    P9.apply_adverse_triggers([r], 2020, {"vii"}, caro_text=starlite)
    check("V9-9 Fix 2: Starlite 2020-21 disputed APGST table printed as '(c)' -> (vii)(b) Adverse",
          r["sub_clauses"][1]["classification"] == "adverse", r["sub_clauses"][1].get("v9_vii_passage"))
    check("V9-10 Fix 2: Starlite (vii)(a) 'no undisputed amounts ... in arrears' is not made Adverse",
          r["sub_clauses"][0]["classification"] == "qualified")
    krf = ("(vii) (a) The Company has been generally regular in depositing undisputed statutory dues with the appropriate "
           "authorities.\n\u2022 According to the information and explanations provided to us, no undisputed amounts payable in "
           "respect of the\nabove were outstanding, at the year end, for a period of more than six months\nfrom the date when "
           "they became payable except\nthese two disputes (VAT and CST payable of Rs.\n20,95,74,867 and Rs. 1,00,65,542 "
           "respectively) are pending before the\nappellate authority.\n(viii) There are no unrecorded transactions.")
    r = vii("clean", krf[:krf.find("\u2022")].strip(), "adverse", "VAT and CST pending before the appellate authority")
    P9.apply_adverse_triggers([r], 2020, {"vii"}, caro_text=krf)
    check("V9-11 Fix 2 counter-case: K.R. Foods 'no undisputed ... except these two disputes' -> (vii)(a) not Adverse",
          r["sub_clauses"][0]["classification"] == "clean", r["sub_clauses"][0].get("v9_vii_passage"))
    rama = ("(vii) a) The Company is regular in depositing undisputed statutory dues with a few delays.\n\nAND\n\nNo undisputed amounts\n"
            "payable in respect of provident fund, employees' state insurance, income tax, GST,\ncustoms duty, cess and any other "
            "material statutory dues applicable to it, were\noutstanding, at the year end, for a period of more than six months "
            "from the\ndate they became payable.\n\n(viii) There are no unrecorded transactions.")
    r = vii("qualified", "The Company is regular in depositing undisputed statutory dues with a few delays.", "clean", "")
    P9.apply_adverse_triggers([r], 2020, {"vii"}, caro_text=rama)
    check("V9-12 Fix 2 counter-case: Rama Cylinders wrapped 'No undisputed amounts ... outstanding' -> not Adverse",
          r["sub_clauses"][0]["classification"] == "qualified", r["sub_clauses"][0].get("v9_vii_passage"))

    # ---- Fix 3a-3d
    def one(main, uid, cls, src, oy=2020, caro=None):
        r = {"main_clause_id": main, "classification": cls, "normalized_fields": {"source_text": src},
             "sub_clauses": [{"sub_clause_id": uid, "classification": cls, "_staged": True,
                              "normalized_fields": {"source_text": src}}]}
        P9.apply_v9_rules([r], oy, caro)
        return r["sub_clauses"][0]["classification"]
    check("V9-13 Fix 3a: Mbook (i)(c) 'doesn't hold any immovable properties' -> NA",
          one("i", "i_c", "clean", "The Company doesn\u2019t hold any immovable properties. Hence reporting under clause 3(i)(c) "
                                   "of the Order is not applicable.") == "not_applicable")
    check("V9-14 Fix 3a counter-case: title deeds held in the company's name stays Favourable",
          one("i", "i_c", "clean", "The title deeds of all the immovable properties disclosed in the financial statements are "
                                   "held in the name of the company.") == "clean")
    hdr = ("the title deeds of all the immovable properties (other than properties where the company is the lessee and the "
           "lease agreements are duly executed in favour of the lessee) disclosed in the financial statements are held in the "
           "name of the company, except the following: Description of Property Gross carrying value Held in name of Whether "
           "promoter, director or their relative or employee Period held - indicate range, where appropriate Reason for not "
           "being held in name of company ")
    check("V9-15 Fix 3b: Whofers / Maa Kudargarhi empty 'Not Applicable' / 'NA' exception table -> Favourable (G2)",
          bool(P9._v7_g2("i_c", "QUAL", hdr + "Not Applicable", "", 2020)) and bool(P9._v7_g2("i_c", "QUAL", hdr + "NA", "", 2020)))
    check("V9-16 Fix 3b counter-case: Committed 2023-24 real property row stays Qualified",
          not P9._v7_g2("i_c", "QUAL", hdr + "Office at Anand Parbat 100.00 AGR Steel Strips Pvt Ltd No 2010 Registration under process", "", 2020))
    u29 = ("The aggregate amount of loans or advances in the nature of loans provided during the year, along with the balance "
           "outstanding as at the balance sheet date, has been disclosed in the financial statements.")
    check("V9-17 Fix 3c: U29119 (iii)(a)(B) amounts only 'disclosed in the financial statements' -> Qualified",
          one("iii", "iii_a_B", "clean", u29) == "qualified")
    check("V9-18 Fix 3c counter-case: (iii)(a)(B) with the amounts printed stays Favourable",
          one("iii", "iii_a_B", "clean", u29 + " Aggregate amount granted during the year Rs. 28,33,000; balance Rs. 1,12,45,000.") == "clean")
    apollo = ["a) The Company has not provided loans to others during the current financial year.",
              "f) The Company has not granted any loan or advance in the nature of loan during the year, which is repayable on "
              "demand or without specifying any terms or period of repayment."]
    aditya = ["(iii)(a) During the year, the Company has not provided loans, advances in the nature of loans, stood guarantee or "
              "provided security to companies, firms, Limited Liability Partnerships or any other parties."]
    check("V9-19 Fix 3d: Apollo 2021-22 has no whole-clause sentence (blanket NA must not apply)", not P9.v9_whole_clause(apollo))
    check("V9-20 Fix 3d counter-case: Aditya 2022-23 whole-clause sentence without 'any' still applies",
          bool(P9.v9_whole_clause(aditya)))
    rq = {"classification": "qualified", "explanation_text": "Qualified", "evidence_text": apollo[0], "score_ready_fields": {}}
    rs = {"classification": "qualified", "explanation_text": "Qualified",
          "evidence_text": "The management has not provided the stock statements to us.", "score_ready_fields": {}}
    check("V9-21 companion: 'has not provided loans' is not Auditor Unable to Verify; 'has not provided the statements to us' still is",
          not PL9._check_scope_limitation(rq) and PL9._check_scope_limitation(rs))

    # ---- Fix 4a: D1C ("generally regular", arrears unanswered anywhere in (vii))
    def vii_a(src, caro):
        r = {"main_clause_id": "vii", "classification": "clean", "normalized_fields": {"source_text": src},
             "sub_clauses": [{"sub_clause_id": "vii_a", "classification": "clean", "_staged": True,
                              "normalized_fields": {"source_text": src}}]}
        P9.apply_v9_rules([r], 2020, caro)
        return r["sub_clauses"][0]["classification"]
    viraj_ref = ("[MCA_BOX: Disclosure relating to regularity in payment of undisputed statutory dues] According to the "
                 "information and explanations given to us, amounts deducted / accrued in respect of undisputed statutory dues "
                 "including GST, Provident fund, Income-tax and other material statutory dues have generally been regularly "
                 "deposited with the appropriate authorities [MCA_BOX: Disclosure in auditors report relating to default in "
                 "repayment of financial dues] As per Attachment")
    check("V9-22 Fix 4a: Viraj Refinery 2024-25 'generally regularly deposited', arrears never answered -> Qualified",
          vii_a(viraj_ref[viraj_ref.find("According"):viraj_ref.find(" [MCA_BOX: Disclosure in auditors report relating to default")],
                viraj_ref) == "qualified")
    shripati = ("(vii) a) The Company has generally been regular in depositing undisputed dues, including Provident Fund, Income "
                "Tax and other material statutory dues as applicable to it with appropriate authorities.\nb) There were no "
                "undisputed amounts payable in respect of Provident Fund, Income Tax and other material statutory dues as "
                "applicable in arrears as at 31st March, 2021 for a period of more than six months from the date they became "
                "payable.\n(viii) There are no unrecorded transactions.")
    check("V9-23 Fix 4a counter-case: Shripati 2020-21 arrears answered under 'b)' -> stays Favourable",
          vii_a(shripati[shripati.find("a) The"):shripati.find("b) There")].strip(), shripati) == "clean")
    stray = ("vii. According to the information and explanations given to us, in respect of statutory dues:\nThe Company has "
             "generally been regular in depositing undisputed statutory dues, including Provident Fund, Income Tax, Goods and "
             "Service Tax and other material statutory dues applicable to it with the appropriate authorities.\nThere were no "
             "undisputed amounts payable in respect of Provident Fund, Employees'\n\nState Insurance, Income Tax, Goods and\n\n"
             "Service Tax. Customs Duty, Excise Duty, Cess and other material statutory dues in arrears as at March 31, 2024 for "
             "a period of more than six months from the date they became payable.\n(viii) There are no unrecorded transactions.")
    check("V9-24 Fix 4a counter-case: Viraj Canes 2023-24 answer with a stray full stop -> stays Favourable",
          vii_a("The Company has generally been regular in depositing undisputed statutory dues, including Provident Fund, "
                "Income Tax, Goods and Service Tax and other material statutory dues applicable to it with the appropriate "
                "authorities.", stray) == "clean")

    # ---- Fix 4b: delay made good -> Qualified; "has defaulted" stays Adverse
    ix_fav = ("The Company has not defaulted in the repayment of loans or other borrowings or in the payment of interest thereon "
              "to any lender except some delay.")
    check("V9-25 Fix 4b: Apollo 2024-25 'not defaulted ... except some delay' (model Favourable) -> Qualified",
          one("ix", "ix_a", "clean", ix_fav) == "qualified")
    dly = ("There has been a delay in repayment of interest of Rs. 2,30,000 to Ugro Capital, which was paid on March 29, 2025.")
    r = {"main_clause_id": "ix", "sub_clauses": [{"sub_clause_id": "ix_a", "classification": "qualified", "_staged": True,
         "evidence_text": dly, "normalized_fields": {"source_text": dly}}]}
    P9.apply_adverse_triggers([r], 2020, {"ix"})
    check("V9-26 Fix 4b: a delay that was paid is not escalated to Adverse",
          r["sub_clauses"][0]["classification"] == "qualified", r["sub_clauses"][0].get("severity_escalated_by_rule"))
    hard = ("The Company has defaulted in repayment of loans to the following lender: Bank of India, term loan Rs. 45,00,000, "
            "delay of 92 days, unpaid as at the balance sheet date.")
    r = {"main_clause_id": "ix", "sub_clauses": [{"sub_clause_id": "ix_a", "classification": "qualified", "_staged": True,
         "evidence_text": hard, "normalized_fields": {"source_text": hard}}]}
    P9.apply_adverse_triggers([r], 2020, {"ix"})
    check("V9-27 Fix 4b counter-case: 'has defaulted' with a delay table stays Adverse (3FATE / Viraj Refinery)",
          r["sub_clauses"][0]["classification"] == "adverse")

    S9.PATCH_SWITCHES.clear()
    S9.PATCH_SWITCHES.update(sw)


if "--v9-only" in sys.argv:
    logging.disable(logging.CRITICAL)
    v9_cases()
    _fail = [r for r in RESULTS if not r[1]] if RESULTS and isinstance(RESULTS[0], tuple) else []
    print(f"V9: {sum(1 for r in RESULTS if (r[1] if isinstance(r, tuple) else r))}/{len(RESULTS)} passed")
    sys.exit(1 if _fail else 0)
v9_cases()                       # the full suite runs the V9 checks too


# ---------------------------------------------------------------------------
# V10 (15-Oct-2026): production input / output. No label logic is touched (proved by replay).
# Runs on its own with:  python tests/run_phase2_cases.py --v10-only
# ---------------------------------------------------------------------------
def v10_cases():
    import os as _os, subprocess as _sp, tempfile as _tf, threading as _th, http.server as _hs, json as _js
    import urllib.request as _ur, urllib.error as _ue
    from pathlib import Path as _P
    from caro import logging_setup as LS, sources as SRC, llm as LLM
    keep = {k: _os.environ.get(k) for k in ("CARO_WRITE_FILES", "CARO_SAVE_ALL", "CIN_SOURCE", "CIN_FOLDER_PATH",
                                            "LLM_SERVER_RETRIES", "LLM_SERVER_RETRY_WAIT_S")
            + tuple(LS.SAVE_SWITCHES.values())}

    def env(**kv):
        for k in keep:
            _os.environ.pop(k, None)
        for k, v in kv.items():
            _os.environ[k] = v
    # ---- output switches
    env()
    check("V10-1 every optional output folder is OFF by default", not any(LS.save_enabled(k) for k in LS.SAVE_SWITCHES))
    env(CARO_SAVE_XML_OUTPUT="true")
    check("V10-2 one switch turns on only its own folder (xml_caro_output)",
          LS.save_enabled("xml_output") and not LS.save_enabled("audit"))
    env(CARO_SAVE_ALL="true")
    check("V10-3 CARO_SAVE_ALL=true turns every folder on (local debugging rerun)",
          all(LS.save_enabled(k) for k in LS.SAVE_SWITCHES))
    env(CARO_SAVE_ALL="true", CARO_WRITE_FILES="false")
    check("V10-4 CARO_WRITE_FILES=false still switches everything off", not any(LS.save_enabled(k) for k in LS.SAVE_SWITCHES))
    # ---- source
    env()
    try:
        SRC.resolve_source(None); ok = False
    except SRC.SourceNotConfiguredError:
        ok = True
    check("V10-5 no source configured -> clear error (never a silent S3 attempt)", ok)
    env(CIN_FOLDER_PATH="/x")
    check("V10-6 CIN_FOLDER_PATH set -> local; CIN_SOURCE=s3 only when asked",
          SRC.resolve_source(None) == "local" and SRC.resolve_source("s3") == "s3")
    with _tf.TemporaryDirectory() as td:
        r = _P(td)
        (r / "U11111MH2000PTC000001" / "Annual Returns and Balance Sheet eForms").mkdir(parents=True)
        (r / "grp" / "U22222MH2000PTC000002" / "Annual Returns and Balance Sheet eForms").mkdir(parents=True)
        (r / "a" / "b" / "c" / "d" / "U33333MH2000PTC000003").mkdir(parents=True)
        c1 = SRC._cin_candidates(r, "U11111MH2000PTC000001")
        c2 = SRC._cin_candidates(r, "u22222mh2000ptc000002")
        c3 = SRC._cin_candidates(r, "U33333MH2000PTC000003")
        check("V10-7 local layouts <root>/<CIN> and <root>/<group>/<CIN> found (case-insensitive); search stops at "
              "CIN_SEARCH_DEPTH (3)", len(c1) == 1 and len(c2) == 1 and c3 == [], (c1, c2, c3))
    code = ("import os,sys; os.environ['CIN_FOLDER_PATH']='/tmp'; sys.path.insert(0, %r); import caro.sources as s; "
            "s.resolve_source(None); print('boto3' in sys.modules)") % str(ROOT)
    out = _sp.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
    check("V10-8 local source does not load boto3 (no AWS package or keys needed on the server)", out == "False", out)
    # ---- one retry on model-server errors
    state = {"n": 0, "mode": "503_then_ok"}

    class H(_hs.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            state["n"] += 1
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            m = state["mode"]
            if m == "400" or (m == "503_then_ok" and state["n"] == 1) or m == "503":
                self.send_response(400 if m == "400" else 503); self.end_headers(); return
            body = _js.dumps({"ok": True}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(body)
    srv = _hs.HTTPServer(("127.0.0.1", 0), H)
    _th.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/x"
    env(LLM_SERVER_RETRY_WAIT_S="0")

    class C:
        pass

    def call(mode):
        state.update(n=0, mode=mode)
        c = C()
        try:
            return LLM.post_json(_ur.Request(url, data=b"{}", method="POST"), 5, c), c
        except Exception as e:                      # noqa: BLE001
            return e, c
    r1, c1 = call("503_then_ok")
    check("V10-9 HTTP 503 then OK -> one retry, the reply is used", r1 == {"ok": True} and c1.server_retries == 1
          and c1.calls == 2, (r1, vars(c1)))
    r2, c2 = call("503")
    check("V10-10 HTTP 503 twice -> exactly one retry, then the error is raised (filing reported Failed)",
          isinstance(r2, _ue.HTTPError) and c2.calls == 2, (r2, vars(c2)))
    r3, c3 = call("400")
    check("V10-11 HTTP 400 (a request problem, not the server) -> no retry", isinstance(r3, _ue.HTTPError) and c3.calls == 1)
    srv.shutdown(); srv.server_close()
    r4, c4 = call("ok")
    check("V10-12 server down -> one retry, then the error", isinstance(r4, (_ue.URLError, OSError))
          and not isinstance(r4, dict) and c4.calls == 2, (r4, vars(c4)))
    # ---- summary logging (fresh process: logging is configured once per process)
    with _tf.TemporaryDirectory() as td:
        code = ("import os,sys,logging; sys.path.insert(0,%r); os.environ['LOG_DIR']=%r; os.environ['LOG_STYLE']='summary';"
                "from caro import logging_setup as L; p=L.configure_logging('api');"
                "logging.getLogger('caro.pipeline').info('DETAIL LINE'); logging.getLogger('caro.pipeline').warning('WARN LINE');"
                "logging.getLogger('caro.summary').info('SUMMARY LINE'); logging.getLogger('caro.pipeline').error('ERROR LINE');"
                "print(L.warnings_count()); print(open(p).read())") % (str(ROOT), td)
        res = _sp.run([sys.executable, "-c", code], capture_output=True, text=True)
        log = res.stdout
        check("V10-13 LOG_STYLE=summary: the API log has the summary and errors only; warnings are counted",
              "SUMMARY LINE" in log and "ERROR LINE" in log and "DETAIL LINE" not in log and "WARN LINE" not in log
              and log.splitlines()[0] == "1", log[-400:])
        check("V10-14 LOG_STYLE=summary: the terminal shows the same (no detail lines, no extracted text)",
              "DETAIL LINE" not in res.stderr and "SUMMARY LINE" in res.stderr, res.stderr[-300:])
    # ---- per-CIN summary block
    from caro import cin_pipeline as CP
    import logging as _lg
    rec = []

    class _R(_lg.Handler):
        def emit(self, r):
            rec.append(r.getMessage())
    hd = _R(); _lg.getLogger("caro.summary").addHandler(hd); _lg.getLogger("caro.summary").setLevel(_lg.INFO)
    _was = _lg.root.manager.disable
    _lg.disable(_lg.NOTSET)
    fake = {"metadata": {"filings": [{"financial_year": "2023-24", "status": "Success", "caro_order_year": 2020,
                                      "company_name": "TEST CO"},
                                     {"financial_year": "2022-23", "status": "Failed", "error": "Call 2 cut off twice"}]},
            "result": [{"financial_year": "2023-24", "red_flag_clause_ids": ["vii"],
                        "CARO_clauses": [{"classification": "Adverse Remark"}, {"classification": "Qualified Remark"}],
                        "CARO_ews": [{"score_ready_fields": {"review_required_flag": True}, "sub_clause_ews": []}]},
                       {"financial_year": "2022-23", "CARO_clauses": [], "CARO_ews": []}]}
    CP._log_cin_summary("U00000MH2000PTC000009", fake, 125.0, 4, 1)
    _lg.getLogger("caro.summary").removeHandler(hd)
    _lg.disable(_was)
    txt = "\n".join(rec)
    check("V10-15 per-CIN summary: one line per year, red flags, result; at most 15 lines",
          len(rec) <= 15 and "ADV 1  QUAL 1" in txt and "FAILED" in txt and "Call 2 cut off" in txt and "(vii)" in txt
          and "PARTIAL 1/2" in txt and "server retries 1" in txt, txt)
    # ---- no debug printing in the production code path
    bad = []
    for f in ("sources.py", "cin_pipeline.py", "pipeline.py", "staged_llm.py", "post_rules.py", "llm.py", "audit.py",
              "runner.py", "extract.py", "xml_caro_extractor.py"):
        for i, line in enumerate((ROOT / "caro" / f).read_text(encoding="utf-8").splitlines(), 1):
            t = line.strip()
            if t.startswith("print(") and "file=sys.stderr" not in t:
                bad.append(f"{f}:{i}")
    check("V10-16 no print() in the production code path (nothing of the document reaches the terminal)", not bad, bad)
    env(**{k: v for k, v in keep.items() if v is not None})


if "--v10-only" in sys.argv:
    logging.disable(logging.CRITICAL)
    v10_cases()
if "--v10-only" not in sys.argv:
    v10_cases()                  # the full suite runs the V10 checks too


def v10_table_cases():
    """V10-17 ... V10-23: disputed_dues_table[].amount in absolute rupees (Toby 15-Oct-2026)."""
    from caro import amounts as AM

    def run(rows, text, items=None, total=None):
        rec = {"main_clause_id": "vii", "normalized_fields": {"disputed_dues_table": rows, "amount_total": total,
                                                              "source_text": text},
               "amount_calc": {"items": [{"as_printed": p, "rupees": v} for p, v in (items or [])]}, "sub_clauses": []}
        AM.absolute_table_amounts([rec], text)
        return [r.get("amount") for r in rec["normalized_fields"]["disputed_dues_table"]], rec
    apollo = ("(vii) b) The dues which have not been deposited on account of dispute are:\nName of the statute Nature of dues "
              "Forum where dispute is pending Period to which the amount relates Amount\n(Rs. In Lacs)\nIncome-tax Act, 1961 "
              "Income-tax Income Tax Appellate Tribunal Assessment Year 2016-17 22.95*\nIncome-tax Act, 1961 Income-tax "
              "CIT (Appeals) Assessment Year 2017-18 185.59*")
    got, _ = run([{"nature_of_dues": "Income-tax", "period": "Assessment Year 2016-17", "amount": "22.95*"},
                  {"nature_of_dues": "Income-tax", "period": "Assessment Year 2017-18", "amount": "185.59*"}],
                 apollo, [("22.95*", 2295000.0), ("185.59*", 18559000.0)], 20854000.0)
    check("V10-17 Apollo 2020-21: '22.95*' and '185.59*' under (Rs. In Lacs) -> 2295000.0 and 18559000.0", got == [2295000.0, 18559000.0], got)
    got, _ = run([{"amount": "22.95*"}, {"amount": "185.59*"}], apollo)
    check("V10-18 same rows with no amount_total working: the table heading alone gives the same values",
          got == [2295000.0, 18559000.0], got)
    got, _ = run([{"amount": "Rs. 20,95,74,867 and Rs. 1,00,65,542 respectively"}], "VAT and CST Rs. 20,95,74,867 and "
                 "Rs. 1,00,65,542 respectively are pending before the appellate authority")
    check("V10-19 K.R. Foods: two figures in one cell are added (VAT + CST = 21,96,40,409)", got == [219640409.0], got)
    got, _ = run([{"amount": "15,410/-"}, {"amount": "714,067"}, {"amount": "7383 Thousand"}], "statutory dues table")
    check("V10-20 figures in full rupees / with a unit word: '15,410/-', '714,067', '7383 Thousand'",
          got == [15410.0, 714067.0, 7383000.0], got)
    got, rec = run([{"amount": "2011-12, 2012-13"}, {"amount": "Rs. in lakhs"}, {"amount": "-"}], "disputed dues table")
    check("V10-21 Aditya 2023-24: a period or a heading in the amount column is never turned into money; kept as "
          "printed and sent for review", got == ["2011-12, 2012-13", "Rs. in lakhs", "-"]
          and "table_amount_unit_unclear" in rec.get("review_reasons", []), (got, rec.get("review_reasons")))
    got, rec = run([{"amount": "22.95"}], "disputed dues table with no unit heading")
    check("V10-22 a decimal figure with no unit anywhere is not guessed (kept, review) - same rule as amount_total",
          got == ["22.95"] and "table_amount_unit_unclear" in rec.get("review_reasons", []), got)
    check("V10-23 the heading 'Rs.( Lakh)' is a unit heading (K C India: 149.23 -> Rs 1,49,23,000)",
          AM.unit_heading("Act\nPeriod\nAmount\nRs.( Lakh)\nRemarks") == 100000.0
          and run([{"amount": "149.23"}], "following demand is payable on account of any disputes.\nAct\nPeriod\nAmount\n"
                  "Rs.( Lakh)\nRemarks\n\nThe Income Tax Act 1961\nAY 2018-19\n149.23\nAppeal filed before CIT(A)")[0] == [14923000.0])


if "--v10-only" in sys.argv:
    v10_table_cases()
    print(f"V10 (with tables): {sum(1 for r in RESULTS if r[1])}/{len(RESULTS)} passed")
    sys.exit(0 if all(r[1] for r in RESULTS) else 1)
v10_table_cases()


# ---------------------------------------------------------------------------
# fixtures (texts already in the repository + the two synthetic filings below)
# ---------------------------------------------------------------------------
def _payload_annexure():
    u = json.load(open(ROOT / "payload.json", encoding="utf-8"))["messages"][1]["content"]
    return u.split('"""', 2)[1].strip("\n")


# Two synthetic filings (not client data): a CARO 2020 narrative with findings, and an
# MCA 16-box table (Mode B tags, an "As per Attachment" box, a footnote, no (xix) box).
SYNTHETIC_2020 = """ANNEXURE 'A' TO THE INDEPENDENT AUDITOR'S REPORT
(Referred to in paragraph 1 under 'Report on Other Legal and Regulatory Requirements' section of our report of even date)
To the best of our information and according to the explanations provided to us by the Company and the books of account and records examined by us in the normal course of audit, we state that:
(i) (a) (A) The Company has maintained proper records showing full particulars, including quantitative details and situation of Property, Plant and Equipment.
(B) The Company does not have any intangible assets and hence reporting under clause 3(i)(a)(B) of the Order is not applicable.
(b) The Property, Plant and Equipment have been physically verified by the management at reasonable intervals and no material discrepancies were noticed on such verification.
(c) The title deeds of all the immovable properties are held in the name of the Company.
(d) The Company has not revalued its Property, Plant and Equipment during the year.
(e) No proceedings have been initiated or are pending against the Company for holding any benami property under the Benami Transactions (Prohibition) Act, 1988 and rules made thereunder.
(ii) (a) The inventory has been physically verified by the management during the year at reasonable intervals. No discrepancies of 10% or more in aggregate for each class of inventory were noticed.
(b) The Company has not been sanctioned working capital limits in excess of five crore rupees, in aggregate, from banks or financial institutions on the basis of security of current assets.
(iii) During the year, the company has provided loans and stood guarantee to companies as follows:
(a) (A) The aggregate amount of loans provided during the year to subsidiaries is Rs. 1,550.00 lakhs and the balance outstanding at the balance sheet date is Rs. 1,550.00 lakhs.
(B) The Company has not provided any loans or advances to parties other than subsidiaries, joint ventures and associates.
(b) In our opinion, the terms and conditions of the loans granted are not prejudicial to the interest of the Company.
(c) The schedule of repayment of principal and payment of interest has been stipulated and the repayments or receipts are regular.
(d) The following amounts are overdue for more than ninety days: principal Rs. 120.00 lakhs and interest Rs. 14.50 lakhs. Reasonable steps have been taken by the Company for recovery of the principal and interest.
(e) No loan granted which has fallen due during the year has been renewed or extended.
(f) The Company has not granted any loans either repayable on demand or without specifying any terms or period of repayment.
(iv) In our opinion, the Company has complied with the provisions of sections 185 and 186 of the Companies Act, 2013 in respect of the loans granted.
(v) The Company has not accepted any deposits or amounts which are deemed to be deposits.
(vi) The maintenance of cost records has not been specified by the Central Government under section 148(1) of the Act for the business activities carried out by the Company.
(vii) (a) The Company has generally been regular in depositing undisputed statutory dues including Goods and Services Tax, provident fund, income-tax and other statutory dues. The following undisputed amounts were outstanding for a period of more than six months from the date they became payable:
Name of the statute    Nature of dues    Amount (Rs. in lakhs)    Period
Income Tax Act, 1961    TDS    15.41    2022-23
(b) There are no dues of income tax or goods and services tax which have not been deposited on account of any dispute.
(viii) There were no transactions relating to previously unrecorded income that have been surrendered or disclosed as income in the tax assessments under the Income Tax Act, 1961.
(ix) (a) The Company has not defaulted in repayment of loans or other borrowings or in the payment of interest thereon to any lender.
(b) The Company has not been declared a wilful defaulter by any bank or financial institution or other lender.
(c) The term loans were applied for the purpose for which the loans were obtained.
(d) Funds raised on short term basis have not been utilised for long term purposes.
(x) (a) The Company has not raised moneys by way of initial public offer or further public offer during the year.
(b) The Company has not made any preferential allotment or private placement of shares or convertible debentures during the year.
(xi) (a) No fraud by the Company or any fraud on the Company has been noticed or reported during the year.
(b) No report under sub-section (12) of section 143 of the Companies Act has been filed in Form ADT-4.
(xii) The Company is not a Nidhi Company and hence reporting under clause 3(xii) of the Order is not applicable.
(xiii) The transactions with the related parties are in compliance with sections 177 and 188 of the Act and the details have been disclosed in the financial statements.
(xiv) (a) The Company has an internal audit system commensurate with the size and nature of its business.
(xv) The Company has not entered into any non-cash transactions with directors or persons connected with them.
(xvi) The Company is not required to be registered under section 45-IA of the Reserve Bank of India Act, 1934.
(xvii) The Company has incurred cash losses of Rs. 773.94 lakhs in the current financial year.
(xviii) There has been no resignation of the statutory auditors during the year.
(xix) On the basis of the financial ratios and ageing, we are unable to comment whether a material uncertainty exists as on the date of the audit report, as the management has not produced the relevant cash flow projections before us.
(xx) The provisions of section 135 of the Act are not applicable to the Company.
For ABC & Co.
Chartered Accountants
UDIN: 24000000ABCDEFG123
Place: Pune
Date: 01-09-2024
"""
SYNTHETIC_TABLE_2020 = """[MCA_BOX: Disclosure in auditors report relating to fixed assets]
The Company has maintained proper records showing full particulars of fixed assets. Title deeds of immovable properties are held in the name of the Company.

[MCA_BOX: Disclosure in auditors report relating to inventories]
Inventory has been physically verified at reasonable intervals and no material discrepancies were noticed.

[MCA_BOX: Disclosure in auditors report relating to loans]
The Company has not made any investments, provided guarantee or security or granted any loans or advances in the nature of loans, secured or unsecured, to companies, firms, limited liability partnerships or any other parties during the year.

[MCA_BOX: Disclosure in auditors report relating to compliance with Section 185 and 186 of Companies Act, 2013]
The Company has not given any loans, investments, guarantees or security covered under sections 185 and 186.

[MCA_BOX: Disclosure in auditors report relating to deposits accepted]
The Company has not accepted any deposits.

[MCA_BOX: Disclosure in auditors report relating to statutory dues]
The Company is regular in depositing undisputed statutory dues. There are no disputed dues.

[MCA_BOX: Disclosure in auditors report relating to default in repayment of financial dues]
As per Attachment

[MCA_BOX: Disclosure in auditors report relating to public offer and term loans used for purpose for which those were raised]
The Company has not raised any money by way of public offer. The Company has not raised any term loan during the year.

[MCA_BOX: Disclosure in auditors report relating to fraud by the company or on the company by its officers or its employees reported during period]
No fraud has been noticed or reported during the year.

[MCA_BOX: Disclosure in auditors report relating to managerial remuneration]
Not applicable

[MCA_BOX: Disclosure in auditors report relating to transactions with related parties]
Transactions with related parties comply with sections 177 and 188.

[MCA_BOX: Disclosure in auditors report relating to non-cash transactions with directors or persons connected with him]
The Company has not entered into any non-cash transactions with directors.
[Footnote] Refer note 26(o) of the financial statements.
"""
GC = ("These events indicate that a material uncertainty exists and the Company has prepared its accounts "
      "as not a going concern.")
FIX = {
    "viraj_2016": (_payload_annexure(), "U00000MH2000PTC000001", 2021, 2016, None, {}),
    "jsons_2020": (open(ROOT / "caro_section_debug_json_foundry.txt", encoding="utf-8").read(),
                   "U29299MH1985PTC035651", 2024, 2020, None, {}),
    "synthetic_2020": (SYNTHETIC_2020, "U00000MH2000PLC000002", 2024, 2020, None, {}),
    "table_2020": (SYNTHETIC_TABLE_2020, "U00000MH2000PTC000003", 2025, 2020, "xml_mode_b", {"main_report_text": GC}),
}


def prepared(name):
    text, cin, fy_end, oy, kind, side = FIX[name]
    doc = ex.ExtractedDocument(source_path=name, company_name=name, cin=cin, financial_year=f"31/03/{fy_end}",
                               statement_type="standalone", caro_applicable=True, report_qualified=None,
                               section_text="", explanatory_full_text=None, document_text=text,
                               caro_order_year=oy)
    doc.caro_text_source_kind = kind
    ct = pl._prepare_llm_input(doc, text, side=side)
    return doc, ct


def run_filing(name, client, flow="single"):
    old = os.environ.get("CARO_LLM_FLOW")
    os.environ["CARO_LLM_FLOW"] = flow
    try:
        doc, ct = prepared(name)
        ictx = pl._input_ctx(doc, ct)
        recs = pl.run_full_caro_extraction(client, ct, doc.company_name, "2023-24",
                                           order_year=doc.caro_order_year, input_ctx=ictx)
        doc.llm_notes = ictx.get("llm_notes")
        return pl.assemble_document(doc, recs), ictx.get("llm_notes") or {}, recs, ct
    finally:
        if old is None:
            os.environ.pop("CARO_LLM_FLOW", None)
        else:
            os.environ["CARO_LLM_FLOW"] = old


def flat(result):
    out = {}
    for c in result["result"]["CARO_clauses"]:
        out[c["main_clause_id"]] = c
        for s in c["sub_clauses"]:
            out[s["sub_clause_id"]] = s
    return out


def key_paths(o, p=""):
    """Every key path of a JSON document, list positions removed."""
    out = set()
    if isinstance(o, dict):
        for k, v in o.items():
            out.add(f"{p}.{k}")
            out |= key_paths(v, f"{p}.{k}")
    elif isinstance(o, list):
        for v in o:
            out |= key_paths(v, p + "[]")
    return out


def schema_problems(res):
    """The client parts of a filing result against the strict schema (schema.Caro, extra keys forbidden)."""
    from schema import Caro
    from pydantic import ValidationError
    errs = []
    for model, items in ((Caro.MainClause, res["result"]["CARO_clauses"]), (Caro.MainClauseEws, res["result"]["CARO_ews"])):
        for it in items:
            try:
                model.model_validate(it)
            except ValidationError as e:
                errs.append(str(e)[:200])
    return errs


# keys that today's schema already lists as optional, present only on some units
OPTIONAL = {".result.CARO_clauses[].comment", ".result.CARO_clauses[].sub_clauses[].comment"}


# ---------------------------------------------------------------------------
# A. CA clause map
# ---------------------------------------------------------------------------
from caro.caro_structure import catalogue_for_order   # noqa: E402

cm = st.clause_map()
for oy, key, idk in ((2020, "caro_2020_units", "unit_id"), (2016, "caro_2016_units", "unit_id_2016")):
    seq, _t, hi, subs_for = catalogue_for_order(oy)
    code = [x for m in seq for x in [m] + [s.id for s in subs_for(m)]]
    mp = [u[idk] for u in cm[key]]
    check(f"A1 clause map CARO {oy}: same units in the same order as the pipeline catalogue ({len(code)})",
          code == mp, set(code) ^ set(mp))
    hik = "high_impact" if oy == 2020 else "high_impact_2016"
    rfk = "red_flag_if_adverse" if oy == 2020 else "red_flag_if_adverse_2016"
    rf = pl._red_flag_categories(oy)
    mains = [u for u in cm[key] if u["level"] == "main"]
    check(f"A2 clause map CARO {oy}: high-impact and red-flag lists equal the code's",
          all(bool(u[hik]) == (u[idk] in hi) and bool(u[rfk]) == (u[idk] in rf) for u in mains))
check("A3 Call 1 / Call 2 unit list: 60 units CARO 2020, 24 units CARO 2016",
      len(st.catalogue_units(2020)) == 60 and len(st.catalogue_units(2016)) == 24)
cw = {c["printed_in_report"]: c["caro_2020_unit"] for c in cm["crosswalk_2016_layout"]}
check("A4 crosswalk: 2016 (iii)(a)/(b)/(c) -> 2020 (iii)(b)/(c)/(d) (letter shift); (viii) -> (ix)(a); "
      "(xiv) -> (x)(b); (xi) managerial remuneration -> none",
      cw["(iii)(a)"] == "(iii)(b)" and cw["(iii)(b)"] == "(iii)(c)" and cw["(iii)(c)"] == "(iii)(d)"
      and cw["(viii)"] == "(ix)(a)" and cw["(xiv)"] == "(x)(b)" and cw["(xi)"] == "none")
cor = {c["unit"]: c["correct"] for c in cm["corrections_vs_word_file"]}
check("A5 corrections to the Word file: (ix)(c) <- 2016 (ix) term loans, (x)(b) <- 2016 (xiv), "
      "(xii) has 3 sub-clauses (D06 + CA review)",
      "term-loan" in cor["(ix)(c)"] and "(xiv)" in cor["(x)(b)"] and cor["(xii)"].startswith("3 sub-clauses"))
check("A6 CA review recorded in the map (version, changes)",
      re.search(r"-ca\d+$", cm["version"]) and len(cm["ca_review"]["changes"]) >= 10)
U = {u["unit"]: u for u in st.catalogue_units(2020)}
hint_cases = [   # (unit the text was placed under, text, expect a hint) -- synthetic sentences
    ("i_b", "The stock of materials has been physically verified at reasonable intervals by the management.", True),
    ("i_b", "The Property, Plant and Equipment have been physically verified by the management.", False),
    ("iii_b", "The Company has complied with the provisions of sections 185 and 186 of the Act.", True),
    ("i_a_B", "The Company has not revalued its property, plant and equipment or intangible assets.", True),
    ("iii_c", "The amount overdue for more than ninety days is Rs. 12 lakhs.", True),
    ("ix_e", "The company has not raised loans on the pledge of securities held in its subsidiaries.", True),
    ("xxi", "The Company has complied with the Internal Audit.", True),
    ("xxi", "Annexure A to the Independent Auditor's Report on the standalone financial statements", True),
    ("xxi", "Reporting under clause 3(xxi) is not applicable to standalone financial statements.", False),
    ("ix_a", "The Company has been regular in repayment of dues to banks.", False),
    ("iv", "The Company has complied with sections 185 and 186.", False),
]
bad = [(u, t[:40]) for u, t, exp in hint_cases if bool(st.subject_hint(U[u], t)) != exp]
check("A7 code subject hints (CA review): inventory under (i)(b), s.185/186 under (iii)(b), revaluation under "
      "(i)(a)(B), overdue under (iii)(c), pledge under (ix)(e), internal audit / heading under (xxi) are flagged; "
      "correct placements are not", not bad, bad)

# ---------------------------------------------------------------------------
# B. rule registry
# ---------------------------------------------------------------------------
from caro.prompts import build_system_prompt   # noqa: E402
bad = []
for oy in (2016, 2020):
    for tbl in (False, True):
        for cw_ in (None, "2016", "hybrid"):
            for cal in ("true", "false"):
                for amt in ("v1", "v2"):
                    os.environ.update(CARO_PROMPT_CALIBRATION=cal, CARO_AMOUNT_RULES=amt)
                    c1, c2, dropped, unknown = st.staged_rule_blocks(oy, table_input=tbl, crosswalk_layout=cw_)
                    s1 = st.build_call1_system(oy, table_input=tbl, crosswalk_layout=cw_)
                    s2 = st.build_call2_system(oy, table_input=tbl, crosswalk_layout=cw_)
                    if unknown:
                        bad.append(("unknown paragraph", oy, tbl, cw_, unknown[0][:60]))
                    for par in c1:
                        if s1.count(par) != 1:
                            bad.append(("C1 count", oy, tbl, cw_, par[:60]))
                    for par in c2:
                        if s2.count(par) != 1:
                            bad.append(("C2 count", oy, tbl, cw_, par[:60]))
                    for par, why in dropped:
                        if not why:
                            bad.append(("drop without reason", par[:60]))
                    allp = [p for _s, p in st.rule_paragraphs(build_system_prompt(oy, table_input=tbl,
                                                                                  crosswalk_layout=cw_))]
                    if len(allp) != len(set(c1) | set(c2) | {d[0] for d in dropped}):
                        bad.append(("paragraph lost", oy, tbl, cw_))
for k in ("CARO_PROMPT_CALIBRATION", "CARO_AMOUNT_RULES"):
    os.environ.pop(k, None)
check("B1 every paragraph of today's prompt is in Call 1, Call 2 or dropped with a reason, each exactly once "
      "per call (all editions / input shapes / switches)", not bad, bad[:3])
s1, s2 = st.build_call1_system(2020), st.build_call2_system(2016)
check("B2 Call 2 carries the edition's own adverse-trigger table",
      "ADVERSE TRIGGERS (CARO 2016)" in s2 and "ADVERSE TRIGGERS (CARO 2020)" not in s2)
check("B3 Call 1 does not classify: no classification section, no trigger table, no scope-limitation rule",
      "ADVERSE TRIGGERS" not in s1 and "AUDITOR SCOPE LIMITATION" not in s1 and '"adverse" :' not in s1)
check("B4 both calls map by subject: the EXTRACT AND MAP rule and the CLAUSE MAP are in both",
      all("EXTRACT AND MAP" in x and "CLAUSE MAP" in x for x in (s1, st.build_call2_system(2020))))
s1x, s2x = st.build_call1_system(2022, crosswalk_layout="2016"), st.build_call2_system(2022, crosswalk_layout="2016")
check("B5 CARO 2016-layout report: both calls get the crosswalk (letter shift of (iii))",
      all("printed (iii)(a) [Terms not prejudicial] -> (iii)(b)" in x for x in (s1x, s2x)))
check("B6 both calls are told a printed question stays with its answer (M4) and headings are never evidence (M8)",
      all("M4." in x and "M8." in x for x in (s1, s2)) and "QUESTION + ANSWER STAY TOGETHER" in s1)

# ---------------------------------------------------------------------------
# C. output shapes
# ---------------------------------------------------------------------------
sch1, sch2 = st.call1_schema(), st.call2_schema()
check("C1 Call 1 shape: no classification field anywhere",
      "classification" not in json.dumps(sch1) and "source_text" in json.dumps(sch1))
check("C2 Call 2 shape: mapping (fits / move / no_text) + the five labels",
      sch2["properties"]["clauses"]["items"]["properties"]["mapping"]["enum"] == ["fits", "move", "no_text"]
      and set(sch2["properties"]["clauses"]["items"]["properties"]["classification"]["enum"])
      == {"clean", "qualified", "adverse", "not_applicable", "insufficient_evidence"})
dd = sch1["properties"]["clauses"]["items"]["properties"]["normalized_fields"]["properties"]["disputed_dues_table"]
check("C3 disputed dues use the schema's own field names",
      set(dd["items"]["properties"]) == {"nature_of_dues", "forum", "period", "amount"})


# ---------------------------------------------------------------------------
# D. llm.py request
# ---------------------------------------------------------------------------
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_sent = []
_real_urlopen = urllib.request.urlopen


def _fake_urlopen(req, timeout=None):
    if req.full_url.endswith("/models"):
        return _Resp(json.dumps({"data": [{"id": "m", "max_model_len": 200000}]}).encode())
    _sent.append(req.data)
    p = json.loads(req.data)
    return _Resp(json.dumps({"choices": [{"message": {"content": llm.MockClient().chat(
        p["messages"][0]["content"], p["messages"][1]["content"],
        schema=(p.get("guided_json") if p.get("guided_json") is not llm.BATCH_JSON_SCHEMA else None))}}]}).encode())


urllib.request.urlopen = _fake_urlopen
cwd = os.getcwd()
with tempfile.TemporaryDirectory() as tmp:
    os.chdir(tmp)
    c = llm.OpenAICompatClient(model="m", max_tokens=4096, guided_json=True)
    c.chat("SYS", "USER")
    p0 = json.loads(_sent[-1])
    c.chat("SYS", "USER", schema=sch1)
    p1 = json.loads(_sent[-1])
    os.environ["LLM_STRUCTURED_OUTPUT"] = "response_format"
    c.chat("SYS", "USER")
    p2 = json.loads(_sent[-1])
    c.chat("SYS", "USER", schema=sch1)
    p3 = json.loads(_sent[-1])
    os.environ.pop("LLM_STRUCTURED_OUTPUT")
    check("D1 schema=None sends today's BATCH_JSON_SCHEMA as guided_json", p0.get("guided_json") == llm.BATCH_JSON_SCHEMA)
    check("D2 a call schema replaces it only for that call", p1.get("guided_json") == sch1)
    check("D3 response_format keeps the name caro_clauses for the single call",
          p2["response_format"]["json_schema"]["name"] == "caro_clauses"
          and p3["response_format"]["json_schema"]["name"] == st.CALL1_TITLE)
    check("D4 OPS-1: no payload.json written by the client", not Path(tmp, "payload.json").exists())
    os.chdir(cwd)

# Golden request fingerprints of the SINGLE call for the four fixtures (code of
# 08-Oct-2026 incl. D06). Any change to the single-call request fails here (C1 / C-12).
# Refresh ONLY for an approved prompt / payload change: python tests/run_phase2_cases.py --print-golden
GOLDEN = {
    "viraj_2016": "309e90b8c71471dd7e3bd13e17227333683ea0c2c384f594ab97c21a21d4388c",
    "jsons_2020": "4ece8679eee96ca15c8b0c28f7ce9c658e39281388f9843adb24971ca3b07f3c",
    "synthetic_2020": "dfdcb9be61e37f67e36a05808cda6cca99c6f06ca2d35d6261967f61bc01e907",
    "table_2020": "35e9c13cfe5475b35306716c57d9731f8923dcb950e0d34adeb2b02e2d8340aa",
}
client = llm.OpenAICompatClient(model="m", max_tokens=4096, guided_json=True)
got_golden = {}
single_results = {}
for name in FIX:
    n0 = len(_sent)
    res, notes, _recs, _ct = run_filing(name, client, "single")
    single_results[name] = res
    got_golden[name] = hashlib.sha256(b"".join(_sent[n0:])).hexdigest()
    check(f"D5 {name}: single-call request byte-identical to the 08-Oct golden request",
          got_golden[name] == GOLDEN[name], got_golden[name])
    check(f"D6 {name}: single flow writes no staged notes to the audit file", "llm_flow" not in notes)
if "--print-golden" in sys.argv:
    print(json.dumps(got_golden, indent=1))

# ---------------------------------------------------------------------------
# E. repetition detector
# ---------------------------------------------------------------------------
loop = "Hence reporting under paragraph 3(viii) of the Order is not applicable to the company. " * 5
check("E1 a copy loop inside one field is caught", st.repeated_span(json.dumps({"units": [{"explanation": loop}]})))
check("E2 a runaway, invalid reply is caught", st.repeated_span('{"units": [{"note": "' + loop * 20))
same = "Not applicable: the auditor states the Company has not granted any loans, guarantees or security to anyone."
check("E3 the same short explanation on sibling units is not a loop",
      not st.repeated_span(json.dumps({"units": [{"explanation": same} for _ in range(5)]})))
check("E4 a normal Call 1 reply is not a loop",
      not st.repeated_span(llm.MockClient().chat(st.build_call1_system(2020), st.build_call1_user(
          FIX["synthetic_2020"][0], "x", "2023-24", 2020), schema=sch1)))


# ---------------------------------------------------------------------------
# F. fallback paths
# ---------------------------------------------------------------------------
class Scripted:
    """Answers Call 1 / Call 2 from scripts (else MockClient); the single call from MockClient."""

    def __init__(self, s1=None, s2=None, schema_arg=True):
        self.s1, self.s2, self.calls, self.users = list(s1 or []), list(s2 or []), [], []
        self.max_tokens = 8000
        self.m = llm.MockClient()
        if not schema_arg:
            self.chat = self._chat_old

    def chat(self, system, user, schema=None):
        return self._route(system, user, schema)

    def _chat_old(self, system, user):
        return self._route(system, user, None)

    def _route(self, system, user, schema):
        if system.startswith(CALL1):
            self.calls.append("C1")
            f = self.s1.pop(0) if self.s1 else None
        elif system.startswith(CALL2):
            self.calls.append("C2")
            self.users.append(user)
            f = self.s2.pop(0) if self.s2 else None
        else:
            self.calls.append("single")
            return self.m.chat(system, user)
        if f is None:
            return self.m.chat(system, user, schema=schema)
        if callable(f):
            return f(system, user, schema)
        return f


def boom(*a):
    raise RuntimeError("server down")


def drop_main(mid):
    def f(system, user, schema):
        r = json.loads(llm.MockClient().chat(system, user, schema=schema))
        r["clauses"] = [c for c in r["clauses"] if str(c.get("main_clause_id")).strip("()") != mid]
        return json.dumps(r)
    return f


def unlabelled(system, user, schema):
    r = json.loads(llm.MockClient().chat(system, user, schema=schema))
    for c in r["clauses"]:
        for s in c.get("sub_clauses") or []:
            s.pop("classification", None)
        c.pop("classification", None)
    return json.dumps(r)


cases = [
    ("F1 Call 1 raises -> single call", dict(s1=[boom]), "server down", ["C1", "single"]),
    ("F2 Call 1 invalid JSON twice -> single call", dict(s1=["{not json", "nope"]), "call 1", ["C1", "C1", "single"]),
    # PATCH 12-Oct-2026 (CUTOFF): repetition is no longer a reason to reject; an unreadable runaway reply with
    # no complete clause in it is still unusable -> after the second attempt the filing falls back as before
    ("F3 Call 1 runaway reply with no complete clause, twice -> single call",
     dict(s1=['{"clauses": [{"x": "' + loop * 20] * 2), "call 1", ["C1", "C1", "single"]),
    ("F4 Call 1 leaves out a main clause even after the follow-up -> single call",
     dict(s1=[drop_main("vii"), drop_main("vii")]), "missing after the follow-up", ["C1", "C1", "single"]),
    ("F5 Call 2 invalid JSON twice -> single call", dict(s2=["{bad", "bad"]), "call 2", ["C1", "C2", "C2", "single"]),
    ("F6 Call 2 leaves units with text unclassified after the follow-up -> single call",
     dict(s2=[unlabelled, unlabelled]), "not classified", ["C1", "C2", "C2", "single"]),
]
pure_single = run_filing("synthetic_2020", llm.MockClient(), "single")[0]
pure_single_f = flat(pure_single)
for title, kw, reason, calls in cases:
    cl = Scripted(**kw)
    res, notes, _r, _c = run_filing("synthetic_2020", cl, "staged")
    check(title, reason in str(notes.get("stage_fallback")) and cl.calls == calls,
          (notes.get("stage_fallback"), cl.calls))
    check(title.split(" ")[0] + " fallback result identical to a pure single run",
          json.dumps(res, sort_keys=True) == json.dumps(pure_single, sort_keys=True))
cl = Scripted(s1=[drop_main("vii"), drop_main("vii")])
res, notes, _r, _c = run_filing("synthetic_2020", cl, "staged_nofallback")
wf = " ".join(notes.get("staged", {}).get("would_fall_back", []))
check("F7 test mode (staged_nofallback): a missing main clause is only recorded as would_fall_back; never the "
      "single call; the clause still gets a record", "missing after the follow-up" in wf and "single" not in cl.calls
      and "vii" in flat(res), (wf, cl.calls))
cl = Scripted(s1=["{bad", "bad"] * 3)
res, notes, _r, _c = run_filing("synthetic_2020", cl, "staged_nofallback")
# PATCH 12-Oct-2026 (F02 / F25): no whole-flow repeats for the same failure; the filing is reported Failed,
# no label is invented, never the single call
check("F8 test mode: invalid JSON twice -> no repeat of the whole two-call flow, never the single call, "
      "the filing is Failed with no invented labels",
      cl.calls == ["C1"] * 2 and _r == [] and "test mode" in str(notes.get("llm_failed")),
      (cl.calls, len(_r or []), notes.get("llm_failed")))
# PATCH 13-Oct-2026 (Phase 2, NO_LABELS). 13-Oct rerun (Starlite, Cellecor, L36911KA1995PLC017077): with the schema
# enforced, Call 2 returned {"mapping", "sub_clause_id"} only; test mode showed every unit as Not Reported.
cl = Scripted(s2=[unlabelled, unlabelled])
res, notes, _r, _c = run_filing("synthetic_2020", cl, "staged_nofallback")
check("F8c (13-Oct rerun) test mode: Call 2 gives no labels, even after the follow-up -> the filing is Failed, "
      "never a page of Not Reported units",
      _r == [] and "not classified" in str(notes.get("llm_failed")) and "single" not in cl.calls,
      (cl.calls, len(_r or []), notes.get("llm_failed")))
import caro.staged_llm as _s2
_c2 = _s2.call2_schema()["properties"]["clauses"]["items"]
check("B15 (13-Oct rerun) the Call 2 schema requires label, quote and explanation for every main and sub-clause, "
      "in the order the prompt shows",
      all(k in _c2["required"] and k in _c2["properties"]["sub_clauses"]["items"]["required"]
          for k in ("mapping", "own_text_label", "classification", "evidence_text", "explanation_text"))
      and list(_c2["properties"])[:5] == ["main_clause_id", "mapping", "move", "own_text_label", "classification"],
      _c2["required"])
_doc8, _ct8 = prepared("synthetic_2020")
_doc8.llm_notes = notes
_fd = pl._llm_failed_document(_doc8)
check("F8b (F25, Cellecor / Siddhivinayak 11-Oct) a filing whose model replies cannot be used is 'Failed' with the "
      "reason and empty clause lists - never Favourable labels from the clause numbers",
      _fd["status"] == "Failed" and _fd["result"] == {"CARO_clauses": [], "CARO_ews": []} and "could not be used" in _fd["error"],
      _fd.get("status"))
cl = Scripted(schema_arg=False)
res, notes, _r, _c = run_filing("synthetic_2020", cl, "staged")
# PATCH 12-Oct-2026: the mock places (iii)(a)(A)'s "loans ... to subsidiaries" sentence on (iii)(a)(B) as well;
# the F16 note asks once more in the existing recheck (one extra Call 2)
_f9_rc = notes.get("staged", {}).get("call2", {}).get("recheck_fix_tags") or {}
check("F9 an old chat(system, user) client runs the two-call flow",
      (cl.calls == ["C1", "C2"] or (cl.calls == ["C1", "C2", "C2"] and set(_f9_rc) == {"iii_a_B"}))
      and not notes.get("stage_fallback"), (cl.calls, notes.get("stage_fallback"), _f9_rc))

# ---------------------------------------------------------------------------
# G. end to end (mock model answering the same way in both flows)
# ---------------------------------------------------------------------------
staged = {}
# PATCH 14-Oct-2026 (v7): G1-G7 prove the two-call PLUMBING adds nothing, so they run with the v7 convention rules off
# (v7 changes labels on purpose after the model; its effect on these fixtures is checked in V7-7 below).
_V7_SW = ("V7_G1", "V7_G2", "V7_G3", "V7_G4", "V7_G7", "V7_G8",
          # PATCH 15-Oct-2026 (v9): label-changing v9 rules are off for the plumbing checks too (checked in V9-*)
          "V9_QSTRIP", "V9_VII_PASSAGE", "V9_IC_NOPROP", "V9_G2_HEADER", "V9_D3_DISCLOSED", "V9_BLANKET_GATE",
          "V9_SCOPE_LOANS", "V9_D1C", "V9_DELAY_QUAL")
_v7_saved = {k: st.PATCH_SWITCHES[k] for k in _V7_SW}
st.PATCH_SWITCHES.update({k: False for k in _V7_SW})
for name in FIX:
    cl = Scripted()
    res, notes, recs, ct = run_filing(name, cl, "staged")
    staged[name] = (res, notes, recs, ct, cl)
    single = run_filing(name, llm.MockClient(), "single")[0]
    _p13 = bool(notes.get("staged", {}).get("call1", {}).get("p13_long_field_reask"))   # 13-Oct P13: one long-field re-ask
    _calls = [c for i, c in enumerate(cl.calls) if not (_p13 and c == "C1" and i == 1)]
    check(f"G1 {name}: two model calls (+ at most one re-read; + one Call-1 re-ask for a field over the P13 limit), "
          "no fallback", _calls[:2] == ["C1", "C2"] and len(_calls) <= 3 and not notes.get("stage_fallback"),
          (cl.calls, notes.get("stage_fallback")))
    kp_s, kp_1 = key_paths(res), key_paths(single)
    check(f"G2 {name}: final JSON has exactly the same keys and structure as the single flow",
          (kp_s ^ kp_1) <= OPTIONAL and list(flat(res)) == list(flat(single))
          and list(res) == list(single) and list(res["result"]) == list(single["result"]), kp_s ^ kp_1)
    check(f"G3 {name}: strict schema (schema.Caro, extra keys forbidden): 0 failures",
          not schema_problems(res), schema_problems(res)[:2])
    diff = {u: (a["classification"], flat(res)[u]["classification"]) for u, a in flat(single).items()
            if a["classification"] != flat(res)[u]["classification"]}
    check(f"G4 {name}: same model judgement -> same client labels as the single flow (plumbing adds nothing)",
          not diff, diff)
    norm = pr._norm(ct)
    odd = [u for u, v in flat(res).items()
           if not ((v["normalized_fields"].get("source_text") or "-") in ("-",)
                   or v["normalized_fields"]["source_text"].startswith("(from main clause)")
                   or pr.evidence_match(v["normalized_fields"]["source_text"], norm) == "valid"
                   or v["normalized_fields"]["source_text"] == flat(single)[u]["normalized_fields"].get("source_text"))]
    check(f"G5 {name}: every client source text is the auditor's exact words (or today's own text, e.g. the "
          "main-report going-concern sentence)", not odd, odd)
    check(f"G6 {name}: no internal key leaks into the client JSON",
          "_staged" not in json.dumps(res) and "staged_trace" not in json.dumps(res))
    check(f"G7 {name}: same Remarks as the single flow", res["result"].get("Remarks") == single["result"].get("Remarks")
          and res["metadata"].get("_source_remark") == single["metadata"].get("_source_remark"))
st.PATCH_SWITCHES.update(_v7_saved)
res, notes, recs, ct, cl = staged["synthetic_2020"]
f = flat(res)
check("G8 adverse unit keeps adverse_flag true in the client JSON",
      f["xvii"]["classification"] == "Adverse Remark" and f["xvii"]["normalized_fields"]["adverse_flag"] is True)
check("G9 amounts still computed by code from the figures as printed",
      f["xvii"]["normalized_fields"]["amount_total"] == 77394000.0, f["xvii"]["normalized_fields"]["amount_total"])
check("G10 existing rules still run (scope limitation -> Auditor Unable to Verify, by code)",
      f["xix"]["classification"] == "Auditor Unable to Verify")
check("G11 silent unit: 'Not Reported by Auditor' (by code), the standard sentence, never 'Not provided by company'",
      f["xi_c"]["classification"] == "Not Reported by Auditor"
      and "Not provided by company" not in json.dumps([u["explanation_text"] for u in f.values()]))
r_iii = [r for r in recs if r["main_clause_id"] == "iii"][0]
check("G12 a clause with sub-clauses keeps its WHOLE passage as source_text (the (iv) rule F10b reads it)",
      "provided loans and stood guarantee" in r_iii["normalized_fields"]["source_text"]
      and "overdue for more than ninety days" in r_iii["normalized_fields"]["source_text"])
check("G13 audit notes: llm_flow, Call 1 and Call 2 notes, labels",
      notes.get("llm_flow") == "staged" and {"call1", "call2", "labels"} <= set(notes.get("staged", {})))
p = call2_payload(cl.users[0])
check("G14 Call 2 gets the clause passage once and \"(same as passage)\" instead of repeating it",
      any(s.get("text") == "(same as passage)" for c in p for s in c.get("sub_clauses") or [])
      or all(c.get("passage") for c in p))
check("G15 Call 2 does not get the annexure again (only Call 1's placed text)",
      "FULL VERBATIM CARO ANNEXURE TEXT" not in cl.users[0])
rt = staged["table_2020"][0]
check("G16 MCA table: a clause with no box -> 'Not Available in Source' (by code), as in the single flow",
      flat(rt)["xvii"]["classification"] == "Not Available in Source", flat(rt)["xvii"]["classification"])

# ---------------------------------------------------------------------------
# H. mapping check (Call 2 moves) -- applied only when verified
# ---------------------------------------------------------------------------
IV = ("In our opinion, the Company has complied with the provisions of sections 185 and 186 of the Companies Act, "
      "2013 in respect of the loans granted.")
INV = ("The inventory has been physically verified by the management during the year at reasonable intervals.")


def c1_misplaced(system, user, schema):
    """Call 1 that maps by printed position: the (iv) sentence also under (iii)(b), and the inventory
    sentence under (i)(b) instead of the PPE sentence."""
    r = json.loads(llm.MockClient().chat(system, user, schema=schema))
    for c in r["clauses"]:
        for s in c.get("sub_clauses") or []:
            nf = s["normalized_fields"]
            if s["sub_clause_id"] == "iii_b":
                nf["source_text"] = (nf.get("source_text") or "") + " " + IV
            if s["sub_clause_id"] == "i_b":
                nf["source_text"], s["evidence_text"] = INV, INV
    return json.dumps(r)


def c2_moves(system, user, schema):
    r = json.loads(llm.MockClient().chat(system, user, schema=schema))
    for c in r["clauses"]:
        for s in c.get("sub_clauses") or []:
            if s["sub_clause_id"] == "iii_b":
                s["mapping"], s["move"] = "move", [{"to": "iv", "text": IV}]
            if s["sub_clause_id"] == "i_b":
                s.update(mapping="move", move=[{"to": "ii_a", "text": INV}], classification="insufficient_evidence",
                         evidence_text="-", explanation_text="")
            if s["sub_clause_id"] == "x_a":                       # words not in (x)(a)'s text: must be rejected
                s["mapping"], s["move"] = "move", [{"to": "xvii", "text": "The Company has incurred heavy losses."}]
            if s["sub_clause_id"] == "x_b":                       # unknown target: must be rejected
                s["mapping"], s["move"] = "move", [{"to": "zz", "text": s.get("evidence_text") or "x"}]
    return json.dumps(r)


cl = Scripted(s1=[c1_misplaced], s2=[c2_moves])
res, notes, recs, ct = run_filing("synthetic_2020", cl, "staged")
f = flat(res)
R = {r["main_clause_id"]: r for r in recs}
Rs = {s["sub_clause_id"]: s for r in recs for s in r["sub_clauses"]}
mv = {(m["from"], m["to"]): m["result"] for m in notes["staged"]["moves"]}
check("H1 the s.185/186 sentence placed under (iii)(b) is moved to (iv): gone from (iii)(b), present on (iv)",
      "185" not in Rs["iii_b"]["normalized_fields"]["source_text"] and "185" in R["iv"]["normalized_fields"]["source_text"]
      and mv.get(("iii_b", "iv", )) == "moved (exact)", (mv, Rs["iii_b"]["normalized_fields"]["source_text"][:120]))
check("H2 the inventory sentence placed under (i)(b) goes to (ii)(a); (i)(b) is left with no text -> "
      "'Not Reported by Auditor' (by code)",
      f["i_b"]["classification"] == "Not Reported by Auditor" and INV[:40] in Rs["ii_a"]["normalized_fields"]["source_text"],
      f["i_b"]["classification"])
check("H3 a move whose words are not in the unit's text is rejected (nothing changes)",
      mv.get(("x_a", "xvii")) == "rejected: words not in this unit's text")
check("H4 a move to an unknown unit is rejected", str(mv.get(("x_b", "?zz"))).startswith("rejected"), mv)
check("H5 every move is in the audit trace of both units",
      Rs["iii_b"]["staged_trace"].get("moved_out") and R["iv"]["staged_trace"].get("moved_in"))
check("H6 strict schema and same keys after moves", not schema_problems(res)
      and (key_paths(res) ^ key_paths(pure_single)) <= OPTIONAL)

# unit-level: unplaced text, amounts move with the text, a clause passage is never cut
cat20 = st.catalogue_units(2020)
units = {"vii": {**st._empty_unit(), "source": "Statutory dues. Disputed GST 0.52 crore before the Appellate Authority."},
         "vii_a": {**st._empty_unit(), "source": "Disputed GST 0.52 crore before the Appellate Authority.",
                   "amount_items": [{"as_printed": "0.52", "counts_toward_total": True, "note": "GST"}]},
         "vii_b": st._empty_unit()}
ans = {"vii_a": {"unit": "vii_a", "mapping": "move", "classification": "insufficient_evidence", "evidence": "",
                 "explanation": "", "moves": [{"to": "vii_b", "text": "Disputed GST 0.52 crore before the Appellate Authority."}]},
       "vii": {"unit": "vii", "mapping": "move", "classification": None, "evidence": "", "explanation": "",
               "moves": [{"to": "xvii", "text": "Statutory dues."}]}}
inp = ("Statutory dues. Disputed GST 0.52 crore before the Appellate Authority. "
       "The Company has incurred cash losses of Rs. 10 lakhs in the current year.")
up = ["The Company has incurred cash losses of Rs. 10 lakhs in the current year."]
nt = {}
st.apply_moves(units, ans, [{"id": "U1", "to": "xvii", "text": up[0]},
                            {"id": "U9", "to": "xviii", "text": "The statutory auditors resigned during the year."}],
               up, inp, cat20, nt)
res_m = {(m["from"], m["to"]): m["result"] for m in nt["moves"]}
check("H7 figures and disputed-dues rows printed inside the moved words move with them",
      units["vii_b"]["amount_items"] and not units["vii_a"]["amount_items"])
check("H8 a move never cuts the passage of a clause with sub-clauses (copied, passage kept)",
      res_m[("vii", "xvii")].startswith("copied") and "Statutory dues." in units["vii"]["source"])
check("H9 unplaced text is placed only when its words are in the input",
      res_m[("U1", "xvii")] == "placed" and res_m[("U9", "xviii")].startswith("rejected")
      and "cash losses" in units["xvii"]["source"])

units2 = {"iii_b": {**st._empty_unit(), "source": "The terms are not prejudicial. " + IV, "evidence": IV}}
ans2 = {"iii_b": {"unit": "iii_b", "mapping": "move", "classification": "clean", "evidence": IV, "explanation": "",
                  "moves": [{"to": "iv", "text": IV}]}}
st.apply_moves(units2, ans2, [], [], IV, cat20, {})
rb = st._unit_record("iii_b", "(iii)(b)", units2["iii_b"], ans2["iii_b"], main_level=False, no_text_expl="")
check("H9b a verdict whose quote is in words moved to another clause loses that quote (today's evidence check "
      "then withdraws it); the words stay only on the clause they answer",
      rb["evidence_text"] == "-" and "185" not in rb["normalized_fields"]["source_text"]
      and "185" in units2["iv"]["source"], rb)

# printed question + answer stay together (Atharv-type layout, synthetic text)
QA = """ANNEXURE A TO THE AUDITORS REPORT
(xii) Whether the Nidhi Company has complied with the Net Owned Funds to Deposits in the ratio of 1:20 to meet out the liability and whether the Nidhi Company is maintaining ten per cent unencumbered term deposits as specified in the Nidhi Rules, 2014 to meet out the liability?
Not Applicable
(xvii) Whether the company has incurred cash losses in the financial year and in the immediately preceding financial year, if so, state the amount of cash losses?
No, the company has not incurred any cash losses during the financial year and in the immediately preceding financial year.
"""
QA_XII = QA.split("\n")[1] + "\n" + QA.split("\n")[2]
QA_XVII = QA.split("\n")[3] + "\n" + QA.split("\n")[4]


def c1_qa(system, user, schema):
    """A Call 1 that follows 'question + answer together'."""
    out = []
    for m in catalogue_for_order(2020)[0]:
        subs = [s.id for s in catalogue_for_order(2020)[3](m)]
        txt = QA_XII if m == "xii" else (QA_XVII if m == "xvii" else "-")
        out.append({"main_clause_id": m, "evidence_text": "-", "normalized_fields": {"source_text": txt},
                    "sub_clauses": [{"sub_clause_id": s, "evidence_text": "-",
                                     "normalized_fields": {"source_text": txt}} for s in subs]})
    return json.dumps({"clauses": out})


def c2_qa(system, user, schema):
    p = call2_payload(user)
    out = []
    for c in p:
        if c["main_clause_id"] == "xii":
            subs = [{"sub_clause_id": s["sub_clause_id"], "mapping": "fits", "classification": "not_applicable",
                     "evidence_text": "Not Applicable", "explanation_text": "Not a Nidhi company."}
                    for s in c["sub_clauses"]]
            out.append({"main_clause_id": "xii", "mapping": "fits", "classification": "not_applicable",
                        "evidence_text": "Not Applicable", "explanation_text": "Not applicable.", "sub_clauses": subs})
        elif c["main_clause_id"] == "xvii":
            out.append({"main_clause_id": "xvii", "mapping": "fits", "classification": "clean",
                        "evidence_text": "No, the company has not incurred any cash losses",
                        "explanation_text": "No cash losses."})
        else:
            out.append({"main_clause_id": c["main_clause_id"], "mapping": "no_text",
                        "sub_clauses": [{"sub_clause_id": s["sub_clause_id"], "mapping": "no_text"}
                                        for s in c.get("sub_clauses") or []]})
    return json.dumps({"clauses": out, "unplaced": []})


FIX["qa_2020"] = (QA, "U00000MH2000PTC000004", 2024, 2020, None, {})
cl = Scripted(s1=[c1_qa], s2=[c2_qa])
res, notes, recs, ct = run_filing("qa_2020", cl, "staged")
p = {c["main_clause_id"]: c for c in call2_payload(cl.users[0])}
check("H10 a printed question reaches Call 2 together with its short answer (never dropped as 'a question')",
      "Whether the Nidhi Company" in p["xii"]["passage"] and "Not Applicable" in p["xii"]["passage"]
      and "Whether the company has incurred cash losses" in p["xvii"]["passage"])
fq = flat(res)
check("H11 ... so the bare 'Not Applicable' is read with its question: (xii)(a)-(c) Not Applicable; "
      "(xvii) 'No, ... not incurred any cash losses' Favourable",
      all(fq[u]["classification"] == "Not Applicable" for u in ("xii_a", "xii_b", "xii_c"))
      and fq["xvii"]["classification"] == "Favourable Remark", {u: fq[u]["classification"] for u in ("xii_a", "xvii")})
check("H12 nothing unplaced in a question-and-answer report (questions + answers covered)",
      not notes["staged"]["unplaced_text"], notes["staged"]["unplaced_text"])
del FIX["qa_2020"]


# ---------------------------------------------------------------------------
# J. a REAL filing with off-by-one printed numbering (Jsons Foundry FY 2023-24: its "viii)" is
# deposits (v), "x)" statutory dues (vii), "xi)" borrowings (ix), "xvi)" related parties (xiii),
# "xxii)" CSR (xx), "xxiii)" CFS (xxi)). GOLD_MAP = what a correct model places under each unit;
# GOLD_LABEL = the CA reading (label, scope flag, deciding snippets, figures as printed).
# ---------------------------------------------------------------------------
GOLD_MAP = {
    "i": ["In respect of its Property"],
    "i_a_A": ["a) Company has maintained records", "However, Company needs to maintain Fixed assets register",
              "The movement and use of plant"],
    "i_b": ["b) As informed to us, all the Property", "no material discrepancies were noticed on such physical",
            "It is not possible to us to verify all the Fixed Assets"],
    "i_c": ["c) As informed to us by the management, the title deeds", "It is the responsibility of management to confirm",
            "Management is advised to take required actions"],
    "i_d": ["d) Company has not revalued"], "i_e": ["e) No proceedings have been initiated"],
    "ii_a": ["In respect of its inventories", "Closing stock of inventory", "Valuation of Work in process",
             "We have relied upon the value of work", "The company is having the arrangement of physical",
             "inventory at reasonable intervals", "The value of inventories are certified", "We have relied upon the certification"],
    "ii_b": ["generally regular in filling quarterly statements", "The balances of receivables and payables",
             "With respect to working capital limits sanctioned", "The amounts of book debts, creditors",
             "The inventory and work in process mentioned in the quarterly"],
    "iii_a_B": ["On verification of relevant records, it is found that", "S Loan of advances given",
                "NO the year (in Rs)", "1 Badri Kedar Infra", "Power Control Electro", "3 Jsons Holding",
                "5 Suraj Engineering", "6 Maind Flow", "B) Following corporate guarantees", "MSEDCL Axis Bank"],
    "iii_b": ["The terms and conditions of grant of all loans"], "iii_c": ["repayment or receipts are regular"],
    "iv": ["A) In our opinion and according to information and explanation given to us, in respect of loans"],
    "v": ["viii) During the year the Company has accepted unsecured Loans", "Name PAN Amount of deposit",
          "S M Jadhav", "A M Jadhav", "S A Jadhav", "S S Jadhav", "Sarvesh Sanjay", "Suraj Sanjay", "Kajal Amey",
          "Globe Engineering", "Amenity Developers"],
    "vi": ["ix) The central Govt.", "The cost information can be derived", "Cost Audit for the financial year",
           "They have submitted their cost audit report"],
    "vii_a": ["x) a) According to the information", "There are no statutory undisputed dues outstanding",
              "no undisputed amounts payable in respect of provident"],
    "vii_b": ["b) According to the information and explanations given to us and the records of the company",
              "It is not possible for us to verify the disputes", "Hence we have relied upon the information given",
              "Name of the statute Nature of dues", "(Rs. in amount pending", "Crore) relates",
              "STATE GST OFFICE - SCN", "0.02 2017-18", "ZD270723066332E", "ZD2708230265483", "ZD270824046513A",
              "ZD2706241784054", "ZD2704240100910", "Period to", "STATE GST OFFICE -", "SANGLI 16.08",
              "SANGLI 27.06", "SANGLI 04.04"],
    "ix_a": ["xi) In our opinion", "The company has not defaulted in repayment"],
    "ix_c": ["The term loans availed by the company"], "ix_d": ["With respect to working capital loans"],
    "x_a": ["xii) The Company has not raised any money"], "xi_a": ["xiii) In our opinion and according"],
    "xii": ["xv) The company is not Nidhi", "hence this clause is not applicable."], "xii_a": ["xv) The company is not Nidhi"],
    "xii_b": ["xv) The company is not Nidhi"], "xii_c": ["xv) The company is not Nidhi"],
    "xiii": ["xvi) It is the responsibility", "We have exercised reasonable auditing", "Considering the large number",
             "We do not claim expertise", "all the transactions with related parties are in compliance"],
    "x_b": ["xvii) The company has not made any preferential"], "xiv_a": ["xviii) The Company has Internal Audit"],
    "xv": ["xix) The company has not entered into any non cash"],
    "xvi": ["xx) The company is not financial banking"], "xvi_a": ["xx) The company is not financial banking"],
    "xix": ["xxi) The company is capable of meeting", "On the basis of the financial ratios",
            "no material uncertainty exists"],
    "xx": ["xxii) The company is advised to prepare"],
    "xx_a": ["The expenditure or contribution made", "For the FY 2023-24 Rs.4109619", "The year wise schedule of spent",
             "Year Amount Spent CSR", "2021-22 4843756", "2022-23 4445975", "2023-24 4109619"],
    "xxi": ["xxiii) The company not being a holding company", "With respect to reporting under clauses of the Order"],
}
# label, scope_limited, deciding snippets, figures as printed
GOLD_LABEL = {
    "i_a_A": ("qualified", False, ["However, Company needs to maintain Fixed assets register"], []),
    "i_b": ("qualified", True, ["It is not possible to us to verify all the Fixed Assets"], []),
    "i_c": ("clean", False, ["c) As informed to us by the management, the title deeds"], []),
    "i_d": ("clean", False, ["d) Company has not revalued"], []),
    "i_e": ("clean", False, ["e) No proceedings have been initiated"], []),
    "ii_a": ("clean", False, ["inventory at reasonable intervals"], []),
    "ii_b": ("qualified", True, ["The inventory and work in process mentioned in the quarterly"], []),
    "iii_a_B": ("clean", False, ["On verification of relevant records, it is found that", "3 Jsons Holding"], []),
    "iii_b": ("clean", False, ["The terms and conditions of grant of all loans"], []),
    "iii_c": ("clean", False, ["repayment or receipts are regular"], []),
    "iv": ("clean", False, ["A) In our opinion and according to information and explanation given to us, in respect of loans"], []),
    "v": ("insufficient_evidence", False, ["viii) During the year the Company has accepted unsecured Loans"], []),
    "vi": ("clean", False, ["ix) The central Govt."], []),
    "vii_a": ("clean", False, ["There are no statutory undisputed dues outstanding"], []),
    "vii_b": ("adverse", False, ["b) According to the information and explanations given to us and the records of the company",
                                 "ZD270723066332E", "ZD2704240100910"], ["0.52", "2.21"]),
    "ix_a": ("clean", False, ["The company has not defaulted in repayment"], []),
    "ix_c": ("clean", False, ["The term loans availed by the company"], []),
    "ix_d": ("qualified", True, ["With respect to working capital loans"], []),
    "x_a": ("not_applicable", False, ["xii) The Company has not raised any money"], []),
    "x_b": ("not_applicable", False, ["xvii) The company has not made any preferential"], []),
    "xi_a": ("clean", False, ["xiii) In our opinion and according"], []),
    "xii_a": ("not_applicable", False, ["xv) The company is not Nidhi"], []),
    "xii_b": ("not_applicable", False, ["xv) The company is not Nidhi"], []),
    "xii_c": ("not_applicable", False, ["xv) The company is not Nidhi"], []),
    "xiii": ("clean", False, ["all the transactions with related parties are in compliance"], []),
    "xiv_a": ("clean", False, ["xviii) The Company has Internal Audit"], []),
    "xv": ("clean", False, ["xix) The company has not entered into any non cash"], []),
    "xvi_a": ("not_applicable", False, ["xx) The company is not financial banking"], []),
    "xix": ("clean", False, ["no material uncertainty exists"], []),
    "xx_a": ("adverse", False, ["2021-22 4843756"], ["3383756.00"]),
    "xxi": ("not_applicable", False, ["xxiii) The company not being a holding company"], []),
}




def _lines_with(text, snips):
    """The input lines that contain the snippets (a snippet may wrap over lines), in reading order."""
    lines = text.splitlines()
    starts, pos = [], 0
    for l in lines:
        starts.append(pos)
        pos += len(l) + 1
    idx = []
    for sn in snips:
        m = re.search(r"\s+".join(re.escape(w) for w in sn.split()), text)
        assert m, f"gold snippet not found: {sn!r}"
        first = max(i for i, s_ in enumerate(starts) if s_ <= m.start())
        last = max(i for i, s_ in enumerate(starts) if s_ <= m.end() - 1)
        idx += [i for i in range(first, last + 1) if i not in idx]
    return [lines[i] for i in sorted(idx)]


def _quote(text, snip):
    m = re.search(r"\s+".join(re.escape(w) for w in snip.split()), text)
    return " ".join(text[m.start():].split()[:40])


class GoldModel:
    """Answers Call 1 / Call 2 the way a correct model would on the Jsons Foundry filing.
    by_number=True: Call 1 maps by the PRINTED number instead (the F12 error) and Call 2 must move
    every misplaced line back to its unit."""

    def __init__(self, text, by_number=False):
        self.text, self.by_number, self.calls = text, by_number, []
        self.max_tokens = 8000
        self.gold_lines = {u: _lines_with(text, sn) for u, sn in GOLD_MAP.items()}
        self.home = {}
        for u, ls in self.gold_lines.items():
            for l in ls:
                if "_" in u or not any(k.startswith(u + "_") for k in GOLD_MAP):
                    self.home.setdefault(l, u)
        self.placed = self._by_number() if by_number else {u: list(ls) for u, ls in self.gold_lines.items()}

    def _by_number(self):
        """every gold line under the unit its PRINTED roman number names (wrong for this filing)."""
        cat = [u["unit"] for u in st.catalogue_units(2020)]
        out, cur = {}, None
        for l in self.text.splitlines():
            m = re.match(r"^\s*([ivx]{1,5})\)", l)
            if m and m.group(1) in cat:
                cur = m.group(1)
                if any(u.startswith(cur + "_") for u in cat):
                    cur = [u for u in cat if u.startswith(cur + "_")][0]
            if l in self.home and cur:
                out.setdefault(cur, []).append(l)
        return out

    def chat(self, system, user, schema=None):
        if system.startswith(CALL1):
            self.calls.append("C1")
            out = []
            for m in catalogue_for_order(2020)[0]:
                subs = [s.id for s in catalogue_for_order(2020)[3](m)]
                ml = list(self.placed.get(m, []))
                for s in subs:
                    ml += [l for l in self.placed.get(s, []) if l not in ml]
                rec = {"main_clause_id": m, "evidence_text": "-",
                       "normalized_fields": {"source_text": "\n".join(ml) or "-"}, "sub_clauses": []}
                for s in subs:
                    figs = GOLD_LABEL.get(s, ("", False, [], []))[3]
                    rec["sub_clauses"].append({"sub_clause_id": s, "evidence_text": "-", "normalized_fields": {
                        "source_text": "\n".join(self.placed.get(s, [])) or "-",
                        "amount_items": [{"as_printed": x, "counts_toward_total": True, "note": ""} for x in figs]}})
                if not subs:
                    figs = GOLD_LABEL.get(m, ("", False, [], []))[3]
                    rec["normalized_fields"]["amount_items"] = [{"as_printed": x, "counts_toward_total": True,
                                                                 "note": ""} for x in figs]
                out.append(rec)
            return json.dumps({"clauses": out})
        self.calls.append("C2")
        self.users = getattr(self, "users", []) + [user]
        out = []
        for c in call2_payload(user):
            def one(uid, text):
                text = c["passage"] if text == "(same as passage)" else text
                moves = []
                if self.by_number and text not in (None, "-"):
                    for l in text.split("\n"):              # consecutive lines with one home = one block
                        h = self.home.get(l)
                        if h and h != uid:
                            if moves and moves[-1]["to"] == h and moves[-1]["_last"]:
                                moves[-1]["text"] += "\n" + l
                            else:
                                moves.append({"to": h, "text": l, "_last": True})
                        elif moves:
                            moves[-1]["_last"] = False
                    for mv_ in moves:
                        mv_.pop("_last")
                cls, _sl, dec, _f = GOLD_LABEL.get(uid, ("insufficient_evidence", False, [], []))
                return {"mapping": "move" if moves else ("fits" if text not in (None, "-") else "no_text"),
                        "move": moves, "classification": cls,
                        "evidence_text": _quote(self.text, dec[0]) if dec and cls != "insufficient_evidence" else "-",
                        "explanation_text": f"{uid}: {cls.replace('_', ' ')} on the auditor's statement."}
            subs = [dict(one(s["sub_clause_id"], s.get("text")), sub_clause_id=s["sub_clause_id"])
                    for s in c.get("sub_clauses") or []]
            main = one(c["main_clause_id"], c.get("passage")) if not subs else {
                "mapping": "fits", "move": [], "explanation_text": f"{c['main_clause_id']} as a whole."}
            if subs and self.by_number:                    # lines printed under the main number
                main_moves = []
                for l in (c.get("passage") or "").split("\n"):
                    h = self.home.get(l)
                    if h and h.split("_")[0] != c["main_clause_id"]:
                        if main_moves and main_moves[-1]["to"] == h:
                            main_moves[-1]["text"] += "\n" + l
                        else:
                            main_moves.append({"to": h, "text": l})
                main.update(mapping="move" if main_moves else "fits", move=main_moves)
            out.append(dict(main, main_clause_id=c["main_clause_id"], sub_clauses=subs))
        up = user.split("UNPLACED TEXT:\n", 1)[1].split("\n\nReturn ONLY", 1)[0]
        assign = []
        for e in (json.loads(up) if up.strip() != "none" else []):
            for l, u in self.home.items():
                if pr.evidence_match(l, pr._norm(e["text"])) == "valid":
                    assign.append({"id": e["id"], "to": u, "text": l})
        return json.dumps({"clauses": out, "unplaced": assign})


expect = {"v": "Not Reported by Auditor", "vi": "Favourable Remark", "vii": "Adverse Remark",
          "vii_b": "Adverse Remark", "vii_a": "Favourable Remark", "ix_d": "Auditor Unable to Verify",
          "ix": "Auditor Unable to Verify", "xiii": "Favourable Remark", "xx": "Adverse Remark",
          "xx_a": "Adverse Remark", "xxi": "Favourable Remark", "xii": "Not Applicable",
          "xvi": "Not Applicable", "xvi_b": "Not Applicable", "xvii": "Not Reported by Auditor",
          "i_b": "Auditor Unable to Verify", "i_a_A": "Qualified Remark", "i": "Auditor Unable to Verify",
          "x": "Not Applicable", "xix": "Favourable Remark", "iii": "Favourable Remark"}
_ctj = prepared("jsons_2020")[1]
for tag, by_number in (("correct model", False), ("Call 1 maps by PRINTED number, Call 2 moves it back", True)):
    gm = GoldModel(_ctj.replace("\t", "    "), by_number=by_number)
    res, notes, recs, ct = run_filing("jsons_2020", gm, "staged")
    fj = flat(res)
    ewj = {}
    for c in res["result"]["CARO_ews"]:
        ewj[c["main_clause_id"]] = c["score_ready_fields"]
        for s_ in c["sub_clause_ews"]:
            ewj[s_["sub_clause_id"]] = s_["score_ready_fields"]
    j = "J" if not by_number else "JN"
    check(f"{j}1 Jsons Foundry ({tag}): 2 calls (+ at most one re-read), no fallback",
          gm.calls[:2] == ["C1", "C2"] and len(gm.calls) <= 3 and not notes.get("stage_fallback"),
          (gm.calls, notes.get("stage_fallback")))
    wrong = {u: (fj[u]["classification"], e) for u, e in expect.items() if fj[u]["classification"] != e}
    check(f"{j}2 off-by-one printed numbering ({tag}): client labels as the CA reads them", not wrong, wrong)
    check(f"{j}3 (vii)(b) disputed GST dues: amount computed by code from 'Rs. in Crore' figures",
          fj["vii_b"]["normalized_fields"]["amount_total"] == 27300000.0, fj["vii_b"]["normalized_fields"]["amount_total"])
    check(f"{j}4 red flags: (vii), (xx) Adverse and every scope limitation ((i)(b), (ii)(b), (ix)(d))",
          all(ewj[u]["red_flag"] for u in ("vii", "xx", "ix", "i", "ii")),
          {u: ewj[u]["red_flag"] for u in ("vii", "xx", "ix", "i", "ii")})
    check(f"{j}5 client source text = the auditor's exact words for every unit",
          all(s_ in (None, "-") or pr.evidence_match(s_, pr._norm(ct)) == "valid" or s_.startswith("(from main clause)")
              for s_ in (v["normalized_fields"].get("source_text") for v in fj.values())))
    check(f"{j}6 strict schema: 0 failures; same keys as the single flow",
          not schema_problems(res) and (key_paths(res) ^ key_paths(pure_single)) <= OPTIONAL)
    if by_number:
        # the moves themselves, on the records Option A hands to today's post-processing (today's
        # display rules may later show only a unit's own sentences, as in the single flow)
        gm_raw = GoldModel(ct, by_number=True)
        raw_recs = st.run_staged(gm_raw, ct, "x", "2023-24", 2020, {"llm_notes": {}})
        R = {s["sub_clause_id"]: s for r in raw_recs for s in r["sub_clauses"]}
        R.update({r["main_clause_id"]: r for r in raw_recs})
        left = [(u, l[:40]) for l, u in gm.home.items()
                if u in R and pr.evidence_match(l, pr._norm(R[u]["normalized_fields"].get("source_text") or "")) != "valid"
                and GOLD_LABEL.get(u, ("insufficient_evidence",))[0] != "insufficient_evidence"]
        mv = notes["staged"]["moves"]
        check("JN7 every misplaced line was moved back to the unit it answers (by subject) -- unnumbered (i) lines "
              "come back through the unplaced-text check -- and every move was verified",
              not left and mv and all(not m["result"].startswith("rejected") for m in mv),
              (left[:4], [m for m in mv if m["result"].startswith("rejected")][:3]))
        hints = notes["staged"]["subject_hints"]
        check("JN8 the code's subject hints point Call 2 at misplaced text (e.g. (v) deposits text under (viii))",
              len(hints) >= 5, list(hints)[:8])

# ---------------------------------------------------------------------------
# H (continued). D06 / N2, and K. Remarks
# ---------------------------------------------------------------------------
check("H13 D06: (ix)(c) fed by the public-offer / term-loans box",
      "ix_c" in MT.feeds("public_offer_term_loans", 2020) and "ix_c" not in MT.NO_BOX_2020
      and "public offer" in MT.box_hint("ix_c", 2020))
check("H14 D06: crosswalk names (ix)(d), (xi)(b), (xi)(c) as having no CARO 2016 counterpart",
      all(x in build_system_prompt(2022, crosswalk_layout="2016") for x in ("(ix)(d)", "(xi)(b)", "(xi)(c)")))
pat = pr._BLANKET_WHOLE[2020]["iii"]
sidd = ("the Company has not made any investments, provided guarantee or security or granted any loans or advances in the "
        "nature of loans, secured or unsecured, to companies, firms, limited liability partnerships or any other parties "
        "during the year.")
wick = ("(f) The Company has not granted any loans or advances in the nature of loans either repayable on demand or "
        "without specifying any terms or period of repayment.")
check("H15 N2: whole-clause sentence still counts; a single sub-point '(f) ...' is not a blanket statement",
      pr._whole_clause_blanket([sidd], pat, "iii") and not pr._whole_clause_blanket([wick], pat, "iii"))
single_tab = run_filing("table_2020", llm.MockClient(), "single")[0]
rem = single_tab["metadata"]["_source_remark"]
check("K1 Remarks: no new sentence (no '(xix) taken from the main audit report'); only the existing "
      "'not in the filing' Remarks",
      "taken from the main audit report" not in rem and rem.startswith("Source is MCA's 16-item XBRL table")
      and rem.endswith("are not in the filing."), rem)
check("K2 Remarks do not list (xix) as missing when (xix) was taken from the main audit report",
      "(xix)" not in rem and flat(single_tab)["xix"]["classification"] == "Adverse Remark", rem)
check("K3 the two-call flow gives the same Remarks", staged["table_2020"][0]["metadata"]["_source_remark"] == rem)

# ---------------------------------------------------------------------------
# S. "Auditor Unable to Verify" (approved 9-Oct): wider wording rule (both flows) + Call 2 judges
#    by meaning, accepted only when the auditor's own quote says they could not verify
# ---------------------------------------------------------------------------
SL = pr.SCOPE_LIMITATION_RE
check("S1 today's rule (both flows) now reads 'it is not possible to us to verify' (Jsons (i)(b) typo), "
      "'not shown to us', 'could not check', 'cannot be verified'; still not 'no discrepancies were noticed'",
      all(SL.search(t) for t in ("it is not possible to us to verify all the Fixed Assets",
                                 "the stock register was not shown to us", "we could not check the records",
                                 "the balances cannot be verified"))
      and not SL.search("no material discrepancies were noticed on such physical verification")
      and not SL.search("the Company has not provided any guarantee"))
SCOPE = """ANNEXURE A TO THE INDEPENDENT AUDITOR'S REPORT
(i) (b) We were not able to physically verify the Property, Plant and Equipment as the plant remained shut throughout the year.
(ii) (a) The inventory has been physically verified by the management at reasonable intervals and no material discrepancies were noticed on such verification.
"""
_IB, _IIA = SCOPE.split("\n")[1], SCOPE.split("\n")[2]


def c1_scope(system, user, schema):
    out = []
    for m in catalogue_for_order(2020)[0]:
        subs = [x.id for x in catalogue_for_order(2020)[3](m)]
        txt = {"i": _IB, "ii": _IIA}.get(m, "-")
        out.append({"main_clause_id": m, "evidence_text": "-", "normalized_fields": {"source_text": txt},
                    "sub_clauses": [{"sub_clause_id": x, "evidence_text": "-", "normalized_fields": {
                        "source_text": {"i_b": _IB, "ii_a": _IIA}.get(x, "-")}} for x in subs]})
    return json.dumps({"clauses": out})


def c2_scope(system, user, schema):
    out = []
    for c in call2_payload(user):
        subs = []
        for x in c.get("sub_clauses") or []:
            if x["sub_clause_id"] == "i_b":
                subs.append({"sub_clause_id": "i_b", "mapping": "fits", "classification": "qualified",
                             "scope_limited": True, "evidence_text": _IB[8:], "explanation_text": "Not verified."})
            elif x["sub_clause_id"] == "ii_a":        # a WRONG scope flag on a clean sentence
                subs.append({"sub_clause_id": "ii_a", "mapping": "fits", "classification": "clean",
                             "scope_limited": True, "evidence_text": _IIA[9:], "explanation_text": "Verified."})
            else:
                subs.append({"sub_clause_id": x["sub_clause_id"], "mapping": "no_text"})
        out.append({"main_clause_id": c["main_clause_id"], "mapping": "fits" if c["passage"] != "-" else "no_text",
                    "sub_clauses": subs})
    return json.dumps({"clauses": out, "unplaced": []})


FIX["scope_2020"] = (SCOPE, "U00000MH2000PTC000005", 2024, 2020, None, {})
cl = Scripted(s1=[c1_scope], s2=[c2_scope])
res, notes, recs, ct = run_filing("scope_2020", cl, "staged")
fs = flat(res)
Rs = {x["sub_clause_id"]: x for r in recs for x in r["sub_clauses"]}
check("S2 Call 2 judges by meaning: 'We were not able to physically verify ...' (wording today's rule does not "
      "list) -> Auditor Unable to Verify, red flag", fs["i_b"]["classification"] == "Auditor Unable to Verify"
      and not SL.search(_IB) and Rs["i_b"]["staged_trace"].get("scope_limited_by_meaning") == "accepted",
      (fs["i_b"]["classification"], Rs["i_b"].get("staged_trace")))
check("S3 a wrong scope flag on 'no material discrepancies were noticed on verification' is rejected by code: "
      "stays Favourable, no false red flag", fs["ii_a"]["classification"] == "Favourable Remark"
      and str(Rs["ii_a"]["staged_trace"].get("scope_limited_by_meaning")).startswith("rejected"),
      (fs["ii_a"]["classification"], Rs["ii_a"].get("staged_trace")))
check("S4 the Call 2 instruction explains scope limitation by meaning, with typo / paraphrase examples and "
      "what is NOT one", all(x in st.build_call2_system(2020) for x in (
          "AUDITOR UNABLE TO VERIFY - judge it by MEANING", "it is not possible to us to verify",
          "no material\n    discrepancies were noticed")) and "scope_limited" in json.dumps(st.call2_schema()))
del FIX["scope_2020"]

# ---------------------------------------------------------------------------
# P. own-question guard (Atharv 2023-24 (iii)(b)), share counts, broken JSON, "none"
# ---------------------------------------------------------------------------
TERMS = "In our opinion, the terms and conditions of the loans granted are not prejudicial to the interest of the Company."
IVQ = ("In our opinion, the Company has complied with the provisions of sections 185 and 186 of the Companies Act, "
       "2013 in respect of the loans granted.")


def c2_cross(first_label, recheck_label):
    """Call 2 that qualifies (iii)(b) because '(iv) does not confirm s.185/186' (the Atharv error);
    on the recheck it answers recheck_label (None = it repeats the same error)."""
    state = {"n": 0}

    def f(system, user, schema):
        state["n"] += 1
        r = json.loads(llm.MockClient().chat(system, user, schema=schema))
        for c in r["clauses"]:
            for x in c.get("sub_clauses") or []:
                if x["sub_clause_id"] == "iii_b":
                    lab = first_label if state["n"] == 1 else (recheck_label or first_label)
                    basis_ok = state["n"] > 1 and recheck_label
                    x.update(classification=lab, own_text_label="clean", evidence_text=TERMS,
                             basis=None if basis_ok else "cross_clause",
                             contradicted_by=None if basis_ok else {"unit": "iv", "quote": IVQ},
                             explanation_text=("Terms are not prejudicial." if basis_ok else
                                               "Terms not prejudicial, but explicit confirmation of section 185/186 "
                                               "compliance is missing, a cross-clause inconsistency."))
        return json.dumps(r)
    return f


cl = Scripted(s2=[c2_cross("qualified", "clean")] * 2)
res, notes, recs, ct = run_filing("synthetic_2020", cl, "staged")
g = [e for e in notes["staged"]["own_question_guard"] if e["unit"] == "iii_b"]
check("P1 Atharv (iii)(b) case: 'not prejudicial' qualified because (iv) 'does not confirm s.185/186' -> code "
      "sends it back (own answer is not a 'nothing happened' statement); the recheck answers clean -> Favourable",
      flat(res)["iii_b"]["classification"] == "Favourable Remark" and cl.calls == ["C1", "C2", "C2"]
      and g and g[0]["result"].startswith("sent back"), (flat(res)["iii_b"]["classification"], cl.calls, g))
cl = Scripted(s2=[c2_cross("qualified", None)] * 2)
res, notes, recs, ct = run_filing("synthetic_2020", cl, "staged")
Rs = {x["sub_clause_id"]: x for r in recs for x in r["sub_clauses"]}
check("P2 ... and if the recheck repeats the error, code keeps the label of the unit's OWN text (Favourable); "
      "the audit trace says why", flat(res)["iii_b"]["classification"] == "Favourable Remark"
      and "reverted to own-text label" in str(Rs["iii_b"]["staged_trace"].get("own_question_guard")),
      (flat(res)["iii_b"]["classification"], Rs["iii_b"]["staged_trace"]))
check("P3 the main clause (iii) is not left Qualified by the rejected sub-clause verdict",
      flat(res)["iii"]["classification"] != "Qualified Remark", flat(res)["iii"]["classification"])

CROSS = """ANNEXURE A TO THE INDEPENDENT AUDITOR'S REPORT
(iii) The Company has not made any investments, provided any guarantee or security or granted any loans or advances in the nature of loans to companies, firms or other parties during the year.
(iv) In respect of the loans granted in earlier years and outstanding at the year end, the Company has complied with the provisions of sections 185 and 186 of the Companies Act, 2013.
"""
_L3, _L4 = CROSS.split("\n")[1], CROSS.split("\n")[2]


def c1_cross(system, user, schema):
    out = []
    for m in catalogue_for_order(2020)[0]:
        subs = [x.id for x in catalogue_for_order(2020)[3](m)]
        txt = {"iii": _L3, "iv": _L4}.get(m, "-")
        out.append({"main_clause_id": m, "evidence_text": "-", "normalized_fields": {"source_text": txt},
                    "sub_clauses": [{"sub_clause_id": x, "evidence_text": "-", "normalized_fields": {
                        "source_text": _L3 if x == "iii_a_B" else "-"}} for x in subs]})
    return json.dumps({"clauses": out})


def c2_cross_ok(system, user, schema):
    out = []
    for c in call2_payload(user):
        subs = []
        for x in c.get("sub_clauses") or []:
            if x["sub_clause_id"] == "iii_a_B":
                subs.append({"sub_clause_id": "iii_a_B", "mapping": "fits", "own_text_label": "not_applicable",
                             "classification": "qualified", "basis": "cross_clause",
                             "contradicted_by": {"unit": "iv", "quote": "In respect of the loans granted in earlier "
                                                 "years and outstanding at the year end"},
                             "evidence_text": "The Company has not made any investments, provided any guarantee or "
                                              "security or granted any loans", "explanation_text": "(iv) shows loans."})
            else:
                subs.append({"sub_clause_id": x["sub_clause_id"], "mapping": "no_text"})
        main = {"main_clause_id": c["main_clause_id"], "mapping": "fits" if c["passage"] != "-" else "no_text",
                "sub_clauses": subs}
        if c["main_clause_id"] == "iv":
            main.update(classification="clean", own_text_label="clean", evidence_text=_L4[5:80],
                        explanation_text="Complied.")
        out.append(main)
    return json.dumps({"clauses": out, "unplaced": []})


FIX["cross_2020"] = (CROSS, "U00000MH2000PTC000006", 2024, 2020, None, {})
cl = Scripted(s1=[c1_cross], s2=[c2_cross_ok])
res, notes, recs, ct = run_filing("cross_2020", cl, "staged")
g = [e for e in notes["staged"]["own_question_guard"] if e["unit"] == "iii_a_B"]
check("P4 a REAL cross-clause case is kept: (iii) says no loans were granted, (iv) speaks of loans granted in "
      "earlier years and outstanding -> Qualified, contradiction verified in (iv)'s text, no follow-up",
      flat(res)["iii_a_B"]["classification"] == "Qualified Remark" and cl.calls == ["C1", "C2"]
      and g and g[0]["result"].startswith("accepted"), (flat(res)["iii_a_B"]["classification"], cl.calls, g))
del FIX["cross_2020"]


def c2_legacy(system, user, schema):
    """An answer without the new fields whose explanation still leans on another clause."""
    r = json.loads(llm.MockClient().chat(system, user, schema=schema))
    for c in r["clauses"]:
        for x in c.get("sub_clauses") or []:
            if x["sub_clause_id"] == "iii_b":
                x.update(classification="qualified", evidence_text=TERMS,
                         explanation_text="Terms are fine but clause (iv) does not explicitly confirm compliance.")
    return json.dumps(r)


cl = Scripted(s2=[c2_legacy, c2_legacy])
res, notes, recs, ct = run_filing("synthetic_2020", cl, "staged")
check("P5 an answer WITHOUT basis / own_text_label whose explanation leans on another clause is caught too: "
      "sent back once; still unsupported -> that filing uses today's single call (never an unsupported verdict)",
      cl.calls == ["C1", "C2", "C2", "single"] and "rest on another clause" in str(notes.get("stage_fallback"))
      and flat(res)["iii_b"]["classification"] == pure_single_f["iii_b"]["classification"],
      (cl.calls, notes.get("stage_fallback"), flat(res)["iii_b"]["classification"]))
cl = Scripted(s2=[c2_legacy, c2_legacy])
res, notes, recs, ct = run_filing("synthetic_2020", cl, "staged_nofallback")
check("P5b ... in test mode it is recorded as would_fall_back (and the audit trace says 'unresolved')",
      "rest on another clause" in " ".join(notes["staged"].get("would_fall_back", [])) and "single" not in cl.calls)
check("P6 Call 2 sees each unit's official question next to its text, and the own-question rule",
      '"question": "Whether investments made, guarantees provided' in cl.users[0]
      and "JUDGE EACH UNIT ONLY AGAINST ITS OWN QUESTION" in st.build_call2_system(2020)
      and "section 185/186 was complied with\n    is (iv)'s question" in st.build_call2_system(2020))

def c2_by_subject(system, user, schema):
    """The exact Atharv 2023-24 wording: no clause number, the other clause named by SUBJECT, and the
    basis declared as 'incomplete'."""
    r = json.loads(llm.MockClient().chat(system, user, schema=schema))
    for c in r["clauses"]:
        for x in c.get("sub_clauses") or []:
            if x["sub_clause_id"] == "iii_b":
                x.update(classification="qualified", own_text_label="qualified", basis="incomplete", evidence_text=TERMS,
                         explanation_text="Terms of loans are prima facie not prejudicial, but explicit confirmation "
                                          "of Section 185/186 compliance is missing.")
    return json.dumps(r)


cl = Scripted(s2=[c2_by_subject, c2_by_subject])
res, notes, recs, ct = run_filing("synthetic_2020", cl, "staged")
check("P5c a reason that names ANOTHER clause only by its SUBJECT ('Section 185/186 compliance is missing' on "
      "(iii)(b), whose text never mentions 185/186) is caught whatever basis is declared",
      cl.calls[:3] == ["C1", "C2", "C2"] and "iii_b" in str(notes.get("stage_fallback") or notes["staged"].get("call2"))
      and flat(res)["iii_b"]["classification"] != "Qualified Remark", (cl.calls, flat(res)["iii_b"]["classification"]))


def c2_genuine():
    """(ix)(d) genuinely qualified on its own text; the FIRST explanation also mentions term loans
    ((ix)(c)'s subject) as 'not mentioned'; the recheck gives the unit's own reason."""
    state = {"n": 0}

    def f(system, user, schema):
        state["n"] += 1
        r = json.loads(llm.MockClient().chat(system, user, schema=schema))
        for c in r["clauses"]:
            for x in c.get("sub_clauses") or []:
                if x["sub_clause_id"] == "ix_d":
                    x.update(classification="qualified", own_text_label="qualified", basis="own_text",
                             evidence_text="Funds raised on short term basis have not been utilised for long term purposes",
                             explanation_text=("Short-term funds statement given; the term loan details are not mentioned."
                                               if state["n"] == 1 else
                                               "The auditor's own statement on short-term funds is qualified."))
        return json.dumps(r)
    return f


g2 = c2_genuine()
cl = Scripted(s2=[g2, g2])
res, notes, recs, ct = run_filing("synthetic_2020", cl, "staged")
check("P5d a GENUINE own-text finding is not lost: when the recheck gives the unit's own reason, it is kept "
      "(no fallback)", not notes.get("stage_fallback") and cl.calls == ["C1", "C2", "C2"]
      and recs and [x for r in recs for x in r["sub_clauses"] if x["sub_clause_id"] == "ix_d"][0]["classification"]
      == "qualified", (notes.get("stage_fallback"), cl.calls))


def c2_main_rollup(system, user, schema):
    """Main (iii) Qualified 'because the other sub-clauses are not addressed' while its answered
    sub-clause is clean (Atharv 2021-22 / 2024-25)."""
    r = json.loads(llm.MockClient().chat(system, user, schema=schema))
    for c in r["clauses"]:
        if c["main_clause_id"] == "iii":
            c.update(classification="qualified", explanation_text="Terms are not prejudicial, but repayment "
                     "schedules, overdue amounts and demand loans are not addressed.")
    return json.dumps(r)


cl = Scripted(s2=[c2_main_rollup])
res, notes, recs, ct = run_filing("synthetic_2020", cl, "staged")
check("P12 a main clause's own label never stands on 'other sub-clauses are not addressed': the main takes its "
      "sub-clauses (today's roll-up), with no needless follow-up question",
      (cl.calls == ["C1", "C2"] or (cl.calls == ["C1", "C2", "C2"]       # PATCH 12-Oct-2026: only the F16 note
           and set(notes["staged"]["call2"].get("recheck_fix_tags") or {}) == {"iii_a_B"}))
      and flat(res)["iii"]["classification"] == pure_single_f["iii"]["classification"],
      (cl.calls, flat(res)["iii"]["classification"]))

DEN = """ANNEXURE A TO THE INDEPENDENT AUDITOR'S REPORT
(vii) (a) According to information and explanations given to us, there were no amounts outstanding in respect of undisputed income tax or GST for more than six months.
(b) According the information and explanation provided to us, there are no pending litigation on dues of income tax. Following are the pending Litigations of Income Tax Act: AY 2018-19 Section 143(1)(a) AY 2020-21 Penalty Proceedings
"""
_VA, _VB = DEN.split("\n")[1], DEN.split("\n")[2]


def c1_den(system, user, schema):
    out = []
    for m in catalogue_for_order(2020)[0]:
        subs = [x.id for x in catalogue_for_order(2020)[3](m)]
        out.append({"main_clause_id": m, "evidence_text": "-", "normalized_fields": {
            "source_text": (_VA + "\n" + _VB) if m == "vii" else "-"},
            "sub_clauses": [{"sub_clause_id": x, "evidence_text": "-", "normalized_fields": {
                "source_text": {"vii_a": _VA, "vii_b": _VB}.get(x, "-")}} for x in subs]})
    return json.dumps({"clauses": out})


def c2_den(second_label):
    state = {"n": 0}

    def f(system, user, schema):
        state["n"] += 1
        out = []
        for c in call2_payload(user):
            subs = []
            for x in c.get("sub_clauses") or []:
                if x["sub_clause_id"] in ("vii_a", "vii_b"):
                    lab = "clean" if (x["sub_clause_id"] == "vii_a" or state["n"] == 1) else second_label
                    q = "there were no amounts outstanding" if x["sub_clause_id"] == "vii_a" else (
                        "there are no pending litigation on dues of income tax" if lab == "clean"
                        else "Following are the pending Litigations of Income Tax Act: AY 2018-19 Section 143(1)(a)")
                    subs.append({"sub_clause_id": x["sub_clause_id"], "mapping": "fits", "own_text_label": lab,
                                 "classification": lab, "basis": "own_text" if lab != "clean" else None,
                                 "evidence_text": q, "explanation_text": "Read as a whole."})
                else:
                    subs.append({"sub_clause_id": x["sub_clause_id"], "mapping": "no_text"})
            out.append({"main_clause_id": c["main_clause_id"], "mapping": "fits" if c["passage"] != "-" else "no_text",
                        "sub_clauses": subs})
        return json.dumps({"clauses": out, "unplaced": []})
    return f


FIX["den_2020"] = (DEN, "U00000MH2000PTC000007", 2024, 2020, None, {})
f2 = c2_den("adverse")
cl = Scripted(s1=[c1_den], s2=[f2, f2])
res, notes, recs, ct = run_filing("den_2020", cl, "staged")
check("Q1 'no pending litigation' then 'Following are the pending Litigations ... AY 2018-19 ...' labelled clean: "
      "code does NOT set a label - it asks Call 2 to read the whole answer again; the model's second reading "
      "(Adverse) stands", cl.calls == ["C1", "C2", "C2"] and flat(res)["vii_b"]["classification"] == "Adverse Remark"
      and "whole answer again" in cl.users[1] and "SELF-CONTRADICTING" in cl.users[1],
      (cl.calls, flat(res)["vii_b"]["classification"]))
f2 = c2_den("clean")
cl = Scripted(s1=[c1_den], s2=[f2, f2])
res, notes, recs, ct = run_filing("den_2020", cl, "staged")
g = notes["staged"].get("denial_then_list_guard") or []
check("Q2 ... and if the model, having re-read it, still says clean, its answer stands (no rule overrides it); "
      "the unit is logged for review", flat(res)["vii_b"]["classification"] == "Favourable Remark"
      and any("review" in e["result"] for e in g) and not notes.get("stage_fallback"),
      (flat(res)["vii_b"]["classification"], g))
check("Q3 a plain denial with no listed items is not sent back",
      st.denial_then_list("There are no dues of income tax which have not been deposited on account of any dispute.")
      is None and st.denial_then_list(_VB))
del FIX["den_2020"]

INV = """ANNEXURE A TO THE INDEPENDENT AUDITOR'S REPORT
(ii) Whether physical verification of inventory has been conducted at reasonable intervals by the management and whether any material discrepancies were noticed;
In our opinion and according to the explanation given to us, procedure of such verification followed is reasonable and adequate having regard to size of the company and nature of business.
"""
_IQ, _IA = INV.split("\n")[1], INV.split("\n")[2]


def c1_inv(system, user, schema):
    out = []
    for m in catalogue_for_order(2020)[0]:
        subs = [x.id for x in catalogue_for_order(2020)[3](m)]
        out.append({"main_clause_id": m, "evidence_text": "-", "normalized_fields": {
            "source_text": (_IQ + "\n" + _IA) if m == "ii" else "-"},
            "sub_clauses": [{"sub_clause_id": x, "evidence_text": "-", "normalized_fields": {
                "source_text": {"ii_a": _IA}.get(x, "-")}} for x in subs]})
    return json.dumps({"clauses": out})


def c2_inv(second_label, quote=True):
    state = {"n": 0}

    def f(system, user, schema):
        state["n"] += 1
        out = []
        for c in call2_payload(user):
            subs = []
            for x in c.get("sub_clauses") or []:
                if x["sub_clause_id"] == "ii_a":
                    lab = "insufficient_evidence" if state["n"] == 1 else second_label
                    subs.append({"sub_clause_id": "ii_a", "mapping": "fits", "own_text_label": lab,
                                 "classification": lab, "basis": "incomplete",
                                 "evidence_text": ("procedure of such verification followed is reasonable and adequate"
                                                   if quote else "-"),
                                 "explanation_text": "Intervals and discrepancies are not stated."})
                else:
                    subs.append({"sub_clause_id": x["sub_clause_id"], "mapping": "no_text"})
            out.append({"main_clause_id": c["main_clause_id"], "mapping": "fits" if c["passage"] != "-" else "no_text",
                        "sub_clauses": subs})
        return json.dumps({"clauses": out, "unplaced": []})
    return f


FIX["inv_2020"] = (INV, "U00000MH2000PTC000008", 2024, 2020, None, {})
f2 = c2_inv("clean")
cl = Scripted(s1=[c1_inv], s2=[f2, f2])
res, notes, recs, ct = run_filing("inv_2020", cl, "staged")
check("R1 (Atharv 2021-22 (ii)(a)) the auditor's answer on the subject is quoted but labelled insufficient_evidence: "
      "code does NOT set a label - it asks Call 2 to read the answer again; the second reading (Favourable) stands",
      cl.calls == ["C1", "C2", "C2"] and flat(res)["ii_a"]["classification"] == "Favourable Remark"
      and "did NOT answer" in cl.users[1] and not notes.get("stage_fallback"),
      (cl.calls, flat(res)["ii_a"]["classification"]))
f2 = c2_inv("insufficient_evidence")
cl = Scripted(s1=[c1_inv], s2=[f2, f2])
res, notes, recs, ct = run_filing("inv_2020", cl, "staged")
g = notes["staged"].get("unanswered_text_guard") or []
check("R2 ... and if the model, having re-read it, keeps insufficient_evidence, its answer stands; the unit is "
      "logged for review", flat(res)["ii_a"]["classification"] == "Not Reported by Auditor"
      and any("review" in e["result"] for e in g) and not notes.get("stage_fallback"),
      (flat(res)["ii_a"]["classification"], g))
f2 = c2_inv("clean", quote=False)
cl = Scripted(s1=[c1_inv], s2=[f2, f2])
res, notes, recs, ct = run_filing("inv_2020", cl, "staged")
check("R3 ... the same when Call 2 confirms the text belongs to the unit ('fits') but gives no quote",
      cl.calls == ["C1", "C2", "C2"], cl.calls)
del FIX["inv_2020"]
check("R4 a printed question alone is not an answer, and units with no text are never sent back",
      st._answer_words(_IQ) == "" and st._answer_words(_IQ + "\n\n" + _IA).startswith("In our opinion"))
check("R5 the two-call prompt says insufficient_evidence is never the label for an answer on the subject, and that "
      "'prima facie' / 'in our opinion' is standard favourable wording",
      "never the label for an auditor's answer" in st.build_call2_system(2020)
      and "prima facie" in st.build_call2_system(2020))

COST = """ANNEXURE A TO THE INDEPENDENT AUDITOR'S REPORT
(vi) As per rule 4 of Companies (Cost Records and Audit) Rules, 2014, the company is required to conduct cost audit if companies overall turnover from all the products are more than 50 crs. As informed to us the company is under process for maintaining proper records for complying with the provisions of this section.
"""
_CV = COST.split("\n")[1]


def c1_cost(system, user, schema):
    out = []
    for m in catalogue_for_order(2020)[0]:
        subs = [x.id for x in catalogue_for_order(2020)[3](m)]
        out.append({"main_clause_id": m, "evidence_text": "-", "normalized_fields": {
            "source_text": _CV if m == "vi" else "-"},
            "sub_clauses": [{"sub_clause_id": x, "evidence_text": "-", "normalized_fields": {"source_text": "-"}}
                            for x in subs]})
    return json.dumps({"clauses": out})


def c2_cost(system, user, schema):
    out = []
    for c in call2_payload(user):
        e = {"main_clause_id": c["main_clause_id"], "mapping": "fits" if c["passage"] != "-" else "no_text",
             "sub_clauses": [{"sub_clause_id": x["sub_clause_id"], "mapping": "no_text"}
                             for x in c.get("sub_clauses") or []]}
        if c["main_clause_id"] == "vi":       # Atharv 2022-23: "under process" read as a scope limitation
            e.update(own_text_label="qualified", classification="qualified", basis="scope_limitation",
                     scope_limited=True,
                     evidence_text="As informed to us the company is under process for maintaining proper records",
                     explanation_text="The company is 'under process' for maintaining cost records, indicating "
                                      "they could not verify compliance as the records were not yet in place.")
        out.append(e)
    return json.dumps({"clauses": out, "unplaced": []})


FIX["cost_2020"] = (COST, "U00000MH2000PTC000009", 2024, 2020, None, {})
cl = Scripted(s1=[c1_cost], s2=[c2_cost])
res, notes, recs, ct = run_filing("cost_2020", cl, "staged")
check("R6 (Atharv 2022-23 (vi)) 'under process for maintaining cost records' is the company's shortfall: Qualified, "
      "never 'Auditor Unable to Verify' - the model's explanation ('could not verify') cannot bring back a scope "
      "limitation the auditor's own words do not state", flat(res)["vi"]["classification"] == "Qualified Remark",
      flat(res)["vi"]["classification"])
del FIX["cost_2020"]
_r = {"classification": "qualified", "explanation_text": "The auditor was unable to verify the records.",
      "evidence_text": "-", "score_ready_fields": {}}
check("R7 single flow unchanged: a record without the two-call trace is still marked by the explanation check",
      pl._check_scope_limitation(_r) and _r.get("scope_limited") is True)
check("R8 the two-call prompt says 'under process' means the company has not yet done it (qualified, never clean)",
      "\"Under process\" / \"in the process of\" means the COMPANY has NOT yet done" in st.build_call2_system(2020))

LN = """ANNEXURE A TO THE INDEPENDENT AUDITOR'S REPORT
(iii) On verification of relevant records, it is found that, the company has granted following loans, unsecured, to the companies covered in the register maintained under section 189 of the Companies Act 2013.
S NO Particulars Closing Balance as on 31.03.2022 (in Rs) Closing Balance as on 31.03.2021 (in Rs) Badri Kedar Infra Pvt Ltd 122475616.00 110099137.00 Maind Flow Controls Pvt. Ltd. 184043408.80 128292187.80
(v) During the year the Company has accepted unsecured Loans as follows Name PAN Amount of deposit/ loan( in Rs)31/03/2022 Amey A Jadhav ALXPJ6244P 324836.00 S M Jadhav HUF AACHJ2210D 153286.00
(ii) (b) With respect to working capital limits sanctioned, the Company is regular in submitting its quarterly returns. The inventory mentioned in the quarterly statements is not verified by us and we have relied upon the information given to us by Management.
"""
_L = LN.split("\n")


def c1_ln(system, user, schema):
    out = []
    for m in catalogue_for_order(2020)[0]:
        subs = [x.id for x in catalogue_for_order(2020)[3](m)]
        main_src = {"iii": _L[1] + "\n" + _L[2], "v": _L[3], "ii": _L[4]}.get(m, "-")
        out.append({"main_clause_id": m, "evidence_text": "-", "normalized_fields": {"source_text": main_src},
                    "sub_clauses": [{"sub_clause_id": x, "evidence_text": "-", "normalized_fields": {
                        "source_text": _L[4] if x == "ii_b" else "-"}} for x in subs]})   # (iii) text on the main only
    return json.dumps({"clauses": out})


def c2_ln(second_ii_b):
    state = {"n": 0}

    def f(system, user, schema):
        state["n"] += 1
        out = []
        for c in call2_payload(user):
            e = {"main_clause_id": c["main_clause_id"], "mapping": "fits" if c["passage"] != "-" else "no_text",
                 "sub_clauses": []}
            for x in c.get("sub_clauses") or []:
                sid = x["sub_clause_id"]
                if sid == "iii_a_B":          # answered from the clause passage
                    e["sub_clauses"].append({"sub_clause_id": sid, "mapping": "fits", "classification": "clean",
                        "own_text_label": "clean", "evidence_text": "the company has granted following loans, "
                        "unsecured, to the companies covered in the register maintained under section 189 of the "
                        "Companies Act 2013", "explanation_text": "Loans given and balances are listed."})
                elif sid == "ii_b":
                    lab = "clean" if state["n"] == 1 else second_ii_b
                    e["sub_clauses"].append({"sub_clause_id": sid, "mapping": "fits", "classification": lab,
                        "own_text_label": lab, "scope_limited": lab == "qualified",
                        "basis": "scope_limitation" if lab == "qualified" else None,
                        "evidence_text": ("the Company is regular in submitting its quarterly returns" if lab == "clean"
                                          else "The inventory mentioned in the quarterly statements is not verified "
                                               "by us and we have relied upon the information given to us by Management."),
                        "explanation_text": "Read again."})
                else:
                    e["sub_clauses"].append({"sub_clause_id": sid, "mapping": "no_text"})
            if c["main_clause_id"] == "v":    # a quote stitched from table cells (PAN column skipped)
                e.update(classification="qualified", own_text_label="qualified", basis="incomplete",
                         evidence_text="During the year the Company has accepted unsecured Loans as follows Amey A "
                                       "Jadhav 324836.00 S M Jadhav HUF 153286.00",
                         explanation_text="Loans accepted; compliance with ss.73-76 not stated.")
            out.append(e)
        return json.dumps({"clauses": out, "unplaced": []})
    return f


FIX["ln_2020"] = (LN, "U00000MH2000PTC000010", 2024, 2020, None, {})
f2 = c2_ln("qualified")
cl = Scripted(s1=[c1_ln], s2=[f2, f2])
res, notes, recs, ct = run_filing("ln_2020", cl, "staged")
fl = flat(res)
check("R9 (Jsons FY 2021-22 (iii)) a sub-clause answered from words placed only on the clause passage keeps its "
      "verdict: the passage lines become its text (same clause, word for word)",
      fl["iii_a_B"]["classification"] == "Favourable Remark" and "Badri Kedar" in fl["iii_a_B"]["normalized_fields"]["source_text"],
      (fl["iii_a_B"]["classification"], fl["iii_a_B"]["normalized_fields"]["source_text"][:80]))
check("R10 (Jsons FY 2021-22 (v)) a quote stitched from table cells is replaced by the auditor's own line, so the "
      "verdict is not withdrawn as 'not in the filing'", fl["v"]["classification"] == "Qualified Remark",
      fl["v"]["classification"])
check("R11 (Jsons FY 2024-25 (ii)(b)) 'not verified by us' in the unit's own text but labelled Favourable: code asks "
      "Call 2 to read it again; the second reading (Auditor Unable to Verify) stands",
      cl.calls == ["C1", "C2", "C2"] and fl["ii_b"]["classification"] == "Auditor Unable to Verify"
      and "not verified by us" in cl.users[1], (cl.calls, fl["ii_b"]["classification"]))
f2 = c2_ln("clean")
cl = Scripted(s1=[c1_ln], s2=[f2, f2])
res, notes, recs, ct = run_filing("ln_2020", cl, "staged")
check("R12 ... and if the model keeps Favourable after reading it again, its answer stands and is logged for review",
      flat(res)["ii_b"]["classification"] == "Favourable Remark"
      and any("review" in e["result"] for e in notes["staged"].get("scope_text_guard") or []))
del FIX["ln_2020"]
_c1 = st.build_call1_system(2020)
_c2 = st.build_call2_system(2020)
check("R13 prompts: previous-year columns never count toward a total (Call 1); a (iii)(a) table of loans is the "
      "answer to (iii)(a), and quotes are never stitched from table cells (Call 2)",
      "only the CURRENT year's balance counts" in _c1 and "A question that asks for AMOUNTS" in _c2
      and "never stitch cells" in _c2)
NA_T = """ANNEXURE A TO THE INDEPENDENT AUDITOR'S REPORT
[MCA_BOX: Disclosure in auditors report relating to deposits accepted]
NA
"""


def c1_na(system, user, schema):
    out = []
    for m in catalogue_for_order(2020)[0]:
        subs = [x.id for x in catalogue_for_order(2020)[3](m)]
        out.append({"main_clause_id": m, "evidence_text": "-", "normalized_fields": {
            "source_text": "NA" if m == "v" else "-"},
            "sub_clauses": [{"sub_clause_id": x, "evidence_text": "-", "normalized_fields": {"source_text": "-"}}
                            for x in subs]})
    return json.dumps({"clauses": out})


def c2_na(system, user, schema):
    second = "recheck" in user
    out = []
    for c in call2_payload(user):
        e = {"main_clause_id": c["main_clause_id"], "mapping": "fits" if c["passage"] != "-" else "no_text",
             "sub_clauses": [{"sub_clause_id": x["sub_clause_id"], "mapping": "no_text"} for x in c.get("sub_clauses") or []]}
        if c["main_clause_id"] == "v":       # U29299 FY 2022-23: "'NA' indicates no information provided"
            e.update(mapping="fits" if second else "no_text",
                     classification="not_applicable" if second else "insufficient_evidence",
                     own_text_label="not_applicable" if second else "insufficient_evidence",
                     evidence_text="NA" if second else "-", explanation_text="Read again.")
        out.append(e)
    return json.dumps({"clauses": out, "unplaced": []})


FIX["na_2020"] = (NA_T, "U00000MH2000PTC000011", 2024, 2020, None, {})
cl = Scripted(s1=[c1_na], s2=[c2_na, c2_na])
res, notes, recs, ct = run_filing("na_2020", cl, "staged")
check("R15 (U29299 FY 2022-23 (v)) a box that says only 'NA' is the auditor's answer: labelled 'no text', it is sent "
      "back once (code sets no label); the model's second reading (Not Applicable) stands",
      cl.calls == ["C1", "C2", "C2"] and flat(res)["v"]["classification"] == "Not Applicable"
      and "short answer" in cl.users[1], (cl.calls, flat(res)["v"]["classification"]))
del FIX["na_2020"]
_ixc = [u for u in st.catalogue_units(2020) if u["unit"] == "ix_c"][0]
check("R16 (Jsons FY 2023-24) a working-capital sentence under (ix)(c) gets a subject hint pointing Call 2 to (ix)(d)",
      "another clause" in (st.subject_hint(_ixc, "The term loans are applied for the purpose. With respect to working "
                                                 "capital loans, we are unable to monitor the use") or "")
      and st.subject_hint(_ixc, "The term loans availed are applied for the purpose for which they were availed.") is None)
check("R17 Call 2 prompt: 'NA' is an answer; 'applies' is not 'complied'; advice is not an exception but a missing "
      "register is a shortfall; (xiv) an affirmative 'commensurate system' stands even with 's.138 not applicable' "
      "(ICAI); (ix)(c) term loans vs (ix)(d) working-capital / short-term funds (ICAI)",
      all(x in _c2 for x in ('"NA", "N.A."', "is not a statement that\n    it was COMPLIED", "Advice is not an exception",
                             "needs to\n    maintain a fixed assets register",
                             "an internal audit system commensurate with the size and business\n        -> clean",
                             "SHORT-TERM / WORKING-CAPITAL borrowings")),
      [x for x in ('"NA", "N.A."', "Advice is not an exception") if x not in _c2])
_P = ("xi) In our opinion the company has no outstanding dues to any financial institutions or banks. The term loans "
      "availed by the company are generally applied for the purpose for which they were availed. With respect to "
      "working capital loans, we are unable to monitor the use of the said loans considering the huge nature of "
      "transactions from these accounts.")
_u = {"ix": {**st._empty_unit(), "source": _P}, "ix_d": st._empty_unit()}
_a = {"ix_d": {"classification": "qualified", "evidence": "With respect to working capital loans, we are unable to "
               "monitor the use of the said loans considering the huge nature of transactions from these accounts."}}
_by = {u["unit"]: u for u in st.catalogue_units(2020)}
st.settle_text(_u, _a, _by, {})
check("R18 (U29299 FY 2023-24 (ix)(d)) a quote found in a one-line clause passage brings that SENTENCE to the "
      "sub-clause and the quote is kept (the 'unable to monitor' words are not lost)",
      "unable to monitor" in _a["ix_d"]["evidence"] and "unable to monitor" in _u["ix_d"]["source"]
      and "no outstanding dues" not in _u["ix_d"]["source"], (_a["ix_d"]["evidence"][:60], _u["ix_d"]["source"][:80]))
import logging as _lg


class _Cap(_lg.Handler):
    def __init__(self):
        super().__init__(); self.recs = []

    def emit(self, r):
        self.recs.append(r)


_h = _Cap(); llm.logger.addHandler(_h); _lvl = llm.logger.level; llm.logger.setLevel(_lg.DEBUG)
_dis = _lg.root.manager.disable; _lg.disable(_lg.NOTSET)
_oc = llm.OpenAICompatClient.__new__(llm.OpenAICompatClient)
_oc.max_tokens, _oc._ctx_len = 35000, 65536
_oc._context_len = lambda: 65536
_room = _oc._budget("x" * 109000, "")
_oc._budget("x" * 160000, "")
llm.logger.removeHandler(_h); llm.logger.setLevel(_lvl); _lg.disable(_dis)
check("R14 log: a trimmed reply allowance is an INFO line in plain words (no '4096 is plenty'); only a low "
      "allowance (< 16,000 tokens) is a WARNING",
      _h.recs and _h.recs[0].levelno == _lg.INFO and "4096" not in _h.recs[0].getMessage()
      and "reply allowance" in _h.recs[0].getMessage() and _h.recs[1].levelno == _lg.WARNING,
      [(r.levelname, r.getMessage()[:90]) for r in _h.recs])

cnm = st.count_not_money
check("P7 a COUNT is never money: '1,50,00,000 Equity Shares', '45 days' dropped; 'Rs. 404049', '15,410/-' kept; "
      "a figure printed once as a count and once as money is kept",
      cnm("1,50,00,000", "issued 1,50,00,000 Equity Shares of Rs. 10 each") and cnm("45", "a delay of 45 days")
      and not cnm("Rs. 404049", "loan of Rs. 404049") and not cnm("15,410", "TDS 15,410/-")
      and not cnm("500", "500 shares and Rs. 500 penalty"))
SH = "During the year Company had issued 1,50,00,000 Equity Shares of Rs. 10 each."
u_ = {"x_b": {**st._empty_unit(), "source": SH,
              "amount_items": [{"as_printed": "1,50,00,000", "counts_toward_total": True, "note": "shares"}]}}
check("P8 ... and Call 1's share count is removed before the amounts are added (audit: dropped_count_figures)",
      st.drop_count_figures(u_) == {"x_b": ["1,50,00,000"]} and not u_["x_b"]["amount_items"])

BROKEN = None


def c2_broken(system, user, schema):
    """A reply with one missing comma inside clause (iv) -- every other clause record is complete."""
    raw = llm.MockClient().chat(system, user, schema=schema)
    r = json.loads(raw)
    txt = json.dumps(r, indent=2)
    i = txt.index('"main_clause_id": "iv"')
    j = txt.index('"explanation_text"', i)
    return txt[:j - 1].rstrip().rstrip(",") + "\n" + txt[j - 1:].replace('"explanation_text"', '"explanation_text"', 1)


cl = Scripted(s2=[c2_broken])
res, notes, recs, ct = run_filing("synthetic_2020", cl, "staged")
sv = notes["staged"]["call2"].get("salvaged") or notes["staged"]["call2"].get("repaired_json")   # PATCH 12-Oct-2026
check("P9 broken JSON (one missing comma): the complete clause records are kept and only the missing clause is "
      "asked again -- no full repeat, no fallback, same labels as the single flow",
      sv and cl.calls == ["C1", "C2", "C2"] and not notes.get("stage_fallback")
      and flat(res)["iv"]["classification"] == pure_single_f["iv"]["classification"]
      and "reask" in str(notes["staged"]["call2"].get("missing_first_reply", "")) or (
          sv and not notes.get("stage_fallback") and flat(res)["iv"]["classification"]
          == pure_single_f["iv"]["classification"]), (sv, cl.calls, notes.get("stage_fallback")))
skel = json.dumps({"clauses": [{"main_clause_id": m, "mapping": "no_text", "move": [],
                                "classification": "insufficient_evidence"} for m in "ivx" * 30]}, indent=2)
check("P10 the repeated JSON structure of a long, BROKEN reply is not a copy loop (only text values are read)",
      not st.repeated_span(skel[:-40]))
nt = {}
st.apply_moves({}, {}, [{"id": "U1", "to": "none", "text": "ANNEXURE A TO THE INDEPENDENT AUDITOR'S REPORT"}],
               ["ANNEXURE A TO THE INDEPENDENT AUDITOR'S REPORT"], "ANNEXURE A TO THE INDEPENDENT AUDITOR'S REPORT",
               cat20, nt)
check("P11 an unplaced heading that Call 2 says answers no clause is logged as 'not evidence', not 'rejected'",
      nt["moves"][0]["result"].startswith("not evidence"), nt["moves"])

# ---------------------------------------------------------------------------
# I. cache, perf, audit
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    os.environ["RESULT_CACHE_DIR"] = tmp
    result_cache.put("U00000MH2000PTC000001", {"flow": "single"})
    os.environ["CARO_LLM_FLOW"] = "staged"
    hit_staged = result_cache.get("U00000MH2000PTC000001")
    result_cache.put("U00000MH2000PTC000001", {"flow": "staged"})
    got_staged = result_cache.get("U00000MH2000PTC000001")
    os.environ.pop("CARO_LLM_FLOW")
    got_single = result_cache.get("U00000MH2000PTC000001")
    names = sorted(p.name for p in Path(tmp).glob("*.json.gz"))
    os.environ.pop("RESULT_CACHE_DIR")
check("I1 a staged run is never served a cached single result, and the reverse (C-14)",
      hit_staged is None and got_staged == {"flow": "staged"} and got_single == {"flow": "single"})
check("I2 single-flow cache file name unchanged", "U00000MH2000PTC000001.json.gz" in names, names)
check("I3 perf: F5a1 / F5a2 are LLM requests", {"F5a1", "F5a2"} <= set(perf.LLM_CODES) and "F5a1" in perf.NAME_OF)
e = audit.entry_for({"staged_trace": {"mapping": "move", "moved_out": [{"to": "iv", "text": "x"}]}}, level="sub",
                    main_clause_id="iii", final_label="x", sub_clause_id="iii_b")
check("I4 audit: the mapping check and every text move are recorded per unit",
      e and "staged_text_moved_by_mapping_check" in e["actions"] and e["staged_trace"]["moved_out"])
check("I5 CARO_LLM_FLOW defaults to single; unknown value -> single",
      st.llm_flow() == "single" and (os.environ.__setitem__("CARO_LLM_FLOW", "x") or st.llm_flow() == "single"))
os.environ.pop("CARO_LLM_FLOW", None)


# ---------------------------------------------------------------------------
# PATCH 12-Oct-2026 (Phase 2, batch 12-Oct): checks named after the filings that showed each problem
# ---------------------------------------------------------------------------
from caro import post_rules as _pr          # noqa: E402

# CUTOFF / F03: broken JSON repaired, valid JSON untouched
_broken = '{"clauses": [{"main_clause_id": "iv", "explanation_text": "Not applicable."\n "sub_clauses": []},' \
          ' {"main_clause_id": "xiv", "explanation_text": "keep that finding ("clean") per the rule", "sub_clauses": []}]}'
_fixed = st.repair_json(_broken)
check("B1 (11-Oct: 30 of 31 broken Call-2 replies) a missing comma and an unescaped quote are repaired - "
      "nothing added or removed but a comma / backslash; a valid reply is returned unchanged",
      _fixed and [c["main_clause_id"] for c in _fixed["clauses"]] == ["iv", "xiv"]
      and '("clean")' in _fixed["clauses"][1]["explanation_text"]
      and st.repair_json('{"clauses": [{"main_clause_id": "i"}]}') == {"clauses": [{"main_clause_id": "i"}]})


class _CutClient(Scripted):
    """Call 1's first reply is cut off at the token limit in the middle of clause (viii)."""

    def __init__(self):
        super().__init__()
        self.last_finish_reason = None
        self._n1 = 0

    def _route(self, system, user, schema):
        self.last_finish_reason = "stop"
        if system.startswith(CALL1):
            self._n1 += 1
            if self._n1 == 1:
                self.calls.append("C1")
                full = self.m.chat(system, user, schema=schema)
                cut = full.index('"main_clause_id": "viii"') if '"main_clause_id": "viii"' in full else len(full) // 2
                self.last_finish_reason = "length"
                return full[:cut + 60]
        return super()._route(system, user, schema)


cl = _CutClient()
res, notes, _r, _c = run_filing("synthetic_2020", cl, "staged")
c1n = notes.get("staged", {}).get("call1", {})
check("B2 (Cellecor / Siddhivinayak 11-Oct) a Call-1 reply cut off at clause (viii): the complete clauses are kept, "
      "the cut-off clause and the rest are asked again in the follow-up, no fallback, every clause has a record",
      not notes.get("stage_fallback") and cl.calls[:2] == ["C1", "C1"] and c1n.get("cut_off")
      and all(m in flat(res) for m in ("i", "vii", "viii", "xx")), (cl.calls, notes.get("stage_fallback"), c1n.get("cut_off")))

_rep_text = "Hence reporting under paragraph 3(viii) of the Order is not applicable to the company. "
_ok_reply = json.dumps({"clauses": [{"main_clause_id": "viii", "evidence_text": _rep_text * 4,
                                     "normalized_fields": {"source_text": _rep_text * 4}, "sub_clauses": []}]})


class _Once:
    last_finish_reason = "stop"

    def __init__(self):
        self.n = 0

    def chat(self, system, user, schema=None):
        self.n += 1
        return _ok_reply


_o = _Once()
_nn = {}
_got = st._ask(_o, 1, "s", "u", {}, lambda *a, **k: None, _nn, "main")
check("B3 (Cellecor 11-Oct: curly apostrophe / 'thebasis' read as a copy loop) a complete, readable reply is never "
      "rejected for repeated text - it is only logged", _o.n == 1 and _got["clauses"] and _nn.get("repetition_seen"),
      (_o.n, _nn))
_txt, _k = st.collapse_repeats(("The company has not raised any money by way of public offer during the year. " * 2).strip(),
                               "The company has not raised any money by way of public offer during the year.")
check("B3b a sentence the model wrote twice in a row is kept once (the filing prints it once)", _k == 1, (_k, _txt))

# F09
_pos = ["in agreement with the books of account of the Company subject to certain minor discrepancies.",
        "regularly deposited except some variation by the Company with the appropriate authorities.",
        "complied with and the funds raised have been used for the purposes for which the funds were raised. However, "
        "1792 numbers of ESOPs constituting 7.79% of the shares have not been issued till date."]
_neg = ["The inventory, except goods-in-transit and stocks lying with third parties, has been physically verified.",
        "the title deeds are held in the name of the company, except the following:- Description of Property Gross "
        "carrying value Held in name of NA (d) The company has not revalued",
        "We have not, however, made a detailed examination of the cost records.",
        "The Company has generally been regular in depositing undisputed statutory dues. There are no undisputed dues "
        "outstanding for more than six months."]
check("B4 (I07: Jaywant, Refit, 3FATE, Ichalkaranji) a stated exception is noticed; routine carve-outs, empty 'except "
      "the following: NA' tables, disclaimers and hedging alone are not",
      all(st.stated_exception(x) for x in _pos) and not any(st.stated_exception(x) for x in _neg),
      ([bool(st.stated_exception(x)) for x in _pos], [bool(st.stated_exception(x)) for x in _neg]))
_c2 = st.build_call2_system(2022)
check("B5 Call 2 (two-call flow only) carries the 12-Oct rules: STATED EXCEPTIONS, D14 arrears without an amount, "
      "dangling list, (iii)(a)(A)/(B), copy-only quotes and plain explanations; CARO 2016 (ix) both halves only for 2016",
      all(x in _c2 for x in ("STATED EXCEPTIONS", "STATUTORY ARREARS (approved 8 Oct, D14)", "DANGLING LIST",
                             "(iii)(a)(A) AND (iii)(a)(B)", "QUOTES AND EXPLANATIONS"))
      and "CARO 2016 (ix) asks about TWO things" in st.build_call2_system(2019)
      and "CARO 2016 (ix) asks about TWO things" not in _c2 and "COPY RULES" in st.build_call1_system(2022))
# PATCH 13-Oct-2026 (Phase 2, STEP_A). WHY: the escape rule must reach the model as \" and \\;
# in a normal Python string they were read as a bare quote and backslash.
import caro.staged_llm as _sa
check("B5b Step A tells the model the real JSON escapes (\\\" and \\\\), not a bare quote",
      'as \\" and a backslash as \\\\.' in _sa._STEP_A_CALL1 and 'text value is \\".' in _sa._STEP_A_CALL2
      and 'as \\" and a backslash as \\\\.' in _sa.build_call1_system(2020))
check("B6 schemas: a quote may be up to 1,500 characters (204 of 7,715 CARO sentences are longer than 600)",
      st.call2_schema()["properties"]["clauses"]["items"]["properties"]["evidence_text"]["maxLength"] == 1500)

# F12
_m_aff = {"main_clause_id": "xiv", "_staged": True, "evidence_text": "In our opinion the company has an adequate "
          "internal audit system commensurate with the size and nature of its business.", "normalized_fields": {}}
_m_exc = {"main_clause_id": "iii", "_staged": True, "evidence_text": "The Company has not granted any loan, secured or "
          "unsecured, to companies except during the normal course of company's business.", "normalized_fields": {}}
_m_na = {"main_clause_id": "iii", "_staged": True, "evidence_text": "The Company has not granted any loans, secured or "
         "unsecured, to companies, firms or other parties.", "normalized_fields": {}}
check("B7 (Cellecor 2021-22 / Jsons Foundry 2023-24 (xiv)(b); Jaywant 2020-21 (iii)(c)) Not Applicable is not passed "
      "down from an affirmative main clause or from 'not granted ... except ...'; a real 'not granted any loans' still "
      "passes down; the single flow (no _staged mark) is unchanged",
      not _pr.na_covers(_m_aff, "xiv_b", 2022) and not _pr.na_covers(_m_exc, "iii_c", 2020)
      and _pr.na_covers(_m_na, "iii_c", 2022) and _pr.na_covers(dict(_m_aff, _staged=False), "xiv_b", 2022)
      == _pr.na_covers({k: v for k, v in _m_aff.items() if k != "_staged"}, "xiv_b", 2022))

# F13
_vi = ("We have broadly reviewed the cost records maintained by the company and we are of the opinion that prima facie "
       "the prescribed records have been made and maintained. However, we have not made detailed examination of the "
       "cost records with a view to determine whether they are accurate or complete.")
_recs = [{"main_clause_id": "vi", "classification": "qualified", "evidence_text": _vi, "_staged": True, "sub_clauses": []},
         {"main_clause_id": "vi", "classification": "qualified", "evidence_text": _vi, "sub_clauses": []},
         {"main_clause_id": "vi", "classification": "qualified", "_staged": True, "sub_clauses": [],
          "evidence_text": "As informed to us the company is under process for maintaining proper records."}]
_pr.apply_overgrade_guard(_recs, 2022)
check("B8 (Committed 2024-25 (vi)) the Order's standard 'we have not, however, made a detailed examination' line is "
      "not an exception (two-call records); 'under process' stays Qualified; single-flow records unchanged",
      [r["classification"] for r in _recs] == ["clean", "qualified", "qualified"], [r["classification"] for r in _recs])
check("B9 (Rama Cylinders / Trisa 2024-25 (ii)(b), K.R. Foods) the could-not-verify check reads these forms",
      all(st._SCOPE_MEANING.search(x) for x in (
          "the Company does not possess the requisite documentation for us to verify whether the returns agree",
          "the same cannot be checked as the Company is unable to provide the relevant records to us for verification",
          "it is difficult to comment the Company has not taken any funds"))
      and not st._SCOPE_MEANING.search("The inventory has been physically verified during the year by the management."))

# F19 - every copy of the quote
_two = ("[MCA_BOX: Disclosure in auditors report relating to statutory dues]\nThere are no dues of income-tax which have "
        "not been deposited on account of any dispute.\n\n(vii) (b) There are no dues of income-tax which have not been "
        "deposited on account of any dispute.\nCommissioner of Customs SCN No. 291 Rs. 7,50,000 2017-18 CESTAT\n")
_u7 = {"sub_clause_id": "vii_b", "classification": "clean", "_staged": True,
       "evidence_text": "There are no dues of income-tax which have not been deposited on account of any dispute.",
       "normalized_fields": {"source_text": "There are no dues of income-tax which have not been deposited on account of any dispute."}}
_pr.apply_adverse_triggers([{"main_clause_id": "vii", "sub_clauses": [_u7]}], 2022, {"vii"}, caro_text=_two)
check("B10 (Western Precicast 2022-23 (vii)(b)) the denial is printed twice; the disputed-dues table under the second "
      "copy is read (existing trigger)", _u7["classification"] == "adverse", _u7["classification"])

# F11
_src11 = ("According to the information and explanations given to us, nothing has come to our attention, which causes us "
          "to believe that any material uncertainty exists as on the date of the audit report and that the Company is "
          "not capable of meeting its liabilities existing at the date of balance sheet as and when they fall due.")
_a11 = {"xix": {"classification": "clean", "evidence": "nothing has come to our attention, which causes us to believe "
                "that any material uncertainty exists ... that the Company is not capable of meeting its liabilities"}}
_n11 = {}
st.expand_elided_quotes({"xix": {**st._empty_unit(), "source": _src11}}, _a11,
                        {"xix": {"main": "xix", "has_subs": False}}, _n11)
check("B11 (Apollo 2022-23 (xix)) a quote shortened with '...' becomes the auditor's whole sentence, so the "
      "going-concern trigger reads 'nothing has come to our attention' and no false Adverse is raised",
      "..." not in _a11["xix"]["evidence"] and _a11["xix"]["evidence"].startswith("According to the information"),
      _a11["xix"]["evidence"][:80])

# F16
# PATCH 13-Oct-2026 (Phase 2, F11): a quote stitched from two different points is never expanded.
from caro.staged_llm import _continuous_span as _cspan
_gc = ("(i) (a) The Company has maintained proper records showing full particulars, including quantitative details and situation of "
       "Property, Plant & Equipment. (b) The fixed assets were physically verified by the management during the year. (c) The title "
       "deeds are held in the name of the Company. (d) The Company has not revalued its Property, Plant and Equipment during the year.")
check("B11b (Garden Court 2022-23 (i)(a)(B), replay 13-Oct) a quote joining (i)(a) ... (d) is left as it is - the skipped words hold "
      "other clause markers - while one sentence shortened with ... is still expanded",
      _cspan("(i) (a) The Company has maintained proper records showing full particulars, including quantitative details and situation "
             "of Property, Plant & Equipment \u2026 (d) The Company has not revalued its Property, Plant and Equipment", _gc) is None
      and _cspan("(b) The fixed assets were physically ... during the year", _gc) == "(b) The fixed assets were physically verified by the management during the year.")
# PATCH 13-Oct-2026 (Phase 2, F33): (xvii) cash loss stated with an amount in a sentence that also says "not incurred".
from caro.post_rules import cash_loss_with_amount as _cla
check("B16 (Cellecor 2021-22 (xvii), CA review Adverse) a cash loss stated with a non-zero amount is found even when the "
      "same sentence says not incurred for the other year; a plain denial, Rs. Nil / 0.00 and a negated amount are not",
      bool(_cla("(xvii) The company has not incurred any cash lossesduring the previous financial year, and amounting to "
                "Rs. 2.42 Lacs cash losses in immediately preceding previous financial year."))
      and bool(_cla("The company has incurred cash losses of Rs. 45.20 lakhs in the current year."))
      and not _cla("The Company has not incurred any cash losses in the financial year and in the immediately preceding "
                   "financial year.")
      and not _cla("The company has not incurred cash losses of Rs. 0.00 during the year.")
      and not _cla("Cash losses: Rs. Nil in the current year and Rs. Nil in the preceding year.")
      and not _cla("The company has not incurred cash losses of Rs 5 lakhs as alleged."))
# PATCH 13-Oct-2026 (Phase 2, F34): (ix)(e)/(ix)(f) rule decided by Toby 13-Oct.
import caro.staged_llm as _s34
def _f34(uid, label, text):
    by = {"ix": {"main": "ix", "has_subs": True, "question": "", "subject_rx": None, "not_rx": None},
          uid: {"main": "ix", "has_subs": False, "question": "q", "subject_rx": None, "not_rx": None}}
    rc, tags, _m, _o = _s34.batch_notices({uid: {"classification": label, "evidence": "-"}},
                                         {uid: {"source": text}, "ix": {"source": ""}}, by, 2020, final=False, notes={})
    return "F34" in (tags.get(uid) or set())
check("B17 (Cellecor / Siddhivinayak / Viraj / Starlite, Toby 13-Oct) (ix)(e)/(f): 'has not raised any loans on pledge' "
      "marked NA is re-read as Favourable; 'does not have any subsidiary ... not applicable' marked Favourable is re-read as "
      "NA; 'does not have any joint venture and associates' (subsidiaries exist) is left Favourable; the rule is in Call 2 "
      "for CARO 2020 only",
      _f34("ix_f", "not_applicable", "(f) The company has not raised any loans during the year on the pledge of securities "
           "held in its subsidiaries or joint ventures or associate companies.")
      and _f34("ix_f", "clean", "f) The company does not have any subsidiary, associate or joint venture, and therefore, "
               "reporting under clause 3(ix)(f) of the said order is not applicable.")
      and not _f34("ix_f", "clean", "The Company has not raised loans during the year on the pledge of securities held in its "
                   "subsidiaries as defined under the Act. The Company does not have any joint venture and associates.")
      and not _f34("ix_f", "not_applicable", "f) The company does not have any subsidiary, associate or joint venture, and "
                   "therefore, reporting under clause 3(ix)(f) of the said order is not applicable.")
      and "(ix)(e) AND (ix)(f)" in _s34.build_call2_system(2020) and "(ix)(e) AND (ix)(f)" not in _s34.build_call2_system(2016))
_star = ("(e) According to the information and explanations given to us and on an overall examination of the financial "
         "statements of the Company, we report that the Company has not taken any funds from any entity or person on "
         "account of or to meet the obligations of its subsidiaries as defined under the Companies Act, 2013 Accordingly, "
         "clause 3(ix)(e) of the Order is not applicable.")
check("B17b (Starlite, Toby 13-Oct) no funds taken AND the clause declared not applicable -> Not Applicable is kept, "
      "Favourable is re-read; 'does not hold any investment in any joint venture' alone never triggers",
      _f34("ix_e", "clean", _star) and not _f34("ix_e", "not_applicable", _star)
      and not _f34("ix_e", "clean", "the Company has not taken any funds from any entity or person on account of or to meet "
                   "the obligations of its subsidiaries, associates or joint ventures. The Company does not hold any "
                   "investment in any joint venture (as defined under the Act) during the year ended 31 March 2024."))
import caro.post_rules as _pr34
_s34.PATCH_SWITCHES["F13"] = False
_off = _pr34._patch_on("F13")
_s34.PATCH_SWITCHES["F13"] = True
check("B18 rollback: every 12/13-Oct fix has a switch in PATCH_SWITCHES (post-rule fixes included) and turning one off "
      "is seen by the post-rules", _off is False and _pr34._patch_on("F13") is True
      and all(k in _s34.PATCH_SWITCHES for k in ("CUTOFF", "F05", "F09", "F10", "F11", "F12", "F13", "F14", "F15", "F16",
                                               "F19", "F33", "F34", "STEP_A", "NO_LABELS")))
check("B12 ((iii)(a) slots) 'parties other than subsidiaries, joint ventures and associates' and 'associate concern' "
      "are not (A) parties; 'Inter Corporate deposit to Associates' is",
      not st._names_sja("The Company has not provided any loans to parties other than subsidiaries, joint ventures "
                        "and associates.") and not st._names_sja("granted unsecured loan to an associate concern")
      and st._names_sja("The Company has also provided Inter Corporate deposit to Associates the tune of Rs. 1255 Lacs"))

# T01 / F27 on a full two-call run of the synthetic filing
_res, _nt, _rc, _ct = run_filing("synthetic_2020", Scripted(), "staged")
_subs = [s for c in _res["result"]["CARO_clauses"] for s in c["sub_clauses"]]
check("B13 (T01) every sub-clause carries a display title (39 CARO 2020 / 8 CARO 2016 titles in the clause map)",
      _subs and all(s.get("sub_clause_title") for s in _subs), [s["sub_clause_id"] for s in _subs if not s.get("sub_clause_title")])
_silent = [u for c in _res["result"]["CARO_clauses"] for u in [c] + c["sub_clauses"]
           if u["classification"] in ("Not Reported by Auditor", "Not Available in Source")]
check("B14 (F27, D15 option A) every silent unit shows exactly the standard sentence; 'Not provided by company' never",
      all(u["explanation_text"].startswith(("Not reported by the auditor:", "Not available in the source:")) for u in _silent),
      [u["explanation_text"][:60] for u in _silent][:3])

# --- PATCH 13-Oct-2026 (amounts): tests named after the filings that showed each problem ---------------
from caro import amounts as _AM, staged_llm as _SA
_AC = _AM                     # the amount checks live in caro/amounts.py


def _amt(uid, cls, items, text, fy_year):
    r = {"classification": cls, "evidence_text": text.split("\n")[0], "staged_trace": {}, "sub_clause_id": uid,
         "normalized_fields": {"source_text": text,
                               "amount_items": [{"as_printed": a, "counts_toward_total": True} for a in items]}}
    return _AM.compute(r, uid, text, text, fy_year)


_t_iii = ("(iii) (a) The Company has granted loans to the following parties during the year:\nParticulars\nGuarantees\n"
          "Loans\nAdvances\nAggregate amount granted during the year\nSubsidiaries\n-\n1,250.00\n-\nOthers\n-\n310.00\n-\n"
          "Balance outstanding as at balance sheet date\nSubsidiaries\n500.00\n2,358.39\n-\nOthers\n-\n1,798.32\n-\n"
          "(b) The terms are not prejudicial.\n")
check("AM1 (WickedRide / Shri Dutt (iii)(a)) the (iii)(a) table is read by row label: (A) = subsidiaries' balance "
      "loan 2,358.39 (guarantee 500 not added), (B) = others' balance 1,798.32; flow rows never used",
      _AC.iii_a_slot_figures(_t_iii, "A")[0] == ["2,358.39"] and _AC.iii_a_slot_figures(_t_iii, "B")[0] == ["1,798.32"])
_t_iib = ("(ii)(b) The quarterly statements filed with banks differ from the books as under:\nQuarter\nAmount as per books\n"
          "Amount as per statement\nDifference\nJun-23\n10,000.00\n9,000.00\n1,000.00\nMar-24\n12,500.00\n10,202.00\n2,298.00\n"
          "(iii) The Company has not granted loans.\n")
check("AM2 (Shri Dutt (ii)(b)) the difference column of the year-end quarter is the amount (not the sum of books, "
      "statement and all quarters)", (_AC.ii_b_difference(_t_iib, [_t_iib.find("2,298.00")]) or [None])[0] == "2,298.00")
_t_vii = ("(vii)(b) Details of dues not deposited on account of dispute:\nName of statute\nNature\nAmount (Rs.)\n"
          "Amount paid under protest (Rs.)\nForum\nIncome Tax Act\nIncome tax\n2,068,873\n219,713\nCIT(A)\n")
_r = _amt("vii_b", "adverse", ["2,068,873", "219,713"], _t_vii, 2021)
check("AM3 (Aditya 2020-21 (vii)(b)) the 'paid under protest' column is never added to the disputed amount; an "
      "'Amount (Rs.)' header line is a column, not a unit note", _r["amount_total"] == 2068873.0, _r)
_t_ix = ("(ix)(a) The Company has defaulted in repayment of loans as under (Rs. in lakh):\nNature\nName of lender\n"
         "Amount not paid on due date\nWhether principal or interest\nNo. of days delay\nTerm loan\nBank A\n120.50\n"
         "Principal\n30\nTerm loan\nBank A\n14.20\nInterest\n30\nThe balance of the term loan outstanding is Rs. 9,500.00 lakh.\n")
_r = _amt("ix_a", "adverse", ["120.50", "14.20", "Rs. 9,500.00 lakh"], _t_ix, 2023)
check("AM4 (Viraj Canes / Viraj Alchohol (ix)(a)) principal AND interest instalments not paid are added; the loan's "
      "outstanding balance is not a default amount", _r["amount_total"] == 13470000.0, _r)
_r = _amt("i_c", "qualified", ["110"], "(i)(c) The title deeds of 110 acre of land are not held in the name of the Company.\n", 2023)
check("AM5 (Shri Dutt (i)(c)) an area ('110 acre') is never an amount", _r["amount_total"] is None, _r)
_SA.PATCH_SWITCHES["AMT_PROMPT"] = False
_off_amt = "AMOUNT ITEMS" in _SA.build_call1_system(2020)
_SA.PATCH_SWITCHES["AMT_PROMPT"] = True
check("AM6 Call 1 carries the AMOUNT ITEMS rules (two-call only, switch AMT_PROMPT); the single prompt is untouched; "
      "every amount fix has a switch",
      "AMOUNT ITEMS" in _SA.build_call1_system(2020) and not _off_amt
      and "AMOUNT ITEMS" not in __import__("caro.prompts", fromlist=["x"]).__dict__.get("SYSTEM_PROMPT", "")
      and all(_AM.amt_switch_on(k) for k in ("TABLE_COLUMNS", "III_A_TABLE", "II_B_DIFF", "NOT_MONEY", "BALANCE_NOT_DEFAULT")))
_single = {"classification": "adverse", "evidence_text": "x", "sub_clause_id": "vii_b",
           "normalized_fields": {"source_text": _t_vii, "amount_items": [{"as_printed": "2,068,873", "counts_toward_total": True},
                                                                       {"as_printed": "219,713", "counts_toward_total": True}]}}
check("AM7 the new amount checks run only on two-call records (no staged_trace -> single flow unchanged)",
      not _AC.is_staged(_single) and _AM.compute(_single, "vii_b", _t_vii, _t_vii, 2021)["amount_total"] != 2068873.0)

# --- PATCH 13-Oct-2026 (mapping / crosswalk / Option C): tests named after the filings ---------------
from caro import post_rules as _PRm, prompts as _PPm
_sas = ("1. Property, Plant and Equipments (Fixed Asset):\n(a) records maintained.\n9. Default in repayment of borrowings:\n"
        "(a) The company has not defaulted. (b) not a wilful defaulter. (d) funds raised on short term basis have not been "
        "used for long-term purposes.\nthe company has not raised moneys by way of initial public offer or further public offer "
        "including debt instruments and term loans.\n11. Fraud and whistle-blower complaints. No report under section "
        "143(12) in Form ADT-4. 14. Internal Audit: internal audit system commensurate. 17. Cash Losses: no cash losses. "
        "18. Resignation of Statutory Auditors: there has been no resignation of the statutory auditors. 19. capable of "
        "meeting its liabilities. 20. no unspent amount. benami property: none.")
_hyb = ("(viii) The company has not defaulted in repayment of dues to banks.\n(x) No fraud on or by the company has been "
        "noticed.\n(xi) Managerial remuneration has been paid in accordance with section 197.\n(xiv) The company has not "
        "made any preferential allotment or private placement.\n(xvii) The company has not incurred cash losses.\n(xx) no "
        "unspent amount under section 135(5).")
_cit = _sas + " Accordingly clause 3(x)(a) is not applicable; clause 3(xvi)(d) is not applicable."
_mix = ("viii) the company has not defaulted the repayment of dues to banks.\nx) the company has not raised money by way of "
        "initial public offer or further public offer.\nxi) no fraud on or by the Company has been noticed.\n")
check("MAP1 (Saspack / U72900DL2007PTC162708 / Garden Court 2024-25) a CARO 2020-layout report is never given the CARO "
      "2016 crosswalk; real hybrids (Western / Jsons / Atharv) keep it; mixed numbering (Prasad 2022-23) gets the "
      "subject-only note; CARO 2016 years untouched",
      st.layout_recheck(_sas, "hybrid", 2023)[0] is None and st.layout_recheck(_cit, "hybrid", 2023)[0] is None
      and st.layout_recheck(_hyb, "hybrid", 2022)[0] == "hybrid" and st.layout_recheck(_mix, None, 2023)[0] == "mixed"
      and st.layout_recheck(_sas, "2016", 2019)[0] == "2016"
      and "NUMBERING NOTE" in st.build_call1_system(2023, crosswalk_layout="mixed")
      and "NUMBERING NOTE" not in st.build_call1_system(2023))
_c16 = {u["unit"]: u for u in st.catalogue_units(2016)}
check("MAP2 (clause map) every CARO 2016 unit carries the subject words of its CARO 2020 successor; (xi) managerial "
      "remuneration uses the M9 words; (ix) covers public offer AND term loans",
      all(u["subject_rx"] for u in _c16.values()) and "section 197" in _c16["xi"]["subject_rx"]
      and "term loan" in _c16["ix"]["subject_rx"] and "public offer" in _c16["ix"]["subject_rx"])
_c20 = {u["unit"]: u for u in st.catalogue_units(2020)}
_j17 = st.own_subject_check("xvii", _c20["xvii"], "The company is capable of meeting its liabilities existing at the date "
                            "of Balance Sheet as and when they fall due within a period of one year.", "", _c20)
_kr = st.own_subject_check("iii_f", _c20["iii_f"], "(f) The company has granted loans or advances in the nature of loans "
                           "either repayable on demand or without specifying any terms or period of repayment, if so, "
                           "specify the aggregate amount", "(iii) the passage", _c20)
check("MAP3 (F35, Jsons 2021-22 (xvii) / U51909 (iv)) own text on another unit's subject -> one recheck note naming it; "
      "a bare 'Not applicable', a blanket 'has not granted any loans' and an on-subject answer -> no note",
      _j17 and "xix" in _j17[1]
      and st.own_subject_check("iv", _c20["iv"], "The company has not granted any loans, secured or unsecured, to "
                               "companies covered in the register maintained under Section 189. Therefore clause (iii)(a) "
                               "to (f) are not applicable.", "", _c20) is not None
      and st.own_subject_check("v", _c20["v"], "Not applicable", "", _c20) is None
      and st.own_subject_check("iii_b", _c20["iii_b"], "During the year the company has not made any investment in, "
                               "provided any guarantee or granted any loan or advance in the nature of loans.", "x", _c20) is None
      and st.own_subject_check("xvii", _c20["xvii"], "The company has not incurred cash losses.", "", _c20) is None)
_u = {"iii": dict(st._empty_unit(), source="Except for business purpose.\nThe company has granted loans to employees."),
      "iii_a_A": dict(st._empty_unit(), source="Except for business purpose."), "iii_a_B": st._empty_unit()}
_nn = {}
st.apply_moves(_u, {"iii_a_A": {"moves": [{"to": "iii_a_B", "text": "The company has granted loans to employees."}]}},
               [], [], "x", st.catalogue_units(2023), _nn)
check("MAP4 (Prasad 2023-24 (iii)(a)) a Call 2 move of words that are in the clause passage (shown as 'same as passage') "
      "is applied to the unit they answer; nothing is removed from the passage",
      "employees" in _u["iii_a_B"]["source"] and "employees" in _u["iii"]["source"]
      and _nn["moves"][0]["result"] == "placed from the clause passage", _nn.get("moves"))
_sys1, _sys2 = st.build_call1_system(2023), st.build_call2_system(2016)
st.PATCH_SWITCHES["ONE_UNIT"] = False
_off1 = "ONE UNIT, ONE ANSWER" in st.build_call1_system(2023)
st.PATCH_SWITCHES["ONE_UNIT"] = True
check("MAP5 Call 1 'ONE UNIT, ONE ANSWER' and Call 2 'WHOLE TEXT, OWN QUESTION' (both Orders); switch ONE_UNIT; the single "
      "prompt has neither", "ONE UNIT, ONE ANSWER" in _sys1 and "WHOLE TEXT, OWN QUESTION" in _sys2 and not _off1
      and "ONE UNIT" not in _PPm.build_system_prompt(2023) and "WHOLE TEXT" not in _PPm.build_system_prompt(2016))


def _og(staged):
    sub = {"sub_clause_id": "i_b", "classification": "qualified",
           "evidence_text": "physical verification was carried out and no material discrepancies were noticed.",
           "normalized_fields": {"source_text": "The fixed assets were physically verified and no material discrepancies were "
                                 "noticed, except that assets at the Pune plant could not be verified."}}
    if staged:
        sub["_staged"] = True
    rec = {"main_clause_id": "i", "classification": "qualified", "evidence_text": "-", "sub_clauses": [sub],
           "normalized_fields": {"source_text": "(i) the clause passage"}}
    _PRm.apply_overgrade_guard([rec], 2023)
    return sub["classification"]


check("MAP6 (Option C) a two-call Qualified is not softened from its quote when the unit's own text states the exception "
      "elsewhere; the single flow behaves as before", _og(True) == "qualified" and _og(False) == "clean")
_own = ("Details of loans given:\nParticulars\nGuarantees\nLoans\nAggregate amount during the year\nSubsidiaries\n500.00\n"
        "1,250.00\nBalance outstanding\nSubsidiaries\n-\n2,358.39\n")
check("MAP7 (WickedRide 2022-23 (iii)(a)(A)) a table quote the model re-wrote ('(Loans) 2,358.39') becomes the auditor's own "
      "lines holding the same figures", (st._table_block(_own, "Subsidiaries (Guarantees) 500.00 (Loans) 1,250.00 ... 2,358.39")
                                          or "").endswith("2,358.39") and st._table_block(_own, "(Loans) 9,999.00 2,358.39") is None)

# --- PATCH 13-Oct-2026 evening batch (P1 P2 P4 P5 P6 P7A-E P8 P10 P11 P13 P15 P16 P18): tests named after filings ---
_by20, _by16 = {u["unit"]: u for u in st.catalogue_units(2023)}, {u["unit"]: u for u in st.catalogue_units(2019)}


def _bn(by, units, ans, oy=2023):
    rc, tags, _m, _s = st.batch_notices(ans, {k: dict(st._empty_unit(), source=v) for k, v in units.items()}, by, oy,
                                       final=False, notes={})
    return rc, tags


_star_vii = ("(vii) (a) Undisputed statutory dues have generally been regularly deposited.\n(b) According to the information "
             "and explanations given to us, no undisputed amounts payable were in arrears for more than six months.\n(c) "
             "According to the records of the Company, the dues outstanding in respect of sales tax on account of any "
             "dispute are as follows:\nName of Statute\nAmount\nThe Andhra Pradesh General Sales Tax\n24.27\n1996-97\n"
             "Honble High Court of Telangana")
_rc, _tg = _bn(_by16, {"vii": _star_vii, "vii_b": "(b) According to the information and explanations given to us, no "
                       "undisputed amounts payable were in arrears for more than six months."},
               {"vii_b": {"classification": "clean", "evidence": "x", "explanation": "x"}}, 2019)
_rc2, _ = _bn(_by16, {"vii": "(vii) (a) regular.\n(b) There are no dues of income tax which have not been deposited on "
                      "account of any dispute.", "vii_b": "(a) regular."},
              {"vii_b": {"classification": "clean", "evidence": "x", "explanation": "x"}}, 2019)
check("P1 (TRIAL, Starlite 2020-21 CARO 2016) (vii)(b) with no dispute text gets a note quoting the dispute sentence and "
      "table of the (vii) paragraph; a denial of disputed dues never triggers it",
      "P1" in _tg.get("vii_b", set()) and "24.27" in _rc.get("vii_b", "") and "P1" not in str(_rc2), (_rc, _rc2))
_sd = ("(vii) (a) undisputed statutory dues have been regularly deposited, except as given in Para(b) below.\n(b)\n"
       "undisputed amounts payable in respect of provident fund were outstanding as at March 31, 2024 for a period of more "
       "than six months from the date they became payable:\nProvident Fund\n4.14\n(c) no disputed dues.")
_rc, _tg = _bn(_by20, {"vii": _sd, "vii_a": "undisputed statutory dues have been regularly deposited, except as given in "
                       "Para(b) below."}, {"vii_a": {"classification": "qualified", "evidence": "x", "explanation": "x"}})
check("P16 (TRIAL, Shri Dutt 2023-24 (vii)(a)) 'except as given in Para(b) below' -> the note quotes Para (b) of the same "
      "clause; the label is the model's", "P16" in _tg.get("vii_a", set()) and "Provident Fund" in _rc.get("vii_a", ""), _rc)
def _na_unit(uid, txt, staged=True):
    sub = {"sub_clause_id": uid, "classification": "not_applicable", "evidence_text": "Hence reporting under clause "
           "3(xi)(a) to (c) is not applicable.", "normalized_fields": {"source_text": txt}}
    if staged:
        sub["_staged"] = True
    _PRm.apply_na_clean_table([{"main_clause_id": uid.split("_")[0], "classification": "not_applicable",
                                "evidence_text": "-", "sub_clauses": [sub], "normalized_fields": {"source_text": "x"}}], 2023)
    return sub["classification"]


_wr = "No fraud by the company or on the company has been noticed or reported during the year. Hence reporting under clause 3(xi)(a) to (c) is not applicable."
_rc, _tg = _bn(_by20, {"ix": "x", "ix_c": "The term loans were applied for the purpose for which they were obtained. Hence "
                       "clause 3(ix)(c) is not applicable."},
               {"ix_c": {"classification": "not_applicable", "evidence": "x", "explanation": "x"}})
_rc2, _tg2 = _bn(_by20, {"ix": "x", "ix_c": "The company does not have any term loans. Hence clause 3(ix)(c) is not "
                         "applicable; applied for the purpose."}, {"ix_c": {"classification": "not_applicable",
                                                                            "evidence": "x", "explanation": "x"}})
check("P2 (WickedRide 2023-24 (xi)(b)) a favourable finding in the unit's own text + 'hence not applicable' in the quote -> "
      "Favourable by the NA/clean table (two-call only); (ix)(c) gets one re-read; a real not-arising answer does not; "
      "the prompt line is CARO 2020 only",
      _na_unit("xi_b", _wr) == "clean" and _na_unit("xi_b", _wr, staged=False) == "not_applicable"
      and "P2" in _tg.get("ix_c", set()) and "P2" not in _tg2.get("ix_c", set())
      and "ONLY for (ix)(e) and (ix)(f)" in st.build_call2_system(2023)
      and "ONLY for (ix)(e) and (ix)(f)" not in st.build_call2_system(2019))
check("P4 / P15 / P18 prompt lines: (iii)(a) judged on the amounts only (missing amounts = incomplete); (viii) convention; "
      "rights issue / allotment without compliance statement; none in the single prompt",
      all(x in st.build_call2_system(2023) for x in ("ask ONLY the aggregate amount", "(viii): 'there are no transactions",
                                                     "a rights issue alone"))
      and "CARO 2016 (xiv): a rights issue" in st.build_call2_system(2019)
      and "a rights issue alone" not in _PPm.build_system_prompt(2023))
_rc, _tg = _bn(_by20, {"iii": "x", "iii_a_B": "The Company has granted loans to its subsidiaries amounting to Rs. 5 lakh."},
               {"iii_a_B": {"classification": "clean", "evidence": "The Company has granted loans to its subsidiaries "
                            "amounting to Rs. 5 lakh.", "explanation": "x"}})
check("P6 (Shri Dutt 2024-25 (iii)(a)(B)) the F16 note asks to move ONLY the words that name subsidiaries and keep (B)'s "
      "own denial; the 90-character look-back is unchanged (replay: a wider one removed a correct note)",
      "Move ONLY the words" in _rc.get("iii_a_B", ""), _rc)
_uc = ("Based on our examination, the provision of section 135 are applicable on the company but has not been complied "
       "with also proper information has not been provided to report the required transactions/figures .")


def _csr(staged):
    sub = {"sub_clause_id": "xx_a", "classification": "qualified", "evidence_text": _uc,
           "normalized_fields": {"source_text": _uc}}
    if staged:
        sub["_staged"] = True
    _PRm.apply_adverse_triggers([{"main_clause_id": "xx", "classification": "qualified", "evidence_text": "-",
                                  "sub_clauses": [sub], "normalized_fields": {}}], 2023, set(), None)
    return sub["classification"], sub.get("review_reasons") or []


_a, _b = _csr(True), _csr(False)
check("P7A (TRIAL, Universal Contractors 2021-22 (xx)) 's.135 applicable but has not been complied with' -> adverse with "
      "the review reason trial:P7A, two-call records only (single flow unchanged)",
      _a[0] == "adverse" and "trial:P7A" in _a[1] and _b[0] == "qualified", (_a, _b))
_rc, _tg = _bn(_by20, {"xvi": "x", "xvi_a": "The particulars of registration are as under:"},
               {"xvi_a": {"classification": "clean", "evidence": "x", "explanation": "x"}})
_rc3, _tg3 = _bn(_by20, {"vii": "x", "vii_a": "Undisputed dues are regular. Dues payable for more than six months: "
                         "Provident Fund 4,14,000 for 2023-24."},
                 {"vii_a": {"classification": "qualified", "evidence": "x", "explanation": "x"}})
_rc4, _tg4 = _bn(_by20, {"xiv": "x", "xiv_a": "The company is required to have an internal audit system but internal audit "
                         "has not been carried out during the year."},
                 {"xiv_a": {"classification": "clean", "evidence": "x", "explanation": "x"}})
_rc5, _tg5 = _bn(_by20, {"vi": "The company maintains cost records but the details of the cost audit are not stated."},
                 {"vi": {"classification": "insufficient_evidence", "evidence": "", "explanation": "The answer is "
                         "incomplete: the outcome is not stated."}})
_rc6, _tg6 = _bn(_by20, {"xiii": "All transactions with related parties are in compliance with sections 177 and 188."},
                 {"xiii": {"classification": "clean", "evidence": "x", "explanation": "Compliance is implied by the "
                           "disclosure."}})
check("P7B-E (TRIAL) / P8 notes: particulars announced but not given; arrears > 6 months listed under (vii)(a); internal "
      "audit required but not carried out; 'insufficient' with an 'incomplete' explanation; a guessing explanation - each "
      "one recheck note, no label set by code",
      "P7B" in _tg.get("xvi_a", set()) and "P7C" in _tg3.get("vii_a", set()) and "P7D" in _tg4.get("xiv_a", set())
      and "P7E" in _tg5.get("vi", set()) and "P8" in _tg6.get("xiii", set()))


def _nat(uid, q, staged=True):
    sub = {"sub_clause_id": uid, "classification": "clean", "evidence_text": q, "normalized_fields": {"source_text": q}}
    if staged:
        sub["_staged"] = True
    _PRm.apply_na_clean_table([{"main_clause_id": uid.split("_")[0], "classification": "clean", "evidence_text": "-",
                                "sub_clauses": [sub], "normalized_fields": {}}], 2023)
    return sub["classification"]


check("P10 (Starlite 2024-25 (iii)(c), Apollo 2021-22 (iii)(b)) no Not Applicable from a sentence with 'except ... below' "
      "or from the (f) on-demand sentence; P11 (Universal Contractors (i)(a)(B)) 'has not any intangible assets' -> NA "
      "(two-call only)",
      _nat("iii_c", "The Company has not granted any loans except for the details given below.") == "clean"
      and _nat("iii_b", "f) The Company has not granted any loans or advances repayable on demand or without specifying "
                        "any terms.") == "clean"
      and _nat("i_a_B", "The company has not any intangible assets.") == "not_applicable"
      and _nat("i_a_B", "The company has not any intangible assets.", staged=False) == "clean")
check("P11 (Sahyadri 2024-25 (x)(b)) a box holding only 'As mentioned in the Audit report' points to text not in the input "
      "(two-call only); P11 (Prasad 2020-21 (ii)) 'not provided by the management ... unable to say' is a scope limitation",
      _PRm.points_to_attachment({"_staged": True, "evidence_text": "As mentioned in the Audit report",
                                 "normalized_fields": {}})
      and not _PRm.points_to_attachment({"evidence_text": "As mentioned in the Audit report", "normalized_fields": {}})
      and st._SCOPE_MEANING.search("the stock records are not provided by the management, hence we are unable to say"))
_loop = ("There are no unrecorded transactions. " + "According to the information given to us, the Company has not made "
         "any default in repayment of loan. Accordingly, the provisions of clause 3(ix) are not applicable. " * 40)
_cut, _did = st.cut_runaway(_loop, "There are no unrecorded transactions. According to the information given to us, the "
                            "Company has not made any default in repayment of loan. Accordingly, the provisions of "
                            "clause 3(ix) are not applicable.")
_sch = st.call1_schema(st.source_limit("x" * 7826))
check("P13 (K.R. Foods 2021-22) Call-1 source_text limit = 6,000 characters (Toby 13-Oct; second attempt 7,200); a "
      "looping field is cut at the first repeated sentence; a field the filing really prints twice is kept",
      _did and _cut.count("not made any default") == 1 and st.source_limit("x" * 7826) == 6000
      and st.source_limit("x" * 100) == 6000 and int(st.source_limit("x") * 1.2) == 7200
      and _sch["properties"]["clauses"]["items"]["properties"]["normalized_fields"]["properties"]["source_text"]["maxLength"] == 6000
      and not st.cut_runaway("A long sentence that the auditor prints twice on purpose here. A long sentence that the "
                             "auditor prints twice on purpose here.", "A long sentence that the auditor prints twice on "
                             "purpose here. A long sentence that the auditor prints twice on purpose here.")[1])
_rm = {}
st._batch_marks(_rm, "vii_b", {"patch_units": {"vii_b": ["P1", "F09"]}}, set())
check("TRIAL flags: a unit touched by a trial fix carries the review reason 'trial:<fix>' (an existing review value, no new "
      "JSON key); TRIAL_FIXES lists P1, F35 (P3), P16, P7A-P7E",
      "trial:P1" in (_rm.get("review_reasons") or []) and {"P1", "F35", "P16", "P7A", "P7E"} <= st.TRIAL_FIXES)

def _amt_f(uid, cls, items, text, fy_year):
    r = {"classification": cls, "evidence_text": text.split("\n")[0], "staged_trace": {}, "sub_clause_id": uid,
         "normalized_fields": {"source_text": text, "amount_items": [dict(as_printed=a, counts_toward_total=False, note=n)
                                                                    for a, n in items]}}
    return _AM.compute(r, uid, text, text, fy_year)


_t_kr = ("(vii)(b) Dues not deposited on account of dispute: Name of statute Nature Amount Forum Delhi VAT Act VAT "
         "Rs 20,95,74,867 Additional Commissioner\n")
_t_ad = ("(vii)(b) Dues not deposited on account of dispute: Name of the Statute Nature of Dues Amount Disputed (Rs. in "
         "lakhs) Period Forum Tamil Nadu VAT Act, 2006 VAT 2011-12, 2012-13 Appellate Assistant Commissioner Income Tax "
         "Act, 1961 Income Tax AY 2013-14 & AY 2015-16 Commissioner Appeals\n")
_r1 = _amt_f("vii_b", "adverse", [("Rs 20,95,74,867", "VAT disputed")], _t_kr, 2025)
_r2 = _amt_f("vii_b", "adverse", [("2011-12, 2012-13", "VAT Period"), ("AY 2013-14 & AY 2015-16", "Income Tax Period")],
             _t_ad, 2024)
check("V4-A (trial K.R. Foods 2024-25 (vii)(b); replay Aditya Auto 2023-24 (vii)) every figure marked 'not counted': "
      "money figures are recovered (Rs 20,95,74,867), periods of a dues table ('2011-12, 2012-13', 'AY 2013-14') never",
      _r1["amount_total"] == 209574867.0 and _r2["amount_total"] is None, (_r1, _r2))
_t_5c = ("(ii)(b) During the year, the Company has been sanctioned Working Capital limits in excess of Rs 5 Crore in "
         "aggregate from banks on the basis of security of current assets. Further there are difference in quarterly "
         "returns filed with bank as per the books of account. The balance as per stock statement submitted to bank was "
         "Rs. 833.43 Lakh and as per audited books of account Rs. 767.79 Lakh.\n")
_r3 = _amt_f("ii_b", "qualified", [("Rs 5 Crore", "limit"), ("Rs. 833.43 Lakh", "statement"),
                                   ("Rs. 767.79 Lakh", "books")], _t_5c, 2023)
check("V4-B (replay U51909DL2004PTC127807 2022-23 (ii)(b)) the Order's Rs 5 crore threshold is not a balance: the "
      "amount is the books-vs-statement difference Rs 65.64 lakh, not the larger balance",
      _r3["amount_total"] == 6564000.0, _r3)
_kr = ("(A) the aggregate amount ... The Following Loan & Advances are Repayment on Demand \u2026 (d) If the amount is "
       "overdue, state the total amount overdue; The management has not provided us with the relevant data and records "
       "regarding overdue amounts. Consequently, we are unable to comment on the total amount overdue")
_js = ("b) As informed to us, all the Property, Plant & Equipment have been physically verified by the management. It is "
       "not possible to us to verify all the Fixed Assets owned by the company considering the quantity and volume.")
_vj = "Since no repayment terms being prescribed, we are unable to comment the overdue amounts, if any."
check("V4-C (trial K.R. Foods 2024-25 (iii)(a)(A); replay Jsons / Western Precicast (i)(b), Viraj (iii)(e)) a "
      "could-not-verify sentence printed after ANOTHER sub-clause's letter is that sibling's; a sentence in the unit's "
      "own answer stays the unit's (Auditor Unable to Verify kept)",
      _PRm._in_sibling_segment(_kr, "The management has not provided us with the relevant data", "iii_a_A")
      and st._in_sibling_segment([_kr], "Consequently, we are unable to comment", "iii_f")
      and not _PRm._in_sibling_segment(_js, "It is not possible to us to verify all the Fixed Assets", "i_b")
      and not _PRm._in_sibling_segment(_vj, "Since no repayment terms being prescribed", "iii_e"))

check("V4-D (trial K.R. Foods 2021-22 (iv)) P8: 'indicating compliance' is a guess and gets the one re-read note; the "
      "auditor's own (xix) wording 'nothing has come to our attention indicating material uncertainty' does not",
      st._GUESS_WORDS.search("the funds were utilized for the stated purpose, indicating compliance")
      and not st._GUESS_WORDS.search("nothing has come to the auditor's attention indicating material uncertainty"))

check("V6-0 shipped config: the 13-Oct prompts (AMT_PROMPT, XWALK_LAYOUT, SUBJ_2016, ONE_UNIT, P2_PROMPT, P4, P15, "
      "P18 off), P7B off, every post-13-Oct recheck note off (F35, P1, P2_NOTE, P6, P7C, P7D, P7E, P8, P13, P16, "
      "GUARD_INCOMPLETE_NOTE); the code fixes (P2 table, P7A, P11, F34, NA_PUSH_V5, GUARD_INCOMPLETE final guard, "
      "SCOPE_OWN, RECHECK_GUARD, amounts) on",
      all(_SHIPPED.get(k) is False for k in _V5_FROZEN + _V6_FROZEN) and all(_SHIPPED.get(k) is True for k in
          ("P2", "P7A", "P11", "F34", "NA_PUSH_V5", "GUARD_INCOMPLETE", "SCOPE_OWN", "RECHECK_GUARD"))
      and st.RECHECK_MAX_TOKENS == 12000)
check("V5-1 (14-Oct run, Viraj Canes (xix) x4) P8: 'the going concern assumption' is the auditor's own term, never a "
      "guess; 'implying', 'we assume', 'based on the assumption that' still are",
      not st._GUESS_WORDS.search("the going concern assumption is going to continue")
      and not st._GUESS_WORDS.search("the assumption of going concern is appropriate")
      and st._GUESS_WORDS.search("implying compliance") and st._GUESS_WORDS.search("we assume no dues")
      and st._GUESS_WORDS.search("based on the assumption that no loans exist"))
_t7c = ("There were no undisputed amounts payable in respect of Provident Fund, Income Tax, Goods and Service Tax. Customs "
        "Duty, Excise Duty, Cess and other material statutory dues in arrears as at March 31, 2024 for a period of more "
        "than six months from the date they became payable.")
_n7 = {}
_r7, _t7, _x7, _s7 = st.batch_notices({"vii_a": {"classification": "clean", "explanation": "No arrears."}},
                                 {"vii_a": dict(st._empty_unit(), source=_t7c), "vii": dict(st._empty_unit(), source=_t7c)},
                                 {u["unit"]: u for u in st.catalogue_units(2024)}, 2024, final=False, notes=_n7)
check("V5-2 (14-Oct run, Viraj Canes 2023-24 (vii)(a)) P7C: a denial cut by a stray full stop ('... Goods and Service "
      "Tax. Customs Duty ... in arrears ... more than six months') gets no arrears note; a date is not a money figure",
      "P7C" not in (_t7.get("vii_a") or set()) and not st._MONEY_FIG.search("as at March 31, 2024")
      and st._MONEY_FIG.search("Rs. 39,75,317") and st._MONEY_FIG.search("4.11 lakhs"))
check("V5-3 (14-Oct run, Siddhivinayak (vii)(b)) P1: 'except for the following: Nil' is an answer, not dispute text "
      "left in the paragraph; (K.R. Foods 2023-24 (iii)(a)(B)) a heading + the printed question is not an answer",
      st._dispute_text("(b) there are no dues of GST ... which have not been deposited by the Company on account of "
                       "disputes, except for the following: Nil", "") is None
      and st._is_question_text("The Following Loan & Advances are Repayment on Demand: the aggregate amount during the "
                               "year, and balance outstanding at the balance sheet date with respect to such loans or "
                               "advances and guarantees or security to parties other than subsidiaries, joint ventures "
                               "and associates;", st.catalogue_units(2024)[[u["unit"] for u in st.catalogue_units(2024)].index("iii_a_B")]["question"]))
_mA = {"main_clause_id": "iii", "_staged": True, "classification": "not_applicable",
       "evidence_text": "The Company has provided loan during the current financial year. e) The Company has not granted "
                        "any loan which has fallen due during the year.", "normalized_fields": {}}
_mS = {"main_clause_id": "iii", "_staged": True, "classification": "not_applicable",
       "evidence_text": "The company do not have any subsidiary, associates & joint venture during financial year 2024-25 "
                        "Accordingly reporting under paragraph 3(ix)(e) and 3(ix)(f) is not applicable.", "normalized_fields": {}}
_mN = {"main_clause_id": "iii", "_staged": True, "classification": "not_applicable",
       "evidence_text": "The Company has not made any investments, provided any guarantee or security or granted any "
                        "loans during the year. Accordingly clause 3(iii) is not applicable.", "normalized_fields": {}}
check("V5-4 (14-Oct run, Apollo 2022-23 / Sahyadri 2024-25 (iii)(a)) Not Applicable is not passed down from a main "
      "clause that says loans WERE provided, or whose NA sentence is about another clause (3(ix)); a real whole-clause "
      "denial still is", not _PRm.na_covers(_mA, "iii_a_A", 2023) and not _PRm.na_covers(_mS, "iii_a_B", 2024)
      and _PRm.na_covers(_mN, "iii_a_A", 2023))

# ---- v6 (14-Oct trial) -----------------------------------------------------------------------------------------
class _RecheckTimeout(GoldModel):
    """Siddhivinayak 2024-25: the recheck request timed out after 600 s and the whole filing was lost."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.max_tokens, self.recheck_cap = 30000, None
    def chat(self, system, user, schema=None):
        if system.startswith(CALL2) and '"recheck":' in user:
            self.recheck_cap = self.max_tokens
            raise TimeoutError("timed out")
        return super().chat(system, user, schema)
_gt = _RecheckTimeout(_ctj.replace("\t", "    "), by_number=True)
_rt, _nt, _rr, _ = run_filing("jsons_2020", _gt, "staged_nofallback")
_n2t = (_nt.get("staged") or {}).get("call2") or {}
check("V6-1 (14-Oct trial, Siddhivinayak 2024-25) a timeout in the recheck keeps the first Call 2 answers (no failed "
      "filing, no fallback); the recheck reply is capped at 12,000 tokens and the client's budget is restored",
      _gt.recheck_cap == 12000 and _gt.max_tokens == 30000 and not _nt.get("stage_fallback")
      and "TimeoutError" in str(_n2t.get("recheck_failed") or _nt) and len(flat(_rt)) > 30,
      (_gt.recheck_cap, _gt.max_tokens, _nt.get("stage_fallback"), str(_n2t.get("recheck_failed"))[:80]))
from caro import amounts as _AM                                                              # noqa: E402
check("V6-2 (Toby 14-Oct) only numeric units scale a figure: '(Rs. in hundreds)' x100, \"(Rs. in '000)\" / '(₹ ’000)' "
      "x1,000, lakh / crore / thousand / million as before; 'rupees five hundred crore' (no digits) and land / period "
      "words are never a heading",
      _AM.unit_heading("(Rs. in hundreds)") == 100 and _AM.unit_heading("(Rs. in '000)") == 1000
      and _AM.unit_heading("(₹ ’000)") == 1000 and _AM.unit_heading("Amount in Rs. '000") == 1000
      and _AM.unit_heading("(Rs. in Lakhs)") == 1e5 and _AM.unit_heading("(Rs. in Crore)") == 1e7
      and _AM.unit_heading("Amount (Rs.) Thousand") == 1e3 and _AM.unit_heading("(₹ in Million)") == 1e6
      and _AM.unit_heading("net worth of rupees five hundred crore or more") == 1.0
      and _AM.unit_heading("110 acre – 13 Ghunte") == 1.0 and _AM.to_rupees("Rs. 5 hundred") == 500
      and _AM.to_rupees("Rs. 1,00,000") == 100000 and _AM.to_rupees("1,250", 1e3) == 1250000)
def _amt(uid, text, items):
    rec = {"staged_trace": {}, "classification": "qualified", "evidence_text": text[:80],
           "normalized_fields": {"amount_items": [{"as_printed": p, "counts_toward_total": True, "note": ""} for p in items]}}
    return _AM.compute(rec, uid, text, text, 2020).get("amount_total")
check("V6-3 (Toby 14-Oct) a land area (acre, bigha, guntha, kanal, marla, cents, hectare, sq. ft) or a count / rate / "
      "period (shares, numbers, %, days) is never rupees even when the model counts it; the money figure beside it is",
      _amt("i_c", "(c) Title deeds of land measuring 12 kanal at Ludhiana are not in the name of the Company.", ["12"]) is None
      and _amt("i_c", "(c) Title deeds of land of 18 marla at Amritsar are not in the name of the Company.", ["18"]) is None
      and _amt("i_c", "(c) Title deeds of 45 cents of land at Kochi are not in the name of the Company.", ["45"]) is None
      and _amt("i_c", "(c) Title deeds of 25 bigha of land at Jaipur are not held in the name of the Company.", ["25"]) is None
      and _amt("i_c", "(c) Title deeds of land of 13 guntha, gross carrying value Rs. 361.75 lacs, are not in the name "
               "of the Company.", ["13", "361.75"]) == 36175000
      and _amt("x_b", "(b) The Company has made a preferential allotment of 1,50,000 equity shares during the year.",
               ["1,50,000"]) is None
      and _amt("iii_d", "(d) The amount of Rs. 12,50,000 is overdue for more than 90 days.", ["12,50,000", "90"]) == 1250000
      and _amt("vii_a", "(a) Undisputed dues of Rs. 4,520 hundred were outstanding for more than six months.",
               ["4,520"]) == 452000
      and _amt("vii_a", "(a) Undisputed dues outstanding for more than six months:\nName of statute (Rs. in '000)\n"
               "Income Tax 4,520\nGST 1,200", ["4,520", "1,200"]) == 5720000
      # replay guard (Starlite 2021-22 (iii)(f)): a word on the NEXT line ("Percentage of loan") is not the figure's unit
      and _amt("iii_f", "(f) the Company has given loans repayable on demand to the following Company:\n\nParticulars"
               "\n\nAmount\n\nStarlite Global Enterprises FZE, UAE\n\n1,23,21,685\n\nPercentage of loan to total "
               "loans\n\n100%", ["1,23,21,685"]) == 12321685)

_LA = pr.R_ARREARS_LISTED
check("V6-4 (P17, Toby 14-Oct: arrears > 6 months = Adverse; D14: no amount needed) undisputed arrears the auditor LISTS "
      "fire the Adverse trigger - 'no undisputed amounts ... except following ... were in arrears ... more than six "
      "months' (Ichalkaranji), '... outstanding ... more than six months ... are as follows' (Aditya), 'there were "
      "following amounts ... in arrears' (Committed Ispat); never a plain denial, 'except as given below: Nil', a "
      "disputed-dues table, the Order's own question wording, or a denial cut by a stray full stop (Viraj Canes 2023-24)",
      _LA.hit("According to the information and explanations given to us, no undisputed amounts payable except following "
              "in respect of the above were in arrears as at March 31, 2022 for a period of more than six months from the "
              "date on when they become payable.")
      and _LA.hit("undisputed dues in respect of taxes deducted at source, which were outstanding, at the year end, for a "
                  "period of more than six months from the date they became payable, are as follows: Name of the statute")
      and _LA.hit("b) there were following amounts payable in respect thereof were in arrears as at the last day of the "
                  "financial year for a period of more than six months from the date they become payable.")
      and not _LA.hit("no undisputed amounts payable in respect of these statutory dues were outstanding, at the year end, "
                      "for a period of more than six months from the date they became payable.")
      and not _LA.hit("There are no undisputed amounts payable which were in arrears for more than six months except as "
                      "given below: Nil")
      and not _LA.hit("There were no undisputed amounts outstanding for more than six months except as stated below.\n"
                      "Name of statute Forum where dispute is pending Appellate")
      and not _LA.hit("The extent of the arrears of statutory dues outstanding as at March 31, 2024, for a period of more "
                      "than six months from the date they became payable.")
      and not _LA.hit("There were no undisputed amounts payable in respect of Provident Fund, Employees'\n\nState "
                      "Insurance, Income Tax, Sales Tax, Service Tax, Value Added Tax, Goods and\n\nService Tax. Customs "
                      "Duty, Excise Duty, Cess and other material statutory dues in\n\narrears as at March 31, 2024 for "
                      "a period of more than six months from the date they\n\nbecame payable.")
      and _LA.staged_patch == "P17_ADV" and _SHIPPED.get("P17_ADV") is True)


# ---------------------------------------------------------------------------
# PATCH 14-Oct-2026 (v7): convention rules on each unit's own text (post_rules.apply_v7_rules), two-call only
# ---------------------------------------------------------------------------
import copy as _cp7


def _v7(main, subs=None, main_src="", staged=True, **extra):
    """one clause record: subs = {unit: (classification, source_text)} or None for a main with no sub-clauses"""
    def unit(uid, cls, src, level):
        d = {"classification": cls, "normalized_fields": {"source_text": src}, "evidence_text": src,
             "score_ready_fields": {}, "explanation_text": "model"}
        if level == "sub":
            d["sub_clause_id"] = uid
        if staged:
            d["_staged"] = True
        return d
    if subs is None:
        r = unit(main, extra.pop("cls"), main_src, "main")
        r["main_clause_id"] = main
        r["sub_clauses"] = []
    else:
        r = unit(main, "clean", main_src, "main")
        r["main_clause_id"] = main
        r["sub_clauses"] = [unit(u, c, t, "sub") for u, (c, t) in subs.items()]
    for k, v in extra.items():
        (r["sub_clauses"][0] if r["sub_clauses"] else r)[k] = v
    return [r]


def _lab7(recs, uid):
    for r in recs:
        for x in (r.get("sub_clauses") or []) + [r]:
            if (x.get("sub_clause_id") or x.get("main_clause_id")) == uid:
                return x["classification"] + ("+scope" if x.get("scope_limited") else "")


_viii = "The Company has not surrendered or disclosed any transaction, previously unrecorded in the books of account, in the tax assessments under the Income Tax Act, 1961 as income during the year. Accordingly, the requirement to report on clause 3(viii) of the Order is not applicable to the Company."
_r = _v7("viii", None, _viii, cls="clean"); pr.apply_v7_rules(_r, 2022)
_rs = _v7("viii", None, _viii, staged=False, cls="clean"); _rs0 = _cp7.deepcopy(_rs); pr.apply_v7_rules(_rs, 2022)
check("V7-0 (rollback + single flow) every v7 group has its own switch, shipped ON; a record of the single flow "
      "(no two-call marker) is never touched; an Adverse label and a label set by another code rule are never changed",
      all(_SHIPPED.get(g) is True for g in ("V7_G1", "V7_G2", "V7_G3", "V7_G4", "V7_G7", "V7_G8"))
      and _lab7(_r, "viii") == "not_applicable" and _rs == _rs0
      and _lab7(pr.apply_v7_rules(_x := _v7("viii", None, _viii, cls="adverse"), 2022) or _x, "viii") == "adverse"
      and _lab7(pr.apply_v7_rules(_y := _v7("viii", None, _viii, cls="clean", blanket_na_rule={"x": 1}), 2022) or _y,
                "viii") == "clean")

_ixc = "(c) According to the information and explanations given to us by the management, the Company has not applied term loans for the purpose other than for which it is obtained. Accordingly, clause 3(ix)(c) of the Order is not applicable."
_iiib_sunstar = "(b) No investments made, guarantees provided, security given and the terms and conditions of the grant of all loans and advances in the nature of loans and guarantees provided are not prejudicial to the company's interest."
_r1 = _v7("ix", {"ix_c": ("not_applicable", _ixc)}); pr.apply_v7_rules(_r1, 2022)
_r1b = _v7("iii", {"iii_b": ("not_applicable", _iiib_sunstar)}, "(a) The Company has not provided any loans or advances in the nature of loans, guarantee or security to any entity during the year.")
pr.apply_v7_rules(_r1b, 2022)
check("V7-1 (group 1, 02 register 'Favourable vs NA') a favourable finding on the unit's own question with an "
      "'accordingly not applicable' tail is Favourable (Starlite (ix)(c)); a boilerplate 'not prejudicial' where the "
      "clause says nothing was given stays NA (Sunstar (iii)(b))",
      _lab7(_r1, "ix_c") == "clean" and _r1[0]["sub_clauses"][0]["v7_rule"]["group"] == "G1"
      and _lab7(_r1b, "iii_b") == "not_applicable")

_tbl = "B Details of loans and guarantees or security given to parties other than subsidiaries, joint ventures and associates is as follows: Rs. In Lakhs Particulars Loans Guarantees Aggregate amount granted during the year 4,863.70 3,000.00 Balance outstanding as at balance sheet 4,863.70 9,000.00"
_western = "iii) On verification of relevant records, it is found that, the company has granted following loans, unsecured, to the companies covered in the register maintained under section 189 of the Companies Act 2013. S NO Particulars Closing Balance as on 31.03.2022 (in Rs) Closing Balance as on 31.03.2021 (in Rs) Badri Kedar Infra Pvt Ltd 122475616.00 110099137.00"
_r2 = _v7("iii", {"iii_a_B": ("qualified", _tbl)}); pr.apply_v7_rules(_r2, 2022)
_r2b = _v7("iii", {"iii_a_B": ("qualified", _western)}); pr.apply_v7_rules(_r2b, 2022)
_r2c = _v7("iii", {"iii_a_B": ("qualified", _tbl + " However, the interest is overdue.")}); pr.apply_v7_rules(_r2c, 2022)
check("V7-2 (group 2) (iii)(a) amounts given and outstanding, no exception -> Favourable (Shri Dutt 2023-24); two "
      "year-end balances with nothing given in the year stay Qualified (CA I14, Western 2021-22); an exception keeps it",
      _lab7(_r2, "iii_a_B") == "clean" and _lab7(_r2b, "iii_a_B") == "qualified" and _lab7(_r2c, "iii_a_B") == "qualified")

_r3 = _v7("iii", {"iii_c": ("clean", "As per the information and explanation given to us, No amount is overdue for more than ninety days.")})
pr.apply_v7_rules(_r3, 2022)
_r3b = _v7("iii", {"iii_c": ("not_applicable", "As no loans are granted, the points relating to receipt of interest & Principle overdue amount are not applicable during the year.")})
pr.apply_v7_rules(_r3b, 2022)
_r3c = _v7("xv", None, "BOX: The company has not entered into any non cash transactions with the directors or persons connected with them. The company is not required to be registered under section 45 IA of the Reserve Bank of India Act 1934", cls="clean")
pr.apply_v7_rules(_r3c, 2022)
_xivp = "XIVBased upon the audit procedure performed and the information given by the management, the company has not been made any preferential allotment or private placement of shares fully or partly convertible debentures during the under review accordingly the provision of clause 3 (xiv) of the order are not applicable to the company and has not commented upon."
_r3d = _v7("xiv", {"xiv_a": ("not_applicable", _xivp)}, _xivp); pr.apply_v7_rules(_r3d, 2022)
check("V7-3 (groups 3 / 6) text that answers another unit's question is withdrawn to Not Reported: (iii)(d)'s "
      "'no amount overdue > 90 days' on (iii)(c) (K.R. Foods 2022-23), the (x)(b) sentence printed as (xiv) (Refit "
      "2022-23); a whole-clause nil ('as no loans are granted ...') stays NA; 'non cash' (with a space) is (xv)'s own word",
      _lab7(_r3, "iii_c") == "insufficient_evidence" and _lab7(_r3b, "iii_c") == "not_applicable"
      and _lab7(_r3c, "xv") == "clean" and _lab7(_r3d, "xiv_a") == "insufficient_evidence")

_r4 = _v7("iii", {"iii_f": ("clean", "According to the information and explanations given to us, the Company has given loans repayable on demand to the following Company: Particulars Amount Starlite Global Enterprises FZE, UAE 149.13 Lakhs Percentage of loan to total loans 100%")})
pr.apply_v7_rules(_r4, 2022)
_r4b = _v7("xiv", {"xiv_b": ("not_applicable", "As per the information provided, No Internal Audit is being conducted during the year under Audit.")})
pr.apply_v7_rules(_r4b, 2022)
_r4c = _v7("x", {"x_b": ("clean", "the Company has made preferential allotment of shares by way of right issue of shares during the year and the requirements of section 42 and section 62 of the Companies Act, 2013 have been complied with.")})
pr.apply_v7_rules(_r4c, 2022)
_r4d = _v7("x", {"x_b": ("clean", "The company has allotted 57000 equity shares of Rs. 100/- each pursuant to conversion of Unsecured Loan to Equity Share Capital.")})
pr.apply_v7_rules(_r4d, 2022)
check("V7-4 (group 4) stated exceptions -> Qualified: demand loans GIVEN under (iii)(f) (Starlite 2023-24), internal audit "
      "required but not carried out (ICAI, K.R. Foods), an allotment with no s.42/62 statement (doc 13); an allotment "
      "WITH a compliance statement stays Favourable (Apollo)",
      _lab7(_r4, "iii_f") == "qualified" and _lab7(_r4b, "xiv_b") == "qualified"
      and _lab7(_r4c, "x_b") == "clean" and _lab7(_r4d, "x_b") == "qualified")

_xx = "(xx) There are no unspent amounts that are required to be transferred to a fund specified in Schedule VII of the Companies Act, 2013 within a period of six months of the expiry of the financial year. Accordingly, clauses 3(ix)(e) and 3(ix)(f) of the Order are not applicable."
_ixm = "(ix) a. The Company has not defaulted in the repayment of loans. e. The company does not have any subsidiary, associates & joint venture. Accordingly reporting under paragraph 3(ix)(e) and 3 (ix) (f) is not applicable"
_r7 = _v7("ix", {"ix_f": ("insufficient_evidence", "-")}, _ixm); pr.apply_v7_rules(_r7, 2022)
_r7b = _v7("ix", {"ix_b": ("insufficient_evidence", "-")}, "the Company has not made any default in repayment of loan or other borrowings. Accordingly, the provisions of clause 3(ix) of the Order are not applicable to the Company.")
pr.apply_v7_rules(_r7b, 2022)
_r7c = _v7("iii", {"iii_f": ("clean", "f) The Company has not granted loan or advance in the nature of loan during the year, which is repayable on demand or without specifying any terms or period of repayment.")})
pr.apply_v7_rules(_r7c, 2022)
check("V7-5 (group 7) conventions -> NA: (viii) 'accordingly not applicable' (V7-0), (iii)(f) 'has not granted ... "
      "repayable on demand' (Apollo), a sub-clause the passage NAMES as not applicable ((ix)(f), Universal 2024-25); a "
      "whole-clause 'clause 3(ix) not applicable' after one finding leaves the other points Not Reported (K.R. Foods)",
      _lab7(_r7, "ix_f") == "not_applicable" and _lab7(_r7b, "ix_b") == "insufficient_evidence"
      and _lab7(_r7c, "iii_f") == "not_applicable")

_r8 = _v7("vi", None, "the provisions of clause 3(vi) of the Order are applicable to the Company. The cost audit report was not made available at the time of audit.", cls="qualified")
pr.apply_v7_rules(_r8, 2022)
_r8b = _v7("ix", {"ix_c": ("clean", "The term loans availed by the company are generally applied for the purpose for which they were availed. With respect to working capital loans, we are unable to monitor the use of the said loans considering the huge nature.")})
pr.apply_v7_rules(_r8b, 2022)
check("V7-6 (group 8) could-not-verify wordings the scope list missed -> Auditor Unable to Verify ('cost audit report "
      "was not made available at the time of audit', K.R. Foods (vi)); the existing list is NOT widened to the whole "
      "text ('unable to monitor' working-capital loans on a Favourable (ix)(c) stays Favourable)",
      _lab7(_r8, "vi") == "qualified+scope" and _lab7(_r8b, "ix_c") == "clean"
      and "auditor_scope_limitation" in (_r8[0].get("review_reasons") or []))


_res7 = run_filing("jsons_2020", Scripted(), "staged")[0]
_one7 = run_filing("jsons_2020", llm.MockClient(), "single")[0]
_d7 = {u: (a["classification"], flat(_res7)[u]["classification"]) for u, a in flat(_one7).items()
       if a["classification"] != flat(_res7)[u]["classification"]}
_t7 = run_filing("table_2020", Scripted(), "staged")[0]
_t1 = run_filing("table_2020", llm.MockClient(), "single")[0]
check("V7-7 (mock model that maps by PRINTED position, Jsons Foundry 2016-layout text) v7 withdraws exactly the units "
      "whose text answers another clause ((v) holds the s.185/186 sentence, (viii) the loans list, (ix)(a)/(d) the cost "
      "records text, (xiii) the fraud sentence, (xvii) the allotment, (xviii) internal audit, (xix) non-cash) and nothing "
      "else; an answer that repeats the question's words ('Transactions with related parties comply with sections 177 "
      "and 188', table_2020 (xiii)) is kept",
      set(_d7) == {"v", "viii", "ix", "ix_a", "ix_d", "xiii", "xvii", "xviii", "xix"}
      and all(b == "Not Reported by Auditor" for _a, b in _d7.values())
      and flat(_t7)["xiii"]["classification"] == flat(_t1)["xiii"]["classification"], (_d7, flat(_t7)["xiii"]["classification"]))

# ---------------------------------------------------------------------------
# PATCH 14-Oct-2026 (v8): Toby's decisions D-1, D-2, D-3, D13 and the cut-off rule (post_rules.apply_v8_rules,
# apply_adverse_triggers V8_CUT). Real wording from the filings named.
# ---------------------------------------------------------------------------
_d1 = ("(vii)(a) Undisputed statutory dues including goods and services tax, provident fund ... have generally been regularly "
       "deposited with the appropriate authorities though there has been a slight delay in a few cases. According to the "
       "information and explanations given to us, no undisputed amounts payable in respect of these statutory dues were "
       "outstanding, at the year end, for a period of more than six months from the date they became payable.")
_hedge = ("(a) According to the books and records produced and examined by us, the Company is generally regular in depositing "
          "undisputed Statutory dues including Provident Fund, Employees' State Insurance, Income Tax, Goods and Services Tax "
          "(GST), Cess and other material statutory dues as applicable with the appropriate authorities and no undisputed "
          "amount payable in respect of aforesaid statutory dues were outstanding as at March 31, 2022 for a period of more "
          "than six months from the date they become payable.")
_v8r = lambda recs: (pr.apply_v8_rules(recs, 2022), recs)[1]
_s8 = _v7("vii", {"vii_a": ("clean", _d1)}, staged=False); _s80 = _cp7.deepcopy(_s8); pr.apply_v8_rules(_s8, 2022)
check("V8-0 (rollback + single flow) every v8 rule has its own switch, shipped ON; a single-flow record is never touched; "
      "an Adverse and an Auditor Unable to Verify are never changed",
      all(_SHIPPED.get(g) is True for g in ("V8_D1", "V8_D1B", "V8_D2", "V8_D3", "V8_D13", "V8_CUT")) and _s8 == _s80
      and _lab7(_v8r(_v7("vii", {"vii_a": ("adverse", _d1)})), "vii_a") == "adverse"
      and _lab7(_v8r(_v7("vii", {"vii_a": ("qualified", _hedge)}, scope_limited=True)), "vii_a") == "qualified+scope")
check("V8-1 (D-1) (vii)(a) delays stated, no arrears -> Qualified (Aditya 2022-23); 'without any delay' and 'generally "
      "regular ... no arrears' (U15421PN2006, 39 confirmed Favourable rows) stay Favourable",
      _lab7(_v8r(_v7("vii", {"vii_a": ("clean", _d1)})), "vii_a") == "qualified"
      and _lab7(_v8r(_v7("vii", {"vii_a": ("clean", "The Company has been regular in depositing undisputed statutory dues "
                                            "without any delay with the appropriate authorities.")})), "vii_a") == "clean"
      and _lab7(_v8r(_v7("vii", {"vii_a": ("clean", _hedge)})), "vii_a") == "clean")
check("V8-2 (D-1, reviewed-truth convention) 'generally regular' + no arrears, NO delay, model said Qualified -> "
      "Favourable; the same with a delay stated stays Qualified",
      _lab7(_v8r(_v7("vii", {"vii_a": ("qualified", _hedge)})), "vii_a") == "clean"
      and _lab7(_v8r(_v7("vii", {"vii_a": ("qualified", _d1)})), "vii_a") == "qualified")
_xxm = ("(xx) There are no unspent amounts that are required to be transferred to a fund specified in Schedule VII of the "
        "Companies Act, 2013 within a period of six months of the expiry of the financial year, in compliance with second "
        "proviso to sub-section 5 of section 135 of the Act. Accordingly, clauses 3(xx)(a) and 3(xx)(b) of the Order are "
        "not applicable.")
_x2 = _v8r(_v7("xx", {"xx_a": ("clean", "There are no unspent amounts ..."), "xx_b": ("insufficient_evidence", "-")}, _xxm))
_x2b = _v7("xx", {"xx_b": ("insufficient_evidence", "-")}, _xxm); _x2b[0]["sub_clauses"][0]["csr_spent_rule"] = {"x": 1}
_v8r(_x2b)
_x2c = _v8r(_v7("xx", {"xx_b": ("clean", "there is no unspent amount under sub-section (5) of Section 135 pursuant to any project.")}, _xxm))
check("V8-3 (D-2) (xx)(b) with no text of its own, the CSR passage naming 3(xx)(a) and 3(xx)(b) not applicable -> Not "
      "Applicable, also over rule F10a's Not Reported (U15421PN2006 2021-22); (xx)(b) with its own answer (Starlite) is "
      "untouched",
      _lab7(_x2, "xx_b") == "not_applicable" and _lab7(_x2b, "xx_b") == "not_applicable" and _lab7(_x2c, "xx_b") == "clean")
_ic = ("The title deeds of all the immovable properties (other than properties where the company is the lessee and the lease "
       "agreements are duly executed in favour of the lessee) are disclosed in financial statement.")
_krf = ("As per the information and explanations given to us, the Company has granted loans, secured or unsecured to companies, "
        "firms or other parties covered in the register maintained under section 189 of the Act. Accordingly, the provisions "
        "of clause3(iii)(a) to (f) of the Order are applicable to the company and hence commented upon: The Loans and "
        "Advances are not prejudicial to the Interest of the Company. The Following Loans & Advances are repayment on Demand : "
        "The Loans & Advances are repayment on Demand, therefore noamount overdue at year end.")
_ia = ("xiv) According to section 138(1) of the Companies Act, 2013 company is fall under the category for appointment of an "
       "internal auditor. The company has not appointed internal auditor.")
check("V8-4 (D-3) incomplete answers on the point's own subject -> Qualified: (i)(c) deeds 'disclosed in financial "
      "statement' (Ichalkaranji); (iii)(a)(A)/(B) and (iii)(c) loans granted, no amounts / demand loans (K.R. Foods "
      "2021-22); (xiv)(b) internal auditor required, not appointed (Ichalkaranji 2022-23). Kept: deeds 'held in the name "
      "of the Company'; (iii)(a) with amounts given",
      _lab7(_v8r(_v7("i", {"i_c": ("clean", _ic)})), "i_c") == "qualified"
      and _lab7(_v8r(_v7("i", {"i_c": ("clean", "The title deeds of all the immovable properties disclosed in the "
                                       "financial statements are held in the name of the Company.")})), "i_c") == "clean"
      and _lab7(_v8r(_v7("iii", {"iii_a_A": ("insufficient_evidence", "-")}, _krf)), "iii_a_A") == "qualified"
      and _lab7(_v8r(_v7("iii", {"iii_c": ("insufficient_evidence", "-")}, _krf)), "iii_c") == "qualified"
      and _lab7(_v8r(_v7("iii", {"iii_a_A": ("insufficient_evidence", "-")}, _krf + " The aggregate amount is Rs. 12,50,000.")),
                "iii_a_A") == "insufficient_evidence"
      and _lab7(_v8r(_v7("xiv", {"xiv_b": ("insufficient_evidence", "-")}, _ia)), "xiv_b") == "qualified")
_dep = ("v) The company has not accepted any deposits from the public except four Credit cooperative societies of Rs. 7383 "
        "thousand and hence the directives issued by the Reserve Bank of India and the provisions of Sections 73 to 76 or any "
        "other relevant provisions of the Act and the Companies (Acceptance of Deposit) Rules, 2015 with regard to the "
        "deposits accepted from the public are applicable.")
check("V8-5 (D13) (v) deposits from credit co-operative societies, provisions 'applicable', no compliance stated -> "
      "Qualified (Ichalkaranji 2022-23); a compliance statement keeps it Favourable",
      _lab7(_v8r(_v7("v", None, _dep, cls="not_applicable")), "v") == "qualified"
      and _lab7(_v8r(_v7("v", None, _dep + " The Company has complied with the said provisions.", cls="clean")), "v") == "clean")
_ixm8 = ("(a) In our opinion and according to the information and explanations given to us, and based on scrutiny of accounts "
         "the Company has not defaulted for repayment of term loan taken from Bank: (b) The company is not declared as willful "
         "defaulter by any bank or financial institution or other lender. However, the credit facilities sanctioned by banks "
         "Following Banks having outstanding of Rs. 906087 thousand including interest is became NPA.")
_cut = _v7("ix", {"ix_a": ("clean", "(a) In our opinion and according to the information and explanations given to us, and "
                                    "based on scrutiny of accounts the Company has not defaulted for repayment of term loan "
                                    "taken from Bank:"),
                  "ix_b": ("clean", "(b) The company is not declared as willful defaulter by any bank or financial "
                                    "institution or other lender.")}, _ixm8)
pr.apply_adverse_triggers(_cut, 2022, set())
_cut2 = _v7("ix", {"ix_a": ("clean", "(a) The Company has not defaulted in repayment of term loan taken from Bank."),
                   "ix_b": ("clean", "(b) not declared wilful defaulter.")}, _ixm8)
pr.apply_adverse_triggers(_cut2, 2022, set())
check("V8-6 (V8_CUT) a sub-point whose copied answer stops at ':' is read with its clause passage, minus its siblings' own "
      "text: Ichalkaranji 2022-23 (ix)(a) '... taken from Bank:' + 'became NPA' -> Adverse; a complete answer is not "
      "re-read (stays Favourable); (ix)(b) untouched",
      _lab7(_cut, "ix_a") == "adverse" and _lab7(_cut, "ix_b") == "clean" and _lab7(_cut2, "ix_a") == "clean",
      (_lab7(_cut, "ix_a"), _lab7(_cut2, "ix_a")))

urllib.request.urlopen = _real_urlopen
n_ok = sum(ok for _n, ok in RESULTS)
print(f"\n{n_ok}/{len(RESULTS)} Phase 2 checks passed")
sys.exit(0 if n_ok == len(RESULTS) else 1)
