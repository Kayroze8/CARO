# CARO code v4b: fixes from the 3-CIN trial run (13 Oct 2026)

**v4b = v4 + one small addition (section 6).** If you already copied v4, copy the five files again; only `staged_llm.py` and the tests file differ from v4.

**What to install:** copy the five files in `caro/` over your pipeline's `caro/` folder. They are `amounts.py`, `staged_llm.py`, `post_rules.py`, `pipeline.py` and `audit.py`.

- `audit.py` is the same as in v3. It is included so the set is complete.
- `tests/run_phase2_cases.py` is optional. It should report **217/217**.
- No `.env` change is needed. Run with `CARO_LLM_FLOW=staged_nofallback` as before.

v4 is v3 plus the fixes below and nothing else. `changes_v3_to_v4.diff` lists every changed line.

## 1. What the trial run showed

The three companies:
- U15100WB2012PTC184651 Shri Dutt
- U15319DL1987PLC027698 K.R. Foods
- U45204UP2009PLC132673 Universal Contractors

The keys and the existing fixes were in order:

- **JSON keys:** same keys and structure (schema 4.1).
- **Trial flags:** they appear only in the audit file, as `trial:<fix>`. The client JSON shows only `review_required_flag = true`.
- **Red flags:** none lost.
- **Fixes seen working:**
  - **P16:** Shri Dutt (vii)(a) is now Adverse, with a red flag.
  - **P7A:** Universal (xx) is now Adverse.
  - **P11:** Universal (i)(a)(B) is now Not Applicable.
  - **P13:** no loop.
  - **Amounts:** Shri Dutt's (ii)(b), (iii)(a)(B) and (vii)(a) figures are correct.

The run also exposed **5 code problems**. All 5 are fixed in v4:

