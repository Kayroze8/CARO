# CARO pipeline – Phase 2 (Option A: extract and map, then check and classify), 9 October 2026

**Nothing changes for clients until you switch it on.** The default is `CARO_LLM_FLOW=single`, which
sends today's request byte for byte. Phase 2 runs only with `CARO_LLM_FLOW=staged`; any problem in a
filing sends that filing back to today's single call automatically. `CARO_LLM_FLOW` is still the only
`.env` switch.

This replaces the 8-Oct "sentence-number" design. On Atharv (9-Oct run) that design threw away printed
question + answer lines, thinned the clause text the post-rules read, and lost a main clause's
disputed-dues rows. Option A keeps today's record shape and the auditor's own words throughout.

---

## 1. The flow (plain words)

```
Edition + layout (edition.py, unchanged: FY rule, report layout, D06)
   │
CALL 1  EXTRACT AND MAP     the model places the auditor's own text under every official CARO
   │                         clause / sub-clause (full text, short quote, figures as printed,
   │                         disputed-dues rows). No labels. Printed question + answer kept together.
   │
CODE    all main clauses returned? (one follow-up question if not) · copy-loop check ·
   │    auditor passages no clause used ("unplaced text") · subject hints from the CA clause map
   │
CALL 2  CHECK AND CLASSIFY  for EVERY clause / sub-clause:
   │      1. does the placed text really answer THIS clause's official question (by full text,
   │         subject and meaning – never the printed number)? If not, which clause it answers
   │      2. the label, a short exact quote and the explanation
   │
CODE    a proposed move is applied only when the moved words really are that clause's text
   │    (or words of the input); then today's record shape is built
   │
TODAY'S CODE, UNCHANGED   validate_evidence → every post-rule → alignment → Phase 1 → roll-ups →
                          labels (Not Reported / Not Available / Auditor Unable to Verify) →
                          Remarks → final JSON 4.1
```

**Two model calls per filing** (one follow-up per call only when something is missing). Call 2 does
not get the annexure again – only what Call 1 placed, plus the unplaced passages.

### What each call is told

* Every paragraph of today's system prompt is copied **word for word** into Call 1, Call 2, or both,
  or dropped with a reason (e.g. flags and scores, which code computes). The test fails if a paragraph
  of today's prompt is not placed. Call 1 gets the mapping and extraction rules; Call 2 gets the mapping
  rules (to check) and every classification rule (adverse triggers, scope limitation, Favourable vs Not
  Applicable, the agreed (xv) / (xvi) / (xxi) / CSR conventions, roll-up).
* Both calls get the **CA clause map** (`caro/caro_clause_map.json`): the official question of every
  clause and sub-clause for the filing's Order, the "do not confuse" notes, and – for a CARO 2016-layout
  report in a CARO 2020 year – the crosswalk (including the (iii)(a)/(b)/(c) → (iii)(b)/(c)/(d) letter
  shift).

### Labels are still decided by today's code

* **Not Reported by Auditor** – no proven text for the clause (narrative report).
* **Not Available in Source** – the MCA form has no box for it, or the box says "As per Attachment" and
  the attachment is missing.
* **Auditor Unable to Verify** – only when the quote is the auditor's own "unable to verify / not produced
  before us" wording (today's rule). The model never sets it.
* A move that cannot be verified is not applied: today's result stands.

---

## 2. The CA clause map (reviewed 9-Oct)

`caro/caro_clause_map.json` is your 9-Oct map with a CA review:

* **Correct:** 60 CARO 2020 units (21 + 39) and 24 CARO 2016 units (16 + 8), same order as the
  pipeline's catalogue; high-impact and red-flag lists identical to the code; the 2016-layout crosswalk;
  all 12 corrections to the Word file (e.g. (ix)(c) ← 2016 (ix) term loans, (x)(b) ← 2016 (xiv), (xii)
  has three sub-clauses, (xvi)(b)–(d) have no 2016 counterpart).
