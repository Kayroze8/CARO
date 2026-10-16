# CARO patch 12-Oct-2026 (Phase 2) — changed files only

Baseline: the 11-Oct two-call run. Tests: `python tests/run_phase2_cases.py` → **188/188** (was 164/164; new checks B1–B14 (with B3b, B5b, B11b), B15, B16, B17, B17b, B18 + F8b, F8c, plus 6 updated checks).
Line endings are kept as in your repo (Windows CRLF), so `git diff` shows only the real changes.

## 1. Files to copy (replace the existing file at the same path)

| File | What changed |
|---|---|
| `caro/llm.py` | 4 lines: remembers whether the reply ended normally or was cut off (`last_finish_reason`). Return value is unchanged. |
| `caro/staged_llm.py` | Two-call flow: cut-off handling, JSON repair, prompt rules (Step A, F05, F09, F10, F16, F17), notices F09–F16, quote expansion F11, review labels. |
| `caro/post_rules.py` | F12, F13, F19. Every change runs **only on two-call records** (`_staged`), so the single flow is untouched. |
| `caro/pipeline.py` | F02, F25 (report "Failed" instead of inventing labels), F27 (standard silent-unit sentences), T01 (sub-clause titles). |
| `caro/caro_clause_map.json` | Sub-clause titles (39 for 2020, 8 for 2016), (iii) ICD subject words, iii_c map fix, 2016 (ix) question. Version string is unchanged. |
| `CLAUDE.md` | Records Toby's (ix)(e)/(f) decision (rule list and open item 4). |
| `tests/run_phase2_cases.py` | 6 checks updated for the intended behaviour, 16 new checks. |

**Do not copy or touch:** `.env` (it contains AWS keys, so rotate them), `__pycache__/*.pyc` (tracked; do not delete), `prompts.py` (unchanged, so single-flow test D stays byte-identical).

Before running the tests, restore the 3 tracked files that are deleted in your working tree:
```
git checkout -- payload.json caro_section_debug_json_foundry.txt caro_section_debug_json_foundry_2.txt
python tests/run_phase2_cases.py        # expect 188/188 (run with LLM_STRUCTURED_OUTPUT unset)
```

## 2. .env changes (no new switches)

**For the rerun, remove the line `LLM_STRUCTURED_OUTPUT=response_format`** (or set it to `guided_json`).
Keep `LLM_GUIDED_JSON=true`, `LLM_MAX_TOKENS=35000`, and `CARO_LLM_FLOW=staged_nofallback` for the test run (`staged` later for clients).

Why (13-Oct rerun, 3 CINs / 11 filings): with `response_format` the server really enforced the JSON templates.
The Call 2 template required only `sub_clause_id` and `mapping`, so the model returned just those two fields for every
unit - no label, no quote - and every unit became Not Reported with source "-". Fixed in this package (NO_LABELS):
the Call 2 template now requires label, quote and explanation, and such a reply fails loudly.
The **single** prompt's template has the same gap (quote and explanation optional); fixing it changes the single
request, which must stay byte-identical (test D5), so it needs your go-ahead. Until then `response_format` must not be
used with the single flow or with `staged` (its fallback is the single call). Broken JSON without enforcement is
handled by the JSON repair (30 of 31 broken 11-Oct replies repaired).

**Rollback without editing .env:** set a fix to `False` in `PATCH_SWITCHES` near the top of `caro/staged_llm.py`
(`CUTOFF, F05, F09, F10, F11, F12, F13, F14, F15, F16, F19, F33, F34, STEP_A, NO_LABELS`; the post-rule fixes F12, F13, F19
and F33 read the same list). Proof (13-Oct): with every switch off, all 135 replayable 11-Oct filings give exactly the
original code's labels, red flags, amounts and quotes (0 differences). Always on (agreed): F25 Failed instead of invented
labels, F27 standard silent sentences, T01 titles, quote limit 1500.

## 3. Fixes safely patched

Principle: code never hard-codes a label. It checks, sends a notice, and asks the model **once** (the recheck). The model's second answer stands.

