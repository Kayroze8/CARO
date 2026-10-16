# v9 — Fixes 1–4, two-call only (15 Oct 2026)

All changes are code that runs after the model. What the model is sent is unchanged. Each fix has its own switch, and every unit a fix changes is recorded in the audit file.

## 1. Where each file goes

Copy these over the repository, keeping the same paths:

| File | What changed |
|---|---|
| `caro/post_rules.py` | Fix 1, Fix 2 and Fix 4b inside `apply_adverse_triggers`. Fix 3d inside `apply_blanket_na`. Fix 3b in `_v7_g2`. New `apply_v9_rules`, which holds Fixes 3a, 3c, 4a and 4b. All marked `PATCH 15-Oct-2026 (v9)` |
| `caro/pipeline.py` | Calls `apply_v9_rules` after v8 and before the roll-up. Companion fix in `_check_scope_limitation` |
| `caro/staged_llm.py` | Two-call-only flow (`llm_flow`) and the ten `V9_*` switches |
| `caro/audit.py` | Audit file entries `v9_rule_*`, `v9_vii_read_from_clause_passage` and `v9_minor_delay_kept_qualified` |
| `tests/run_phase2_cases.py` | 28 new checks (V9-0 … V9-27). The existing checks run with the v9 switches off, the same way v7 is handled |
| `tools/replay_staged.py` | Optional. The replay used for the proof in §4 |
| `changes_v8_to_v9.diff` | Every changed line |

Do not ship `.env`. The AWS keys still need to be rotated.

## 2. Switches (`PATCH_SWITCHES` in `caro/staged_llm.py`, all shipped ON)

| Switch | What it does |
|---|---|
| `V9_TWO_CALL_ONLY` | The two-call flow only: no fallback to the single call. An unset, `staged` or unknown `CARO_LLM_FLOW` all mean two-call with no fallback. A check that used to send the filing to the single call is now recorded as `would_fall_back` and the units are flagged for review. A filing whose two calls fail is reported as failed. The single-call prompts and code stay in the repository; `CARO_LLM_FLOW=single` is the emergency rollback |
| `V9_QSTRIP` | Fix 1: the Adverse checks never read the printed CARO question |
| `V9_VII_PASSAGE` | Fix 2: (vii)(a) arrears and (vii)(b) disputed dues are read from the whole (vii) passage. The passage is found by its subject, ends at (viii), has wrapped lines rejoined and the sibling's own text removed |
| `V9_IC_NOPROP` | Fix 3a: (i)(c) "does not hold any immovable property" → Not Applicable |
| `V9_G2_HEADER` | Fix 3b: the v7 G2 empty-exception-table rule. Root cause: `[^A-Z]` inside a case-insensitive pattern never matches a letter, so the table headers were never removed |
| `V9_D3_DISCLOSED` | Fix 3c: (iii)(a) loans given but the amounts only "disclosed in the financial statements" → Qualified (D-3) |
| `V9_BLANKET_GATE` | Fix 3d: blanket NA is applied only from a sentence that covers the whole clause (the N2 test, without requiring the word "any") |
| `V9_SCOPE_LOANS` | Companion to 3d: "has not provided **loans / guarantees / security**" is not Auditor Unable to Verify |
| `V9_D1C` | Fix 4a: (vii)(a) says "generally regular", no delay is stated, and the arrears question is unanswered anywhere in (vii) → Qualified |
| `V9_DELAY_QUAL` | Fix 4b: a delay in repaying a lender that was made good → Qualified. "Has defaulted", NPA or unpaid amounts stay Adverse |

**Never changed by v9:**
- an Adverse or an Auditor Unable to Verify label (Fixes 1, 2 and 4b act only inside the Adverse checks);
- a label set by another agreed rule;
- amounts.

v9 also applies only one rule per unit.

## 3. Run the tests

```
python tests/run_phase2_cases.py --v9-only     # expect: V9: 28/28 passed
python tests/run_phase2_cases.py               # full suite: expect 241 existing + 28 V9, all PASS
```

## 4. How it was proved (on your 14/15-Oct run: 158 filings, 8,508 units, no new model calls)

1. **Replay bench.** The saved two-call replies were run through the code. The unchanged v8 code reproduces 8,504 of 8,508 live labels. The 4 differences come from Mode B side inputs that are not in the logs.
2. **v8 against v9 on every unit:** 44 label changes in total, of which 27 are scoreable units and the rest main clauses following them. All are listed below and every one is intended.
   - Changes to source text, explanation or amount on any unit whose label did not change: **0**.
   - Amount changes overall: **0**.
3. **Scored against truth** (`truth_v8` with your 15-Oct decisions 4a and 4b applied; the new companies against the line-by-line review):

| Group | v8 | v9 | Fixed | **Broken** |
|---|---|---|---|---|
| Existing 35 companies (6,083 units) | 98.88% | **99.08%** | 12 | **0** |
| New 7 companies (924 units) | 96.75% | **98.38%** | 15 | **0** |
| All | 98.60% | **98.99%** | 27 | **0** |

4. **Rollback:** with all `V9_*` switches off, the output is identical to v8 on all 8,508 units: labels, amounts, red flags, review flags, text and explanations.
5. **Tests:**
   - 28/28 V9 checks pass. Each fix has real-wording cases plus counter-cases that must not change: Atharv, K.R. Foods, Rama Cylinders, Shripati, Viraj Canes, Committed 2023-24, Aditya, and a "has defaulted" table.
   - With the fixes switched off, the 10 positive checks fail, so the tests really exercise the fixes.
   - The existing suite gives results identical to v8 for every check that could run here (102). The rest could not run: `payload.json` and `caro_section_debug_json_foundry.txt` were not in the zip, so **please run the full suite on your machine**.

**The 27 corrected units:**
- **Fix 1:** Eleanor 2023-24 and 2024-25 (ix)(b), (ix)(d), (xvii): Adverse → Favourable.
- **Fix 2:** Committed 2022-23 (vii)(a), Starlite 2020-21 (vii)(b), Mbook 2024-25 (vii)(b): → Adverse.
- **Fix 3a:** Cellecor 2021-22 and Mbook 2022-23 / 2023-24 (i)(c) → NA.
- **Fix 3b:** Maa Kudargarhi 2023-24 and Whofers 2023-24 (i)(c) → Favourable.
- **Fix 3c:** U29119 2024-25 (iii)(a)(A) / (B) → Qualified.
- **Fix 3d:** Apollo 2021-22 (iii)(b) / (c) / (d) → Favourable / Qualified / Favourable. The main clause (iii) is now Qualified, not Auditor Unable to Verify.
- **Fix 4a:** Midas 2022-23 to 2024-25, Prasad 2023-24, Refit 2022-23, Sky Point 2022-23, Sahyadri 2024-25, Viraj Refinery 2024-25 (vii)(a) → Qualified.
- **Fix 4b:** nothing changes today (the pipeline already says Qualified for Apollo and Vananam). It stops future "delay … paid" wording from being escalated to Adverse.

**Not changed, needs your decision:** Vananam 2023-24 (i)(c) says "not having any immovable properties … hence this clause is not applicable". The agreed P2 rule ("…hence not applicable" after a good answer → Favourable) sets it to Favourable first, and v9 never overrides an agreed rule. If (i)(c) "no property" should be NA even with that wording, P2 needs an exception for (i)(c).

## 5. Rollback

- One fix: set its `V9_*` switch to `False`.
- All of v9: set every `V9_*` switch to `False`. The output is identical to v8.
- Single-call flow (emergency only): `CARO_LLM_FLOW=single`.
