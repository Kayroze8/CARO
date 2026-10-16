# 25 — v8: your decisions D-1, D-2, D-3, D13 and the cut-off fix (14 Oct 2026, night)

Prepared for Toby (Metis Intellisystems). Builds on v7 (doc 23).

---

## 0. Bottom line

| Reply set (same saved model replies, no new model run) | v7 (reviewed / CA) | **v8 (reviewed / CA)** | Fixed | Broken |
|---|---|---|---|---|
| 13-Oct full run, 138 filings | 98.70% / 98.34% | **99.01% / 98.65%** | 19 | **0** |
| Your v6 live trial, 29 filings (6 CINs) | 98.13% / 97.11% | **99.06% / 98.05%** | 12 | **0** |
| 11-Oct full run, 138 filings | 96.87% / 96.75% | 97.34% / 97.22% | 28 | **0** |
| 14-Oct full run, 138 filings | 97.48% / 97.79% | 97.73% / 98.04% | 15 | **0** |
| v5 trial, 29 filings | 97.82% / 96.80% | 98.44% / 97.43% | 8 | **0** |

All figures are scored against `truth_v8.csv`: your truth with the decisions of 14 Oct, 22:28 applied (§3).

**Accuracy was not lost anywhere.** I checked this two ways:
1. Against the new truth: 0 units went from right to wrong, on both truths, in all five reply sets.
2. Against the old (v7) truth: the only units that "changed" are the 20 rows your decisions changed. 0 others.

---

## 1. What v8 does

Every rule runs in code **after** the model has answered (two-call flow only), exactly like v7. Each rule has its own switch.

| Switch | Your decision | Rule | Real example |
|---|---|---|---|
| `V8_D1` | **D-1: Qualified** | (vii)(a): the auditor states **delays** or an exception in depositing undisputed dues, with no arrears over 6 months → Qualified | Aditya 2022-23 "though there has been a slight delay in a few cases"; U72900 2020-21 to 2022-23 "except that there has been delays in few instances"; U74900 2024-25 "regularly deposited except some variation" |
| `V8_D1B` | D-1 scope (see §2) | (vii)(a) "generally regular" + no arrears + **no delay stated**, where the model said Qualified → Favourable. This is the convention of 39 confirmed rows in your truth | U15421PN2006 2021-22: "generally regular … no undisputed amount payable … outstanding … more than six months" |
| `V8_D2` | **D-2: NA** | (xx)(b) with no text of its own, where the CSR passage names "3(xx)(a) and 3(xx)(b)" (or "clause (xx)") as not applicable → Not Applicable. This replaces rule F10a's Not Reported for this one case only | U15421PN2006 ×4, U15510 ×3, U28113 ×1 |
| `V8_D3` | **D-3: Qualified** | An answer on the point's own subject that leaves out the fact the question asks for → Qualified. Four cases, listed below this table | Ichalkaranji (i)(c); K.R. Foods (iii) 2021-22 and 2024-25; Ichalkaranji (xiv)(b) |
| `V8_D13` | **D13: Qualified** | (v) deposits accepted (credit co-operative societies), the auditor says ss.73–76 "are applicable", and no compliance is stated → Qualified | Ichalkaranji 2022-23 and 2023-24 |
| `V8_CUT` | **v8 cut-off: yes** | A sub-point whose copied text stops at ":" is also read with its clause passage, minus the siblings' own text, for the Adverse checks. A narrow NPA check is added for this case only | Ichalkaranji 2022-23 (ix)(a) "… taken from Bank:" → the passage's "… became NPA" → **Adverse** |

The four D-3 cases:
- **(i)(c):** title deeds "disclosed in financial statement", with no word on whose name they are in.
- **(iii)(a)(A)/(B):** loans stated as granted, but no amounts given. This includes a heading such as "The Following Loan & Advances are Repayment on Demand:" with nothing after it.
- **(iii)(c):** demand loans with no repayment schedule stated.
- **(xiv)(a)/(b):** internal auditor required but not appointed, stated only in the clause passage.