| ID | Problem on 11-Oct (plain words) | Fix | Where | Test |
|---|---|---|---|---|
| CUTOFF (F01–F04) | Replies cut off at the token limit, or repeating text, were treated as garbage, so whole filings were lost | Uses the server's "cut off" signal. Repeated text is only logged; a cut-off reply drops its last half-written record and keeps the rest | staged_llm `_ask`, llm.py | B2, B3, P9 |
| JSON repair | 30 of 31 broken replies were missing one comma or quote | Repairs up to 60 such slips, else asks again | staged_llm `repair_json` | B1 |
| F02 | No-fallback mode crashed the whole filing | Records a failure and stops cleanly | pipeline | F3, F8 |
| F03/F04 | Call 1 copied the same lines many times (output 4.7× the CARO text) | Collapses repeats against the source text (logged) | staged_llm `collapse_repeats` | B3b |
| STEP_A | Model paraphrased, shortened ("…"), repeated | Copy rules in Call 1; quote/explanation rules in Call 2 | prompts in staged_llm (13-Oct: escape rule fixed, now reaches the model as \\" and \\\\) | B5, B5b |
| Schema 1500 | Quotes cut at 600 characters lost the deciding words | Quote limit raised to 1500 | staged_llm schemas | B6 |
| F05 (Option A) | 2016 (ix) marked NA though term loans were applied | Rule plus notice, then recheck (CARO 2016 only) | staged_llm, clause map | B5 |
| F09 | "Clean" or NA given while the text states an exception | Exception list (with non-exceptions, so no false alarms), plus notice | staged_llm `stated_exception` | B4 |
| F10 | (vii)(b) used the arrears sentence | Notice, then recheck; the answer moves only when the model moves it | staged_llm | — |
| F11 | Quote with "…" in the middle | Rebuilds the full continuous sentence from the source (≤1500 chars). 13-Oct: never across another clause marker such as (b)/(c) - a quote stitched from two points stays as it is | `expand_elided_quotes` | B11, B11b |
| F12 | Main clause NA spread to subs even when the sentence said "except…" | No spread if there is no NA statement or there is a carve-out (two-call only) | post_rules `na_covers` | B7 |
| F13 | (vi) cost-records disclaimer read as an exception; 4 scope phrasings missed | Disclaimer ignored for the exception test; 4 scope forms added | post_rules, staged_llm | B8, B9 |
| F14 | Sub verdict quoting another clause's sentence | Notice; extra main added to recheck; if still wrong, review label `verdict_quotes_another_clause` | staged_llm | — |
| F15 | Silent sub while the passage answers it | Notice (4 exclusions) | staged_llm | — |
| F16 | iii(a)(A)/(B) slots swapped | Definitions in prompt, plus notice | staged_llm | B12, F9, P12 |
| F17 | (xiv)(b) silent marked NA | 3-row rule: silent = insufficient_evidence, never NA | staged_llm prompt | R17 |
| F19 | Adverse trigger looked only at the first copy of the clause | Checks every copy (two-call only); clearer hint wording; iii_c map fix | post_rules, clause map | B10 |
| D14 | Arrears rule said "non-nil amount" | Statutory-arrears line replaces it | staged_llm prompt | B5 |
| F25 | When every model attempt failed, rule-based labels were invented | Filing reported **Failed** with the error | pipeline | F8b |
| F27 | Silent-unit wording varied | The two agreed standard sentences; the model's text kept in `llm_explanation` | pipeline | B14 |
| NO_LABELS (13-Oct) | With the template enforced, Call 2 returned no labels; every unit showed Not Reported | Call 2 template requires label, quote, explanation (prompt's field order); more than half the units with text unlabelled after the follow-up = failure (staged → single call; test mode → Failed) | staged_llm `call2_schema`, `run_staged` | B15, F6, F8c |
| Log (13-Oct) | Log said "single LLM request" for two-call filings | Log line names the flow; output unchanged | pipeline | — |
| F33 (13-Oct) | Cellecor 2021-22 (xvii): 'not incurred ... and amounting to Rs. 2.42 Lacs cash losses in immediately preceding year' - the word 'not' blocked the cash-loss red flag | For (xvii), two-call records only: a cash loss stated with a NON-ZERO amount triggers the existing cash-loss Adverse; plain denials, Rs. Nil / 0.00 and negated amounts do not. 11-Oct replay: exactly this one unit changes (→ Adverse, as CA review) | post_rules `cash_loss_with_amount` | B16 |
| F34 (13-Oct, Toby's decision) | (ix)(e)/(ix)(f): same wording labelled Favourable 24× and NA 31× | Call 2 rule (CARO 2020): NA only when the auditor says no subsidiaries/JVs/associates (denial covering subsidiaries) or that the clause does not apply; 'no such funds taken / loans raised' = Favourable. Recheck notice when the label conflicts; the model's second answer stands. 11-Oct replay: 17 notices (Starlite 8 → NA, Siddhivinayak 6, Viraj 2, Cellecor 1 → Favourable); 'no JV and associates' with subsidiaries left Favourable | staged_llm `_CALL2_IX_EF`, `batch_notices` | B17 |
| T01 | `sub_clause_title` blank | Titles from the clause map (see Word file) | pipeline, clause map | B13 |

Review labels: every patched unit carries `staged_trace.patch_fixes`. In `staged_nofallback` it also carries review reason `patch_check:<fix>`, so the rerun shows exactly where each fix acted.

## 4. Not applied (as instructed)
- **F18**: not applied.
- **F06, F26, F28**: by design (your requirement). Not changed and no longer flagged.
- F07: just restore payload.json via git (above). F08: performance only. F20–F24, F31: Tier 3. F29/F30: deferred; measure after the rerun.

## 5. Evidence: 11-Oct replies replayed through old vs new code (no model calls, 138 filings, 0 errors)
Judgement accuracy on 4,106 CA-scored units: **97.35% → 97.59%**; on the 204 hard, CA-reviewed units: **48.5% → 55.4%**. No amount changed.
19 labels changed: 14 fixed, 4 differ from the truth table, read one by one:
- Jaywant 2020-21 (iii)(c) → Not reported: **new is right** (loans were given 'except in normal course'; (c) never answered). Truth table to be corrected.
- Sunstar 2023-24 (iv) → Not applicable: **consistent** with the same wording CA-reviewed in 2021-22 and 2024-25.
- Committed 2020-21 (xiv) and Garden Court 2022-23 (i)(a)(B): **replay artefacts** - JSON repair rescued a first reply that had inferred an answer; on 11-Oct that reply was broken and the re-ask was correct. In the live run JSON is enforced by the server and Step A forbids inferring. Watch both.
13-Oct: F11 guard added (18 stitched quotes no longer expanded; zero label changes).

## 6. After your rerun
Send the new audit JSONs and the stage files. I will run `rerun_verdict.py` and `patch_check.py` against the 11-Oct baseline, then give a keep / roll back / adjust decision for each fix, with a CA reading of each changed unit.

## 7. 13-Oct test run (3 CINs, 11 filings, with response_format) - findings
- **Main fault (fixed, NO_LABELS):** with the template enforced, Call 2 and the recheck left out the label; all units showed Not Reported / source "-". On 11-Oct (no enforcement) this happened 0 times in 6,284 units.
- **Call 1 + Step A worked:** all replies ended normally (no cut-off, no broken JSON); quotes not word-for-word 35 → 6, "..." 12 → 0, no repeated lines, no placeholders. The 6 left are joined table cells (Starlite (iii)) and two small wording slips (Cellecor (vi), (x)(a)); the evidence check handles them.
- Cellecor 2023-24 and 2024-25 (lost on 11-Oct) now get complete Call 1 replies.
- F10 worked on Starlite 2022-23 (vii)(b): the recheck moved to the disputed-dues sentence (its label was lost to the same template gap).
- Call 1 replies are still 3.4–6× the CARO text (indented JSON, main + sub text): cost/time only, not accuracy.
- Not errors: Cellecor 2020-21 "NonXBRL" (same as 11-Oct); one damaged Cellecor AOC-4 PDF; old forms carrying the pre-conversion CIN (U…PTC). The run was stopped during Rajesh Exports.

## 8. 13-Oct rerun, Cellecor (L32300DL2020PLC375196) - checked against the CA truth table
- 4 filings Success; 2020-21 NonXBRL with the existing 'scanned documents' Remark (same as 11-Oct).
- 2023-24 and 2024-25 (lost on 11-Oct, 3.6% / 3.5%) now **100%**. Company total **99.05%** (210 units) vs single 98.57%, 11-Oct 47.6%.
- Titles filled, no rule jargon or guessing words in explanations, silent units use the standard sentence, amounts correct.
- (xvii) 2021-22 Qualified → Adverse with this package (F33).
- (ix)(f) 2021-22 Favourable vs truth NA: open policy point (same as Viraj (ix)(e)/(f)) - needs Toby's decision.
- (ix)(e)/(f) decided by Toby 13-Oct (F34); truth table: 21 entries set by the rule (issue ix_ef_rule_13Oct); K.R. Foods 2022-23 (ix)(e) 'difficult to comment' left as it was.

## 9. Safety audit (13-Oct, final code vs your original code)
| Check | Result |
|---|---|
| Single flow - request sent to the model | byte-identical (tests D5, 4 fixtures) |
| Single flow - final JSON | identical except 110 blank `sub_clause_title` now filled (0 existing titles changed) |
| Final JSON keys / schema 4.1 | unchanged (tests G2, G3, J6) |
| Two-call flow, 135 filings of 11-Oct | 31 labels changed, 0 amounts; judgement accuracy 96.94% → 97.21% (CA truth, 4,116 units) |
| All switches off | 0 differences from the original code (labels, red flags, amounts, quotes) |
| Red flags | +8 added (Western (vii)(b), Cellecor (xvii), ...), 2 removed: Apollo (xix) false going-concern (CA: Favourable) and U15511 (iii)(a)(B) - the 'unable to comment' stays flagged on (iii)(d) and main (iii) |
| 3 filings lost on 11-Oct | replay shows Failed (their Call 2 reply was never saved); the original code showed them 'Success' with every unit Not Reported |
| Prompt size | Call 2 +~6.5k characters (2020); reply allowance still ~28k tokens |

(ix)(e)/(f) wording (Toby 13-Oct) in Call 2: Favourable = no funds taken / no loans raised without declaring the clause inapplicable; Not Applicable = clause declared not applicable, or the entities do not exist and the clause therefore does not apply (Starlite stays NA); exception - 'no JV / associates' while subsidiaries exist is not Not Applicable by itself.
