# CARO code v6: the two-call flow with 13-Oct model requests (14 Oct 2026)

**Install:** copy the five files in `caro/` over your pipeline's `caro/` folder: amounts.py, staged_llm.py, post_rules.py, pipeline.py and audit.py.
- staged_llm.py, amounts.py and post_rules.py differ from v5.
- `tests/run_phase2_cases.py` should report **226/226**.

## 0. v6 final (14 Oct, 17:45): your decisions applied

- **P17: listed arrears over six months are Adverse** (code rule R_ARREARS_LISTED in post_rules.py, switch `P17_ADV`; two-call flow only; no amount needed, as decided in D14).
  - Before: the old rule needed a printed amount, and it read "no undisputed amounts ... **except following** ... were in arrears ... more than six months" as a denial, so a listed arrear stayed Qualified.
  - Now Adverse:
    - Ichalkaranji 2021-22, 2022-23 and 2023-24;
    - Aditya 2021-22 and 2024-25;
    - Shri Dutt 2022-23, 2023-24 and 2024-25 (the arrears table under the auditor's "(b)");
    - Committed Ispat 2023-24.
  - Never Adverse:
    - a plain denial;
    - "except as given below: Nil";
    - a disputed-dues table (forum / appellate);
    - the Order's own question wording ("The extent of the arrears ...", Viraj Alchohol 2023-24);
    - a denial broken by a stray full stop (Viraj Canes 2023-24; the same trap as the old P7C false trigger).
  - It changes **only the report labels**: every model request is still identical to 13 Oct (capture test 138/138 rerun after this change).
- **Apollo 2020-21 (vii)(b): Adverse.** v6 already gives Adverse; the truth file is updated.
- **WickedRide 2022-23 (iii)(a)(A): Favourable.** The table as source text is accepted; truth updated.
- Scoring now uses `truth_14Oct_decided.csv`, written next to `truth_13Oct_reviewed.csv`; the original is untouched. It adds 7 decided rows.

| Same replies, decided truth | Reviewed truth | CA truth |
|---|---|---|
| 13-Oct run, 138 filings | 96.88% | 95.71% |
| **v6 final, 138 filings** | **97.18%** | **96.16%** |
| 13-Oct run, 29 trial filings | 96.74% | 94.00% |
| **v6 final, 29 trial filings** | **97.64%** | **94.89%** |

**v6 final vs the 13-Oct run, every label change (34):**
- **21 corrections:**
  - (vii)(a) arrears: Ichalkaranji ×2, Aditya ×2, Shri Dutt ×3;
  - Siddhivinayak (ix)(c) ×4;
  - Universal (xx)(a)/(b) and (i)(a)(B) ×3;
  - U01110 (iv) ×2;
  - WickedRide (xi)(b)/(c) and (iii)(a)(A).
- **7 main clauses** that follow their sub-clauses (not scored).
- **1 still wrong either way:** Starlite 2024-25 (iii)(c).
- **4 replay artefacts:** steps with no saved reply (U29299 2022-23 ×3, U74999 2024-25 (xix)). Live these keep their 13-Oct labels.
- **1 with no truth in either file:** Siddhivinayak 2024-25 (iii)(a)(B).
- **Real regressions: 0.**

**Run with:** `CARO_LLM_FLOW=staged_nofallback` for the trial and `CARO_LLM_FLOW=staged` in production. No other `.env` change is needed.

## 1. Verdict on the v5 trial: it FAILED

| 29 trial filings (6 CINs) | Reviewed truth | CA-corrected truth |
|---|---|---|
| 13-Oct run | 864/889 = **97.19%** | 846/896 = **94.42%** |
| v5 live (your trial run) | 819/889 = **92.13%** | 807/896 = **90.07%** |
| v5 live, leaving out the failed filing (28 filings) | 819/853 = 96.01% (13-Oct on the same 28: 97.19%) | 807/860 = 93.84% (13-Oct: 94.30%) |

Siddhivinayak 2024-25 failed outright: no labels at all.

**The code rules are not the cause.** The same v5 code, fed the 13-Oct model replies, scores 97.64% / 94.87%. The losses came from the model's **recheck** answers.

**Root cause.**
- In v5 I froze the prompts but kept seven new recheck notes (F35, P1, P2, P7C, P7D, P7E, P8, P16), plus new wording in P6 and GUARD_INCOMPLETE.
- A note is extra text inside the recheck request. The model re-reads the whole clause, so a changed request changes answers on units that carried no note at all.
- What changed in the trial:
  - first Call 2 replies: 25 of 29 were identical to 13-Oct;
  - recheck replies: 11 differed, 2 rechecks were new (13 Oct had none) and 1 timed out;
  - labels: 20 changed between the replay and the live run, 17 of them wrong.
- Examples of the wrong ones:
  - K.R. Foods 2021-22 (ix)(b)-(f): Not Reported became Favourable or NA after the P8 note.
  - K.R. Foods 2022-23 and 2023-24 (iii)(a)(A)/(B): Not Reported became Qualified (P7E).
  - Viraj Canes 2020-21 (ii) and (ix): Not Reported became Qualified (P7E).
- The three right ones:
  - K.R. Foods 2020-21 (ii): UTV;
  - K.R. Foods 2023-24 (vi): UTV;
  - K.R. Foods 2023-24 (xiv)(b): Qualified.

**My mistake.** v5 judged the notes on their 14-Oct record. That record was measured on 14-Oct replies, and they cannot predict how the model answers a different request. It was the same error as the prompt changes, one level down.

**The rule from now on:** nothing that changes what the model is sent goes to production without its own live A/B run.

## 2. Timing: why the trial took 1 h 31 min

The same 29 filings took 4,248 s on 13 Oct and 5,173 s in the trial, 925 s more.

| Filing | 13-Oct | Trial | Extra | Cause |
|---|---|---|---|---|
| Siddhivinayak 2024-25 | 129 s | 721 s | +592 s | **Code** (fixed in v6, see below) |
| Ichalkaranji 2021-22 | 143 s | 357 s | +213 s | Server: reply byte-identical to 13-Oct, written at ~290 characters/s instead of ~750 |
| Ichalkaranji 2022-23 | 143 s | 311 s | +169 s | Server: same as above (last 15 minutes of the run) |
| Western 2022-23 | 144 s | 205 s | +61 s | Server: Call 1 identical, written at 266 characters/s |
| The other 25 filings | | | about -110 s in total | Within about ±30 s each |

**What happened to Siddhivinayak 2024-25.**
- Call 1 and Call 2 finished normally by 14:30:17.
- The recheck request carried the new F35 note on (ix)(c). The model's reply never finished.
- At 14:40:18 the HTTP timeout (600 s) stopped it, and the whole filing was marked Failed, although its first answers were complete.

Two code gaps made this possible:
1. The recheck was given the whole remaining context (about 23,000-35,000 tokens). The largest recheck reply in all runs so far is 18,465 characters (about 6,000 tokens), so a runaway reply could run for 10 minutes.
2. A timeout during the recheck was not caught. In `staged_nofallback` the filing failed. In production (`staged`) it would have fallen back to the single call, costing another 2-3 minutes and giving single-call labels.

**Already slow on 13 Oct, unchanged:** K.R. Foods 2021-22 Call 1 loops (171,603 characters, 219 s). The follow-up question recovers it in 34 s.

## 3. Amount units: only real number words multiply a figure

I tested every unit word through the full amount code, with the model wrongly marking each figure "counted". The table shows the result before v6 and with v6.

| Printed | Before v6 | v6 |
|---|---|---|
| Rs. 28.33 lakhs / lacs | Rs 28,33,000 ✓ | ✓ unchanged |
| Rs. 23.15 and Rs. 25.22 crore | Rs 48.37 crore ✓ | ✓ unchanged |
| Rs. 906087 thousand | Rs 90.61 crore ✓ | ✓ unchanged |
| Rs. 50 million | Rs 5 crore ✓ | ✓ unchanged |
| 0.075 under "(Rs. in Crore)" | Rs 7,50,000 ✓ | ✓ unchanged |
| 15,410/- under a lakh heading | Rs 15,410 ✓ (full rupees) | ✓ unchanged |
| Rs. 4,520 **hundred** | Rs 4,520 ✗ | **Rs 4,52,000** |
| 4,520 + 1,200 under **"(Rs. in hundreds)"** | Rs 5,720 ✗ | **Rs 5,72,000** |
| 4,520 + 1,200 under **"(Rs. in '000)"** / "(₹ ’000)" | Rs 5,720 ✗ | **Rs 57,20,000** |
| "rupees five hundred crore" (the (xx) threshold, no digits) | not a figure ✓ | ✓ unchanged |
| 110 acre / 25 bigha / 13 guntha / 3.5 hectare / 1,250 sq. ft | dropped ✓ | ✓ unchanged |
| 12 **kanal** / 18 **marla** / 45 **cents** of land | Rs 12 / 18 / 45 ✗ | **dropped (land area)** |
| 13 guntha **and** Rs. 361.75 lacs (same sentence) | Rs 3.62 crore ✓ | ✓ unchanged |
| 1,50,000 **equity shares** | Rs 1,50,000 ✗ | **dropped (count)** |
| Rs. 12,50,000 overdue for more than **90 days** | Rs 12,50,090 ✗ | **Rs 12,50,000** |
| 1792 **numbers** of ESOPs, **7.79%** | dropped ✓ | ✓ (now with the reason "count_or_period_not_money") |
| Rs. 5 crore working-capital limit ((ii)(b)) | dropped ✓ | ✓ unchanged |

**How v6 decides.**
- Only these words multiply a figure: lakh/lac, crore/cr, thousand, '000, hundred, million and billion.
- A figure with a land word right after it is never money. The land words are acre, bigha, biswa, guntha, kanal, marla, katha, cents, hectare and sq. ft/m/yd.
- A figure with a count, rate or period word right after it is never money. Those words are shares, units, numbers, %, days, months and years.
- Neither rule applies when "Rs" or ₹ is printed before the figure.
- The word must be on the same line as the figure. The first draft read "Percentage of loan" on the next line as "%" and dropped Starlite 2021-22 (iii)(f) Rs 1,23,21,685. The replay caught it before packaging; it is fixed and kept as a test.

**Honest scope.**
- None of the CARO texts in the four runs prints a "hundred" or "'000" heading. The only land areas printed are acre and guntha.
- So these were gaps that had not yet cost an amount in your data, not the cause of the trial's errors.
- Proof: on all four saved reply sets (11-Oct, 13-Oct, 14-Oct, trial; 23,520 units), v6 changes **0** amounts.

## 4. What v6 changes (v5 → v6)

1. **Every model request is again exactly the 13-Oct request, including the recheck.** These switches are now OFF:
   - the recheck notes F35, P1, P2_NOTE (new switch), P6, P7C, P7D, P7E, P8 and P16;
   - the recheck use of GUARD_INCOMPLETE (new switch GUARD_INCOMPLETE_NOTE);
   - P13 (the Call 1 schema limit).

   The code parts stay ON: the P2 NA/clean table, GUARD_INCOMPLETE in the final guard, P7A, P11, F34, SCOPE_OWN and NA_PUSH_V5.
2. **RECHECK_GUARD (code, ON):**
   - The recheck reply is capped at 12,000 tokens, twice the largest real one. Up to that point the model writes exactly the same text (temperature 0); only a runaway reply is stopped sooner.
   - A timeout or network error in the recheck keeps the first Call 2 answers instead of losing the filing.
3. **Amounts:** hundred and '000 headings, the land words kanal, marla, biswa, katha and cents, and the count/period check (switch NOT_QTY).

## 5. Proof

| Check | Result |
|---|---|
| Model requests vs the exact 13-Oct code, all 138 filings | **138/138 identical**: 405 requests, including 128 recheck / follow-up requests (system prompt, user message and schema) |
| Same check on the trial's own replies | 29/29 identical |
| Tests | 225/225 (new: V6-0 shipped switches, V6-1 recheck timeout, V6-2 unit words, V6-3 land / count / period) |
| Single flow | Byte-identical |
| JSON keys | Unchanged |
| Amounts on 23,520 saved units | 0 changes |

Accuracy, replaying the 13-Oct replies. Because the requests are identical, this is what a live run gives if the server repeats itself:

| | Reviewed truth | CA truth |
|---|---|---|
| 13-Oct run, 138 filings | 97.01% | 95.85% |
| **v6, 138 filings** | **97.08%** | **96.08%** |
| 13-Oct run, 29 trial filings | 97.19% | 94.42% |
| **v6, 29 trial filings** | **97.64%** | **94.87%** |

v6 against the 13-Oct run, 21 changed labels, each read as a CA:

- **8 corrections:**
  - Siddhivinayak (ix)(c) ×4, NA → Favourable;
  - Universal 2021-22 (xx)(a)/(b), Qualified → Adverse;
  - WickedRide 2024-25 (xi)(b)/(c), NA → Favourable.
- **7 with no reviewed truth; 5 of them agree with the CA truth:**
  - Universal (i)(a)(B) ×3, Favourable → NA;
  - U01110 (iv) ×2, Favourable → NA.
- **1 still wrong** (it was already wrong on 13 Oct): Starlite 2024-25 (iii)(c).
- **4 replay artefacts** (no reply saved for that step): U29299 2022-23 (ii)(b), (xi)(b), (xi)(c) and U74999 2024-25 (xix). These are counted as wrong above, so live should be slightly higher.
- **1 against your truth file: WickedRide 2022-23 (iii)(a)(A), NR → Favourable.**
  - The auditor's table gives the loans to subsidiaries: Rs 576.50 lakh granted and Rs 2,744.41 lakh outstanding.
  - As a CA I read that as a Favourable answer. Please confirm, or keep NR.

**What we give up for now:** the three right answers the notes produced in the trial (K.R. Foods 2020-21 (ii), 2023-24 (vi) and (xiv)(b)). Each note can come back later, one at a time, with its own live A/B run on these 6 CINs.

## 6. What a live run can still change

The server itself is not perfectly repeatable. In the trial, Viraj Canes 2023-24 and 2024-25 got slightly different Call 2 replies to byte-identical requests. No label changed, but small differences like this are possible.

## 7. Rollback

| To roll back | Do this |
|---|---|
| The whole two-call flow | `CARO_LLM_FLOW=single` |
| The recheck guard | `PATCH_SWITCHES["RECHECK_GUARD"] = False` |
| The count / period check | `AMOUNT_SWITCHES["NOT_QTY"] = False` |
| Any single note, for a later A/B run | Its PATCH_SWITCHES entry set to True |

## 8. Still your decisions

- P17: are arrears over six months Adverse or Qualified?
- Apollo 2020-21 (vii)(b).
- WickedRide 2022-23 (iii)(a)(A).
- The 15 truth corrections.