* **Sharpened (15 changes, listed in the file under `ca_review`):**
  * "words of another clause" for the subject check – e.g. *stock / inventory physically verified* under
    (i)(b) belongs to (ii)(a); *revalued … or intangible assets* under (i)(a)(B) is (i)(d); *overdue >
    90 days* under (iii)(c) is (iii)(d); *pledge* under (ix)(e) is (ix)(f); *internal audit* or an
    annexure heading under (xxi) is not (xxi);
  * (ix)(a) also recognises "regular in repayment of dues to banks"; (xxi) recognises "standalone";
  * (iii)(a): the Order's own proviso – not applicable to a company whose principal business is to give
    loans;
  * a 2016-style "no loans to parties in the s.189 register" covers only s.189 parties.
* Tested on your mapping workbook: the subject words flag **52 of 52** known wrong placements
  (previously 42); on Atharv they also flag one wrong placement in the 8-Oct output – 2024-25 (iii)(a)(A)
  holds the "terms … not prejudicial" sentence, which is (iii)(b).
* The subject check only adds a **hint** to Call 2 ("words of another clause found"); code never moves
  text on its own.

---

## 3. Files changed

| File | Change |
|---|---|
| `caro/staged_llm.py` | Rewritten for Option A: CA clause map, rule registry, Call 1 / Call 2 prompts and shapes, checks, verified moves, today's record shape, fallback |
| `caro/caro_clause_map.json` | **New** – your clause map + the CA review |
| `caro/pipeline.py` | Hook comments; `_source_remark`: **no extra Remarks sentence** (N3's "(xix) taken from the main audit report" removed – only the existing Remarks are used); unused `cin` removed from the input context |
| `caro/llm.py` | Offline mock answers Call 1 / Call 2 |
| `caro/audit.py` | Per-unit trace of the mapping check and every text move |
| `caro/post_rules.py` | `SCOPE_LIMITATION_RE` wider (approved 9-Oct, both flows) – see section 6 |
| `tests/run_phase2_cases.py` | Rewritten for Option A (164 checks) + `--replay` |

Unchanged: every extraction module, `prompts.py` (today's prompt), all other post-rules, `amounts.py`,
`gap_resolver.py`, roll-ups, `schema.py`, `cin_pipeline.py`, the final JSON keys (schema 4.1).

---

## 4. How it was proved here (no model, no client data)

| Proof | Result |
|---|---|
| Single flow: the HTTP request of 4 filings compared with the 08-Oct golden request | **Byte-identical** |
| Final JSON: keys / structure of the two-call flow vs the single flow (Viraj 2020-21 CARO 2016, Jsons Foundry 2023-24, a synthetic CARO 2020 filing, a synthetic MCA table) and the strict schema (extra keys forbidden) | **Identical keys, 0 schema failures** |
| `--replay`: saved single-call replies fed through the Option A plumbing (same judgement in both flows) | **204 units: 0 label, 0 amount, 0 red-flag, 0 review, 0 source-text differences** |
| A real filing whose printed numbering is off by up to two (Jsons Foundry: its "viii)" is (v), "xxii)" is (xx)) – a correct model | Labels as a CA reads them; (vii)(b) Rs 2.73 crore and (xx) Adverse with red flags, by code |
| Same filing, Call 1 maps by **printed number** (the error you reported) | Call 2 moves every misplaced line back – unnumbered (i) lines come back through "unplaced text" – every move verified; same final labels as the correct model |
| Question-and-answer layout (Atharv type, synthetic text) | The printed question reaches Call 2 with its short answer: "Not Applicable" under the Nidhi question → (xii)(a)–(c) Not Applicable; "No, … not incurred any cash losses" → (xvii) Favourable |
| Moves with words not in the clause's text, or to an unknown clause | Rejected; nothing changes |
| Every fallback path, the test mode, old `chat(system, user)` clients | Covered |
| Your 5 Atharv inputs through the two-call flow (mock model) | Run cleanly, same keys, 0 schema failures; both system prompts are smaller than today's single prompt |

**No accuracy gain is claimed from these tests** – they prove the plumbing adds nothing and loses
nothing. Whether the model maps and judges better can only be shown by live runs (section 5).

---

## 5. What to run on the company machine (in this order)

```
# 0. the offline checks
python tests/run_phase2_cases.py                       # expect 164/164

# 1. plumbing replay on saved single-call runs (any earlier run's logs)
python tests/run_phase2_cases.py --replay --text-dir <run>/extracted_text --llm-dir <run>/extracted_llm_out --out phase2_replay.csv
#    expect 0 label / amount / red-flag rows

# 2. N4: if vLLM ignores guided_json, set
LLM_STRUCTURED_OUTPUT=response_format

# 3. Atharv in test mode (never the single call), then compare with the 8-Oct JSON
CARO_LLM_FLOW=staged_nofallback  (your usual --cin command for U74900PN2011PTC138795)

# 4. the 8 CA-key CINs: 3 runs single, 3 runs staged; score both against the CA key
```

Per filing in the audit file: `llm_reply.staged.call1 / call2` (reply sizes, follow-ups),
`moves` (every move: from, to, words, result), `unplaced_text`, `subject_hints`, `labels`,
`would_fall_back` (test mode). Saved replies: `<stem>__stage1.txt` (Call 1) and `<stem>__stage2.txt`
(Call 2) in `logs/extracted_llm_out/`.

Expected cost: 2 calls in a row instead of 1. Call 1's reply is shorter than today's (no labels,
explanations or scores); Call 2's reply is a label, quote and explanation per clause. Expect roughly
1.1–1.4 × today's model time per filing – to be measured in step 4.

---

## 6. Things to note

- **"Auditor Unable to Verify" (approved 9-Oct):**
  - today's wording rule (`post_rules.SCOPE_LIMITATION_RE`, **both flows**) now also reads "it is not
    possible **to** us to verify" (Jsons Foundry (i)(b)), "not shown / submitted / given to us", "not been
    able to", "could not check / ascertain / confirm", "cannot be verified";
  - in the two-call flow, Call 2 also judges it **by meaning** (typos, word order, paraphrase – e.g.
    "we were not able to physically verify the inventory") and returns `scope_limited`. Code accepts it
    only when the unit's own quote says the auditor could not verify / comment, or that records were not
    given to them – so "no material discrepancies were noticed on verification" or "the company has not
    provided any guarantee" can never become a false red flag. Accepted / rejected is in the audit file
    (`staged_trace.scope_limited_by_meaning`).
- **Security:** the repository contains a committed `.env` with AWS keys. Please rotate those keys and
  remove `.env` from the repository history (not deleted by me).

---

## 7. 9-Oct fixes after the Atharv Option A run

| Problem seen on Atharv | Fix (logic, not a one-off patch) |
|---|---|
| 2023-24 (iii)(b) "terms … not prejudicial" turned **Qualified** because "(iv) does not confirm s.185/186" – the CROSS-CLAUSE CONSISTENCY rule stretched to another clause's question | **Own-question rule.** Call 2 sees each clause's official question next to its text and returns `own_text_label` (the label of the clause's own text against its own question) and a `basis` for every Qualified / Adverse. A cross-clause downgrade is accepted by code **only** when the rule's own conditions hold: this clause's answer says *nothing happened*, another clause states a contradicting **fact**, and that fact is quoted word for word from the other clause's text. A reason that rests on another clause – declared, or detected from the explanation (by clause number *or* by that clause's subject words absent from this clause's own text) – is sent back once; if it still rests on another clause, that filing uses today's single call. Nothing that rests on another clause is ever output. A main clause with sub-clauses takes its label from them (today's roll-up). On the Atharv replies, the check flags exactly one unit in 264 – that one. |
| 2021-22 (x)/(x)(b): "1,50,00,000 Equity Shares" counted as ₹1.5 crore | Call 1 is told counts are never money, and code drops a figure that is followed – wherever it is printed – by shares / units / debentures / days / months / years (unless a ₹ / Rs sign precedes it). |
| 2021-22: one missing comma made Call 2 repeat the whole reply; the audit called it a "copy loop" | Broken JSON is salvaged (18 of 21 clause records on that reply) and only the missing clauses are asked again. The copy-loop check reads only the text inside values, so repeated JSON structure is never a "loop". |
| Headings that answer no clause logged as "rejected: unknown unit" | Logged as "not evidence: answers no clause". |
| 2021-22 (vii)(b): "there are no pending litigation …" then "Following are the pending Litigations of Income Tax Act: AY 2018-19 …" – Favourable in both flows | **The model decides, not a fixed rule** (Toby, 9-Oct). Call 2 is told to read each answer as a whole, as a CA would: when a denial is followed by listed items, decide whether they contradict it (SELF-CONTRADICTING DISCLOSURE). Code only notices the pattern in the unit's own text (denial + "following are / as under" + listed years / sections / amounts, or disputed-dues rows) while the label is Favourable / Not Applicable, and asks Call 2 once to read it again. Call 2's second answer stands; if it is still Favourable it is logged for review (`denial_then_list_guard`). |

### 7a. After the second Atharv run (9-Oct evening)

| Problem seen on Atharv | Fix |
|---|---|
| 2021-22 (ii)/(ii)(a): "procedure of such verification followed is reasonable and adequate" came out **Not Reported by Auditor**; (x)(b) "had issued 1,50,00,000 Equity Shares" and (vi) "under process for maintaining proper records" the same way. Call 2 quoted the auditor's answer and confirmed it belonged to the clause, but labelled it `insufficient_evidence` – against today's rule ("text empty / unintelligible / clause completely omitted"). | **The model decides, not a rule.** Call 2 is told that `insufficient_evidence` means the auditor did not answer the question; an answer that states an outcome is judged on that outcome, and an on-subject answer with no outcome is an INCOMPLETE / DANGLING DISCLOSURE. Code notices only the contradiction in Call 2's own reply (label `insufficient_evidence` + the clause's own text quoted, or confirmed as "fits") and asks Call 2 once to read it again, in the same single recheck call. Call 2's second answer stands; if it keeps `insufficient_evidence` it is logged for review (`unanswered_text_guard`). On the saved Atharv replies exactly these three units in 2021-22 are sent back; no other year changes. |
| 2021-22 (vi) "the company is under process for maintaining proper records" (cost records) came out **Favourable** – the records are not yet maintained | Call 2 is told that "under process" / "in the process of" means the company has not yet done what the question asks: a shortfall in the clause's own text ("qualified"), never "clean" and not a scope limitation. The model still decides. |
| 2022-23 (vi), same sentence, came out **Auditor Unable to Verify**. Call 2's own scope check by meaning had rejected it (the auditor never says he could not verify), but the pipeline's older safety net re-marked it because the model's *explanation* said "could not verify" | That safety net now honours the two-call flow's check: when the auditor's quote was read for a scope limitation and rejected, the model's explanation cannot bring it back. Single flow unchanged (it has no such check, so the net works as before). |
| 2022-23 (iii)(b) "terms … are, in our opinion, prima facie, not prejudicial" turned **Qualified** ("'prima facie' suggests a reservation"); the same sentence is Favourable in 2023-24 and 2024-25 | Call 2 is told that "in our opinion", "prima facie", "according to the information and explanations given to us" are the standard wording of a favourable CARO opinion, not a reservation (today's HEDGING LANGUAGE rule). |

### 7b. After the 3-company comparison (10-Oct: U27101PN1994PTC077414, U29299MH1985PTC035651, U74999PN2015PTC157583)

| Problem seen | Fix |
|---|---|
| Jsons FY 2021-22 (v) "accepted unsecured Loans as follows Name PAN …": Call 2 quoted names and amounts but skipped the PAN column. The evidence check could not find the stitched quote and withdrew the verdict → **Not Reported** | Before the records are built, a quote the evidence check would throw away is replaced by the auditor's own line it was taken from (first 40 words). No label is changed. Call 2 is also told to quote words in printed order and never stitch table cells. |
| Jsons FY 2021-22 (iii): the s.189 loan table was placed only on the clause passage of (iii); (iii)(a)(B) had no text, so its answer was withdrawn → **Not Reported** | When Call 2 answers a sub-clause with words that sit only in its own clause's passage, those passage lines (with the table printed under them, up to the next numbered point) become the sub-clause's text. The words are verified against the passage. Call 2 is told to do this too. |
| (iii)(a) loan tables graded **Qualified** because "repayment / terms / overdue not stated" | Call 2 is told that a question asking for amounts is answered by the amounts; terms, repayment and overdue are the questions of (iii)(b)-(f). The model still decides. |
| (iii)(a) totals added the previous-year column (Jsons FY 2022-23 ₹75.15 cr = 31.03.2023 + 31.03.2022 balances; the right figure is ₹44.50 cr) | Call 1 is told that only the current year's balance counts. Previous-year and during-the-year columns are `counts_toward_total: false` (FLOW VS. STOCK). |
| Jsons FY 2024-25 (ii)(b): the unit's own text says "the inventory … in the quarterly statements is not verified by us", but it was labelled **Favourable**. FY 2022-23 (i)(b): "It is not possible to us to verify all the Fixed Assets" stayed in the (i) passage, under no sub-clause | `scope_text_guard`: code only notices a could-not-verify sentence in a Favourable / Not Applicable unit's own text (or in a clause passage that no sub-clause holds) and asks Call 2 once to read it again, in the same recheck call. The second reading stands; if it is kept, the unit is logged for review. It is skipped when the quote itself already says it, because today's scope rule marks that. |
| Log line "LLM_MAX_TOKENS=35000 does not fit this request … 4096 is plenty" | Now an INFO line in plain words: "reply allowance for this call: N tokens (the model takes 65536 in total; this prompt uses ~M, so LLM_MAX_TOKENS was trimmed to fit)". It is a WARNING only below 16,000 tokens, when a long reply could be cut off. |

### 7c. Six reading slips from the same comparison (10-Oct)

| Slip | Fix (the model still decides; code sets no label) |
|---|---|
| U29299 FY 2022-23 (v): the deposits box says only "NA"; Call 2 read it as "no information" → Not Reported | Call 2 is told that a bare "NA" / "Nil" / "Not Applicable" is the auditor's answer (as `mca_table.boxes_present` already treats it). Code notices a unit whose whole text is such a short answer but is labelled "no text", and asks Call 2 once to read it again. |
| Jsons FY 2023-25 (i)(c): "Management is advised to take required actions to confirm the title deeds" graded Qualified | Call 2 is told that advice and "it is the responsibility of management" sentences added to a favourable finding leave it favourable. |
| U29299 FY 2022-23 (i)(a): "however Company needs to maintain Fixed assets register" graded Favourable | Same prompt bullet: a sentence that says the records the question asks about are not kept is a shortfall (qualified). |
| Viraj FY 2022-23 / 2023-24 (xiv): "has an internal audit system commensurate …" and "Internal Audit is not applicable … as per Section 138" | **Per the ICAI Guidance Note (Toby, 10-Oct): no blanket "Not Applicable wins".** (xiv)(a) asks whether a commensurate system EXISTS; section 138 only decides whether an internal auditor must be APPOINTED. An affirmative "commensurate system" finding is kept (Favourable) unless the rest of the answer contradicts it. Not Applicable applies only when the answer reports no system and says internal audit is not required. For the two-call flow this replaces the (xiv) example in today's DO NOT OVER-GRADE list. The single prompt is unchanged (byte-identical), so it still follows the old example. |
| Jsons FY 2023-24: "unable to monitor the use of working capital loans" put on (ix)(c) | Per the ICAI Guidance Note: (ix)(c) = term loans applied for their sanctioned purpose; (ix)(d) = short-term funds used for long-term purposes. Not being able to trace the use of working-capital borrowings is (ix)(d). Clause map ca2: "working capital" is a not-this-subject word of (ix)(c) and a subject word of (ix)(d), so code's subject hint points Call 2 to move it. The Call 2 prompt states the ICAI distinction. |
| Jsons FY 2020-21 (iv): "There are such loans … to which provisions of section 185/186 are applicable" read as confirmed compliance | Call 2 is told to judge only what the quote says: a law that APPLIES is not a law COMPLIED with. On the subject with no outcome, it is an INCOMPLETE / DANGLING DISCLOSURE. |

A replay of the three companies' real replies through this code changes no label. Labels move only when Call 2 answers differently on the next live run. The only replay differences are the ones `main` shows too (replay set-up).

Audit file per unit (`staged_trace`): `own_text_label`, `basis`, `own_question_guard`, `dropped_count_figures`;
per filing: `own_question_guard`, `denial_then_list_guard`, `unanswered_text_guard`, `scope_text_guard`, `text_settled`, `call2.recheck`,
`call1.dropped_count_figures`, `call2.salvaged`.
Offline checks: **164 / 164**.