**Safety, as in v7:**
- An Adverse or an Auditor Unable to Verify is never changed.
- A label set by another agreed rule is never changed. The only exception is D-2 replacing F10a's Not Reported on (xx)(b).
- One rule per unit.
- Every change is recorded in the audit file under `v8_rule`, with the rule, from and to labels, the auditor's words and why. The audit actions are `v8_decision_rule_<D1|D1B|D2|D3|D13>` and `v8_cut_answer_read_with_clause_passage`.
- No new Remarks, no new JSON keys (schema 4.1), and amounts are untouched.

---

## 2. Three things you should know (please confirm or overrule)

1. **D-1 was kept narrow on purpose.** Your truth has **39 confirmed Favourable** (vii)(a) rows that say "generally regular … no arrears over six months" with **no delay**. Turning every "generally regular" into Qualified would break all 39. So D-1 fires only when the auditor **states a delay or an exception**.
   - Of the 7 rows tagged `pending_decision:P17`, 5 state a delay and are now Qualified.
   - The other 2 say only "generally regular", like the 39, so I kept them **Favourable**:
     - Committed Ispat 2021-22;
     - U74999PN2015 2024-25.

   Tell me if you want those two Qualified as well.
2. **Aditya Auto 2020-21 (vii)(a) stays Adverse; I corrected the truth row.** The same report's para (b) lists **TDS of Rs 68,21,580 outstanding for more than six months** (April–September 2020, paid 26-Oct-2021). Under your P17 rule this is Adverse, the same as Aditya 2021-22 and 2024-25. The truth row said Qualified; it now says Adverse.
3. **Ichalkaranji 2024-25 (xiv)(b): truth corrected to Qualified.** The auditor writes "required to appoint an internal auditor … has not appointed". That is the same substance as 2023-24, which your truth already marks Qualified (ICAI, doc 13 #6). 2024-25 said Not Reported.

These two corrections apply your own rules consistently. Both are tagged `decided_14Oct_v8` in `truth_v8.csv`, so you can see and reverse them.

---

## 3. Truth file changes (`truth_v8.csv`, 20 rows, all tagged `decided_14Oct_v8`)

| Decision | Rows |
|---|---|
| D-1 → Qualified | Aditya 2022-23; U72900DL2007PTC162708 2020-21, 2021-22, 2022-23; U74900DL2011PTC227729 2024-25 (all (vii)(a)) |
| D-1 scope → Favourable kept | Committed Ispat 2021-22; U74999PN2015PTC157583 2024-25 |
| P17 consistency → Adverse | Aditya 2020-21 (vii)(a) |
| D-3 → Qualified | K.R. Foods 2021-22 (iii)(a)(A), (iii)(a)(B), (iii)(c); 2024-25 (iii)(a)(A), (iii)(a)(B). Viraj Canes 2020-21 (ii), (ix); 2021-22 (ii)(a); 2022-23 (ii)(a). U29299 2020-21 (iii)(a) |
| ICAI → Qualified | Ichalkaranji 2022-23 and 2024-25 (xiv)(b) |
| D-2 | No change needed: your truth already says NA |
| D13 | No change needed: Ichalkaranji (v) already Qualified |

---

## 4. How v8 was proven

1. **Tests:** 241/241. These are the 234 existing tests plus V8-0 to V8-6, which use real filing wording, including counter-cases that must **not** change:
   - "without any delay";
   - "generally regular … no arrears";
   - deeds "held in the name of the Company";
   - (iii)(a) with amounts;
   - (xx)(b) with its own answer (Starlite);
   - (v) with a compliance statement;
   - a complete (ix)(a) answer.
2. **Replay on 5 saved reply sets:** the table in §0, 0 breaks on both truths. Each rule was also run **on its own**:

   | Rule alone | 11-Oct | 13-Oct | 14-Oct | v5 trial | v6 trial | Breaks |
   |---|---|---|---|---|---|---|
   | D1 | 10 | 0 | 0 | 0 | 0 | 0 |
   | D1B | 1 | 0 | 0 | 0 | 0 | 0 |
   | D2 | 8 | 8 | 8 | 0 | 0 | 0 |
   | D3 | 7 | 9 | 4 | 6 | 9 | 0 |
   | D13 | 2 | 2 | 2 | 2 | 2 | 0 |
   | CUT | 0 | 0 | 1 | 0 | 1 | 0 |

   D1 shows fixes only in the 11-Oct replies because in the other runs the model already said Qualified. The rule is a safety net for the runs where it doesn't.
3. **Model requests:** byte-identical to the 13-Oct code (138/138 and 29/29).
4. **Rollback:** with all six v8 switches off, the output is byte-identical to v7 on all 5 sets.
5. **Schema and shape:**
   - your six corrected files pass strict validation: 1,168 clause objects, 0 errors, 6/6 whole files;
   - they have the same key paths as the files you delivered;
   - there are 0 amount changes.

During the build, D-3 first missed K.R. Foods 2024-25 (iii)(a), for two reasons:
- the amount check mistook the year in "Companies Act, 2013" for an amount;
- the point held a dangling heading rather than no text.

Both were fixed, and every proof above was re-run on the final code.

---

## 5. Your six files (v8 applied to your own live replies)

| Company | CIN | v6 delivered | v7 | **v8** | Still wrong (rev / CA) |
|---|---|---|---|---|---|
| Aditya Auto | U35204KA1996PTC020766 | 96.31% / 96.31% | 100.00% / 100.00% | **100.00% / 100.00%** | 0 / 0 |
| Ichalkaranji | U17290PN2012PLC145174 | 94.90% / 94.39% | 96.43% / 95.92% | **100.00% / 99.49%** | 0 / 1 |
| K.R. Foods | U15319DL1987PLC027698 | 94.47% / 94.01% | 96.77% / 96.31% | **99.08% / 98.62%** | 2 / 3 |
| Western Precicast | U27101PN1994PTC077414 | 98.60% / 97.65% | 99.07% / 98.12% | **99.07% / 98.12%** | 2 / 4 |
| Siddhivinayak | U45200PN2011PLC138765 | 99.55% / 95.96% | 99.55% / 95.96% | **99.55% / 95.96%** | 1 / 9 |
| Viraj Canes | U15422PN2011PLC138871 | 96.77% / 96.30% | 96.77% / 96.30% | **96.77% / 96.30%** | 7 / 8 |
| **All 29 filings** | | 96.80% / 95.79% | 98.13% / 97.11% | **99.06% / 98.05%** | 12 / 25 |

All figures are scored on `truth_v8.csv`.

**The 14 label changes v7 → v8 in your files** (each read as a CA; all match your truth):
- K.R. Foods 2021-22 (iii)(a)(A), (iii)(a)(B) and (iii)(c) → Qualified (D-3).
- K.R. Foods 2024-25 (iii)(a)(A) and (iii)(a)(B) → Qualified (D-3, dangling heading).
- Ichalkaranji:
  - 2021-22 to 2023-24 (i)(c) → Qualified (D-3, deeds "disclosed in financial statement");
  - 2022-23 and 2023-24 (v) → Qualified (D13);
  - 2022-23 (ix)(a) → **Adverse** (cut-off, NPA);
  - 2024-25 (xiv)(b) → Qualified (ICAI).
- Plus the main clauses (i) and (ix) following their sub-clauses.

**Still wrong, and why:**
- **Viraj Canes (ii)/(ii)(a) 2020-21 to 2022-23, and (ix) 2020-21 (4 units, D-3).** The inventory sentence is in the filing, but the model's Call 1 did not place it on any point in those years. No after-the-model rule can see it. This needs a Call 1 fix (a later, separate A/B test).
- **Viraj Canes, other units:**
  - (iv) 2024-25: identical wording carries opposite truths in different years;
  - (x)(a) 2021-22: "raised moneys as per the Act";
  - (iii)(a)(B) 2022-23: an empty CARO 2016 sentence.
- **K.R. Foods 2022-23 (i)(a)(B) and (iii)(d):** the auditor's letters are printed shifted.
- **Western Precicast:**
  - 2022-23 (iii)(a)(B): a table read as Unable to Verify;
  - 2023-24 (v): loans from directors listed under (v); your truth is mixed on this pattern across companies.
- **Siddhivinayak:**
  - (iii)(b)–(e) 2022-23 and 2023-24 are CA-truth only: "no loans", yet loans are listed;
  - 2024-25 (iii)(a)(B).

---

## 6. Running new companies and measuring overall accuracy

1. **Install:** copy `caro/*.py` and `tests/run_phase2_cases.py` over your repo, then run `python tests/run_phase2_cases.py`. Expect **241/241**.
2. **Run** the new CINs with `CARO_LLM_FLOW=staged` (production; falls back to the single call). For a test run use `staged_nofallback`.
3. **Make the review sheet:** `python tools/review_sheet.py make logs/caro_results review_new.xlsx`.
   - It lists one row per point: the pipeline label, the auditor's text, the explanation, and the review / red flags.
   - It leaves a yellow **CA label** column with a drop-down (FAV, NA, QUAL, ADV, UTV, NR, NAS), and a "Label guide" sheet.
   - Main clauses with sub-clauses are left out (they are roll-ups).
4. **A CA fills the CA label column.** Blank rows are skipped. Doc 24 is the rulebook.
5. **Score:** `python tools/review_sheet.py score review_new.xlsx`.
   - It prints overall accuracy, accuracy by clause, by company and by label, and the most common disagreements.
   - It writes a "Disagreements" sheet into the same workbook.
   - I tested it on your six files filled with your truth: it gives 99.06%, the same as my scorer.
6. **Send me** the filled sheet plus the usual folders (caro_results, caro_audit, extracted_llm_out, extracted_text, api log). I'll read every disagreement and turn the patterns into the next rules, proved the same way.

**What to expect on new companies.** These rules were built and checked on the same ~35 companies, so new companies are the honest test. My expectation is **lower than 99%**, probably around 96–98% on first contact, because new companies bring wording the rules have never seen. Most of the gap will show up as model mapping differences (like the Viraj inventory case) rather than classification errors. That is exactly what the review sheet will measure.

---

## 7. Rollback

- Any single v8 rule: set `V8_D1`, `V8_D1B`, `V8_D2`, `V8_D3`, `V8_D13` or `V8_CUT` to `False` in `caro/staged_llm.py` PATCH_SWITCHES.
- All of v8: all six to `False`. The output is identical to v7.
- v7 as well: also set `V7_G1`…`V7_G8` to `False`. The output is identical to v6.
- Whole flow: `CARO_LLM_FLOW=single`.

Do not ship `.env` (AWS keys; still to be rotated).

---

## 8. Files in this pack

| File | What it is |
|---|---|
| `caro/post_rules.py` | `apply_v8_rules`, the D-1 / D-2 / D-3 / D13 rules, `R_NPA_CUT`, and V8_CUT in `apply_adverse_triggers` (marked `PATCH 14-Oct-2026 (v8)`) |
| `caro/staged_llm.py` | Switches `V8_D1` … `V8_CUT` |
| `caro/pipeline.py` | Calls `apply_v8_rules` after v7, before the roll-up |
| `caro/audit.py` | `v8_rule` and `v8_cut_read_passage` in the audit file |
| `caro/amounts.py` | Unchanged since v6, included so one copy installs everything |
| `tests/run_phase2_cases.py` | V8-0 to V8-6 |
| `changes_v7_to_v8.diff` | Every changed line |
| `corrected_results_6CIN/` | Your six files with v8 applied |
| `tools/review_sheet.py` | **New:** review sheet and scorer for new companies |
| `tools/truth_v8.csv` | Truth with your 14-Oct decisions (read with `keep_default_na=False`) |
| `tools/score9.py` and the replay scripts | Reference copies of the scorer and replay scripts I used |
| `docs/` | 02 (decisions register, updated), 13, 22 (timeline), 23 (v7), 24 (CA guide, updated), 25 (this file) |