| # | Problem in the trial run | Fix (switch) |
|---|---|---|
| 1 | The model marked **every** figure "not counted", so the amount was lost. Examples: K.R. Foods 2024-25 (vii)(b) disputed VAT + CST Rs 21,96,40,409; 2020-21 (iii)(b) loan balance Rs 1,95,69,231. | `ALL_FALSE_RECOVER` (amounts.py). When nothing at all is counted, the money figures are given back to the normal code checks. Those checks drop previous-year, paid, threshold and area figures. |
| 2 | K.R. Foods 2021-22 (iii)(a): the own-question guard could not settle an "incomplete" answer. In `staged` mode this would fall back to the single call. | `GUARD_INCOMPLETE` (staged_llm.py). An "incomplete" answer whose explanation names only its own missing figures is not treated as cross-clause. |
| 3 | Universal 2024-25 (xx)(b): a blanket CSR statement copied to the sub-clause raised a false "wrong subject" note. | A blanket statement (the clause's own sentence repeated) is skipped by F35. |
| 4 | P7E fired on a printed question ("the aggregate amount during the year …"). | Question-text detection: a sentence that is mostly the official question's words and has no answer verb is not treated as an answer. |
| 5 | K.R. Foods 2024-25 (iii)(a)(A), (iii)(a)(B), (iii)(f) showed **Auditor Unable to Verify** from the **(iii)(d)** sentence "management has not provided us … overdue amounts … unable to comment". | `SCOPE_OWN` (staged_llm.py and post_rules.py). A could-not-verify sentence printed **after another sub-clause's letter** ("(d) If the amount is overdue …") belongs to that sub-clause. |

## 2. Three problems found while proving v4, also fixed

Before packaging I replayed all saved replies with v4: 13-Oct (138 filings) and 11-Oct. The replay found 3 problems in the first versions of fixes 1 and 5. **All 3 are fixed in the code you are getting.**

1. **Years read as rupees.** Aditya Auto 2023-24 (vii) has a disputed-dues table with the amount column blank in the text. The model rightly marked the periods ("2011-12, 2012-13", "AY 2013-14") "not counted". The first version of fix 1 counted them, which gave Rs 120.86 crore.
   - **Now:** only money-like figures are recovered. Periods, dates, years, share counts, areas and rates never are. The amount stays blank, as before.
2. **The Rs 5 crore threshold.** U51909DL2004PTC127807 2022-23 (ii)(b) lists the "in excess of Rs 5 Crore" threshold, the statement balance (833.43 lakh) and the books balance (767.79 lakh). The first version took the larger balance.
   - **Now:** the threshold of the Order is never a figure. The amount is the books-vs-statement difference, **Rs 65.64 lakh**, the same rule as Shri Dutt.
   - A main clause that has sub-clauses keeps today's amount rule (no recovery at main level).
3. **Fix 5 in its first form removed correct "Auditor Unable to Verify" labels.** It used subject words alone, which removed 8 correct sub-clause labels in each replay. Examples:
   - Jsons Foundry and Western Precicast (i)(b): "It is not possible to us to verify all the Fixed Assets";
   - Viraj (iii)(e): "Since no repayment terms … we are unable to comment the overdue amounts".

   These sentences are the unit's **own** answer. **Now:** a sentence counts as a sibling's only when it comes after a different sub-clause letter in the text. All of them are back to Auditor Unable to Verify, matching the CA truth.

## 3. Proof

| Check | Result |
|---|---|
| Tests | **217/217** (4 new: V4-D 'indicating compliance', V4-A periods vs money, V4-B Rs 5 crore threshold, V4-C sibling sentence) |
| Single flow | Byte-identical |
| JSON keys | Unchanged |
| Trial replay (3 CINs, 792 units) | 5 labels and 4 amounts change, all corrections (table below). **0 fallbacks** in `staged` mode (v3 had 2). |
| 13-Oct replay (7,380 units) vs v3 | **0 label changes**. 3 amounts filled: (ii)/(ii)(b) Rs 65.64 lakh difference, (iv) Rs 15 crore guarantee as co-borrower. |
| 11-Oct replay (7,200 units) vs v3 | 1 label change (below). 1 amount filled: the same (iv) Rs 15 crore. |

**Trial replay changes (CA view):**

| Filing | Unit | v3 | v4 | CA view |
|---|---|---|---|---|
| K.R. Foods 2024-25 | (iii)(a)(A), (iii)(a)(B) | Auditor Unable to Verify | Qualified | Right. The could-not-verify sentence is the (iii)(d) answer. (A)/(B) print only the question and "repayable on demand", with no amounts, so the answer is incomplete. |
| K.R. Foods 2024-25 | (iii)(f) | Auditor Unable to Verify | Qualified | Right. The answer says only "repayable on demand"; the aggregate amount and percentage are not given. |
| Universal 2024-25 | (xx), (xx)(b) | Qualified | Favourable | Right. "Section 135 … has been complied with" is a clean answer for both (a) and (b). |
| K.R. Foods 2020-21 | (iii)(b) amount | blank | Rs 1,95,69,231 | Right (closing loan balance). |
| K.R. Foods 2024-25 | (vii)(b) amount | blank | Rs 21,96,40,409 | Right (VAT Rs 20,95,74,867 + CST Rs 1,00,65,542). |

**11-Oct replay change:** Viraj 2023-24 (iii)(a)(A) goes from Auditor Unable to Verify to Qualified.
- The could-not-verify sentence is printed under "(d)", so the change is right.
- As a CA I would mark (A) **Favourable**: it gives the loan to an associate (Rs 4.86 crore) and its closing balance (Rs 15.88 crore).
- The P20 truth file says Not Reported. Please decide this one when you sign off the P20 truth list.

## 4. What is still model variance (not code)

These K.R. Foods units changed because the model read them differently in the live run. No code rule should set them. Watch them in the full run:
- **2021-22 (iv):** guarantee "to which s.185/186 are attracted"; the live run said Favourable. Per our rule, a law that applies is not a law complied with, so it should be Qualified.
- **2021-22 (i)(a)(B), (ix)(c), (xx)(b):** now answered from on-subject text; earlier they were Not Reported.
- **2022-23 (iii)(b):** Favourable. The sentence is a clean "not prejudicial".
- **2022-23 (xiv)(b):** Not Applicable. The CA truth is Qualified: no internal audit conducted although required.

## 5. Running all the files

You can run all the files now with v4 and `CARO_LLM_FLOW=staged_nofallback`. Send the same four outputs as for the trial (caro_results, caro_audit, extracted_llm_out, extracted_text, plus the api log). I will check every flagged unit as a CA.

## 6. v4b: "indicating compliance" added to the guessing words (P8)

P8 sends one re-read note when a Favourable / Not Applicable explanation guesses ("implying", "presumably", "by default" ...). The trial showed one more guess: K.R. Foods 2021-22 (iv), "the funds were utilized for the stated purpose, **indicating compliance**" (the auditor says only that s.185/186 *apply*).

- Added: only the phrase "indicating / indicates / indicated (full / due) compliance".
- Not added: a bare "indicating". In the saved replies it appears 17 times, and 13 of them are the auditor's own (xix) wording "nothing has come to our attention indicating material uncertainty", which is not a guess.
- Effect on the saved replies: the note fires on 3 units in all the saved replies (K.R. Foods 2021-22 (iv) in the trial; Atharv (iv) once in each of the 11-Oct and 13-Oct runs), so about 1 extra recheck call per run. The note never sets a label; the model re-reads once and its answer stands.
- Replay: 0 label or amount changes, single flow byte-identical, 0 fallbacks.
