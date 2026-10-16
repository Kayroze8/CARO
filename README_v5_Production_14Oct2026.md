# CARO code v5: production build for the two-call flow (14 Oct 2026)

**Install:** copy the five files in `caro/` over your pipeline's `caro/` folder. They are amounts.py, staged_llm.py, post_rules.py, pipeline.py and audit.py.
- Only staged_llm.py and post_rules.py differ from v4b.
- `tests/run_phase2_cases.py` should report **222/222**.

**Run with:** `CARO_LLM_FLOW=staged` in production, so that a filing the two calls cannot settle falls back to the single call. Use `staged_nofallback` only for test runs.

No other `.env` change is needed.

## 1. Root cause of the 14-Oct losses

The 14-Oct run fixed 82 labels and broke 81. I traced each of the 81 regressions to the step where it went wrong:

| Where the wrong label started | Regressions | Cause |
|---|---|---|
| The model's FIRST answer was already different | 43 | The new prompt lines (Call 1: AMOUNT ITEMS, ONE UNIT; Call 2: WHOLE TEXT, CLAUSE NOTES P2/P4/P15/P18) |
| Same first answer, wrong after the recheck note | 10 | False triggers: P8 "going concern assumption", P7C denial split by a full stop, P7B, P7E/F35 on a printed question |
| Same first answer, wrong after the code rules | 15 | Quote / source differences; code pushing a wrong main-clause NA down to empty sub-clauses |
| Reply not parseable for the comparison | 13 | - |

**The key fact:** the model runs at temperature 0. When we change the prompt, the model re-reads units we did not target. 182 first answers changed on identical text, and first-answer accuracy did not improve (85.6% on 13 Oct vs 85.2% on 14 Oct).

Code changes can be proven offline by replaying the saved replies. Prompt changes cannot be proven without a live run. That is why the code fixes held, while the prompt fixes moved labels both ways.

**The recheck itself is not the problem.** On 14 Oct the recheck turned:
- 72 wrong answers right and 21 right answers wrong on units that carried a note;
- 43 wrong answers right and 13 right answers wrong on other units of the same clause.

Most of the wrong turns came from the four false triggers.

## 2. What v5 changes

1. **Prompt freeze.** The model now gets exactly the 13-Oct prompts.
   - These switches are now off: AMT_PROMPT, ONE_UNIT, XWALK_LAYOUT, SUBJ_2016, P4, P15 and P18, plus P2's prompt line (new switch P2_PROMPT). P2's code part stays on.
   - Proof: on all 138 filings, the Call 1 and first Call 2 requests are **byte-identical** to the 13-Oct run's (system prompt, user message and Call 2 schema).
   - The only difference is that the Call 1 schema keeps the P13 6,000-character limit on source_text. The longest real field was 5,234 characters, so this only matters for a runaway field like the K.R. Foods loop.
2. **Every code fix proven by replay stays on.** That covers:
   - amounts;
   - P2 NA/clean table, P11, P16, P7A, F34, F35;
   - ALL_FALSE_RECOVER, the Rs 5 crore threshold, SCOPE_OWN, GUARD_INCOMPLETE.
3. **The four false triggers are fixed:**
   - P8: "going concern assumption" is the auditor's own term, never a guess.
   - P7C: needs a money figure (a date is not one), and does not fire when the sentence before says "no undisputed amounts".
   - P1: "except for the following: Nil" is an answer.
   - P7E/F35: a heading followed by the printed question is not an answer.
4. **P7B is off.** On 14 Oct it made 1 wrong change and no right ones.
5. **NA_PUSH_V5 (code).** Not Applicable is no longer passed down to empty sub-clauses when:
   - the main clause says loans WERE given ("The Company has provided loan during the current financial year"), or
   - the NA sentence is about another clause ("reporting under 3(ix)(e) and 3(ix)(f) is not applicable").

## 3. Proof

| Check | Result |
|---|---|
| Tests | 222/222 (5 new: V5-0 shipped switches, V5-1 to V5-4 each named after its filing) |
| Single flow | Byte-identical |
| JSON keys | Unchanged |
| Call 1 / Call 2 requests vs 13-Oct | 276 / 276 identical (Call 1 schema differs only by the P13 limit) |

Accuracy on independent units:

| Measure | 13-Oct run | 14-Oct run | v5 (replay of 13-Oct replies) |
|---|---|---|---|
| Your reviewed truth file (3,907 units) | 97.01% | 95.50% | **97.11%** |
| CA-corrected truth (3,930 units) | 95.85% | 95.95% | **96.11%** |

The v5 figures include 4 known replay artefacts counted as wrong, so the live figure should be slightly higher.

**v5 vs the 13-Oct run.** I read every difference. Of the 21 independent label changes:
- 15 are corrections (Universal (i)(a)(B) ×3 and (xx)(a)/(b), Sunstar (iv) ×2, Siddhivinayak (ix)(c) ×4, WickedRide (xi)(b)/(c), (xiv)(a) and 2022-23 (iii)(a)(A));
- 4 are replay artefacts;
- 2 move from one wrong label to another (Starlite 2024-25 (iii)(c), Siddhivinayak 2024-25 (iii)(a)(B)). The 67 amount changes are the verified amount fixes, for example:
- Ichalkaranji 2022-23 NPA Rs 9,060.87 lakh;
- Western 2024-25 (iii)(a)(B) Rs 17,98,32,141.73 (31-3-2025 column);
- WickedRide (iii)(a)(A)/(B) split by table row.

The 11-Oct replies show 0 label changes from v4b.

## 4. What cannot be proven without a live run

With the prompts frozen, the model's first answers should match 13 Oct (temperature 0; vLLM can still differ slightly between runs). The open part is the recheck notes added after 13 Oct (F35, P8, P2, P7E, P16):
- On the 13-Oct answers they would reach **46 more units**.
- Their 14-Oct record, once the false triggers are removed: they fixed 29 of 36 wrong first answers and broke very few right ones.
- On the 13-Oct answers, 15 of the 46 units are wrong and could be fixed. About 5 are right and could still flip:
  - Rajesh 2021-22 (xxi): the (xxi) rule shows it Favourable anyway;
  - Atharv 2021-22 (xiii) and 2022-23 (iv);
  - Viraj Alchohol (iii)(e) ×2: the note left them unchanged on 14 Oct.

In staged mode, three filings fall back to the single call, as on 13 Oct: Rajesh 2023-24, Apollo 2023-24, Atharv 2021-22.

## 5. Rollback and monitoring

- **Rollback:**
  - The whole flow: `CARO_LLM_FLOW=single` (no redeploy).
  - One fix: set its PATCH_SWITCHES entry to False.
  - Every trial note at once: set F35, P1, P2, P7C, P7E, P8 and P16 to False. The labels are then those of the 13-Oct run plus the replay-proven code fixes.
- **Watch on the first production batch, against these 13-Oct figures:**

  | Measure | 13-Oct figure |
  |---|---|
  | Adverse | 183 |
  | Auditor Unable to Verify | 95 |
  | Not Reported | 496 |
  | review_required_flag | 900 |
  | Red flags | 277 |
  | Recheck calls | 68 of 138 filings |
  | Would-fall-back | 3 |

  A move of more than about 5% in any of these means stop and look.

## 6. Not changed (your decisions)

- P17 convention: are arrears over six months Adverse or Qualified?
- Apollo 2020-21 (vii)(b).
- Signing off the 15 truth corrections.
