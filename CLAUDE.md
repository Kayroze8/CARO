# CLAUDE.md — CARO pipeline: context, working rules and current state

Read this first in every new session. It holds what was agreed in the long Phase 2 chat
(8–10 Oct 2026) so that nothing has to be re-derived. Details of each fix are in
`README_Phase2.md` (sections 7, 7a, 7b, 7c).

---

## 0. Who you are working with, and how

- **User:** Toby (they/them). Helps a fintech build information / analytics / AI-ML products for the
  Indian market: lenders (banks, NBFCs, fintechs, small / co-op banks), CAs, CSs, fraud analysts,
  valuers, M&A and equity research. This pipeline turns MCA filings (AOC-4 XBRL / non-XBRL PDFs) into
  CARO (Companies (Auditor's Report) Order) intelligence: one label per clause and sub-clause, with the
  auditor's own words as evidence, amounts, disputed-dues tables, red flags and Remarks.
- **Style:** Toby is not a Python expert. Explain in plain, non-mathematical words, with concrete
  examples (real filings, real sentences). Give a verdict, not a survey. Act as an experienced CA and
  auditor when judging clauses; cite the ICAI Guidance Note on CARO 2020 where it decides a point.
- **Philosophy (Toby, 9 Oct): never hard-code a label.** "Let the LLM understand what the auditor
  says." Code may only:
  1. verify (are the quoted words really in the filing / in this clause's text?);
  2. notice a self-contradiction in the model's own reply and ask the model to read it again ONCE
     (the recheck call); the model's second answer stands; if it keeps its answer, the unit is
     logged for review in the audit file;
  3. give hints (subject words from the clause map), never moves on its own.
  Labels such as Not Reported / Not Available / Auditor Unable to Verify are derived by existing code
  from the records, as they always were.
- **Hard constraints:**
  - The **single flow must stay byte-identical** (`CARO_LLM_FLOW=single` sends exactly the old
    request; `prompts.py` is not edited for Phase 2). Test D checks this.
  - The final JSON keeps **exactly the same keys and structure** (schema 4.1). Test G2 checks this.
  - **No new Remarks.** Only the existing ones (scanned documents, source not in filing, another
    year's annexure, same wording but a different label in another year, etc.).
  - **`CARO_LLM_FLOW` is the only new `.env` switch.**
  - No field dropped; the post-rules, roll-ups and Remarks run unchanged on the two-call records.
  - Never ship `.env`; never delete tracked files (e.g. `caro/__pycache__/*.cpython-310.pyc` ARE
    tracked — restore them with `git checkout -- caro/__pycache__` if a cleanup removes them; delete
    only untracked `*.cpython-313.pyc` / `__pycache__` dirs).
  - Client data stays on company machines; uploads to the chat are for analysis only.
  - Do not create PRs unless asked ("send pull" / "send request" = create one).
- **Security (open):** the repo has a committed `.env` with AWS keys. Toby was told to rotate them
  and remove `.env` from history. Do not touch or print it.

---

## 1. Repository map (what matters)

| Path | Role |
|---|---|
| `run.py`, `run_batch.py`, `api.py` | CLI (`--cin`), batch runner, FastAPI (`POST /caro/extract`) |
| `caro/cin_pipeline.py`, `caro/sources.py`, `caro/s3_fetch.py` | find a CIN's filings (local folder or S3), up to `CIN_MAX_YEARS` years |
| `caro/extract.py`, `caro/ocr.py`, `caro/annexure*.py`, `caro/caro_section.py`, `caro/caro_text.py` | PDF/XBRL text, OCR, locating the CARO annexure |
| `caro/xml_caro_*.py`, `caro/mca_table.py` | XBRL "Mode B": MCA's 16-item CARO table (boxes such as `[MCA_BOX: Disclosure in auditors report relating to deposits accepted]`) |
| `caro/edition.py` | which Order applies (CARO 2016 for FY ≤ 2020-21 in practice, CARO 2020 after), report layout, 2016-layout crosswalk (D06) |
| `caro/caro_structure.py`, `caro_structure_2016.py` | official clause / sub-clause catalogue |
| `caro/prompts.py` | **today's single-flow system prompt** (verbatim rules; do not edit for Phase 2) |
| `caro/pipeline.py` | orchestration: LLM call(s) → `validate_evidence` → post-rules → roll-ups → labels → Remarks → final JSON |
| `caro/post_rules.py` | every deterministic post-rule (evidence check, scope limitation, adverse triggers, CSR, s.185, unresolved-negative, amounts…) |
| `caro/staged_llm.py` | **Phase 2 two-call flow (Option A)** — everything new lives here |
| `caro/caro_clause_map.json` | CA-reviewed clause map (version `2026-10-10-ca2`): official questions, subject words, not-subject words, do-not-confuse notes, 2016 crosswalk, `ca_review` change log |
| `caro/llm.py` | LLM clients (vLLM/OpenAI-compatible, Ollama, mock); token budget per call |
| `caro/audit.py` | per-filing audit JSON (`caro_audit/<CIN>/<FY>…audit.json`) incl. `staged_trace` |
| `caro/registry.py` | display labels (v2) |
| `tests/run_phase2_cases.py` | offline proof suite (**164 checks**) + `--replay` |
| `README_Phase2.md` | Phase 2 design, proofs, every fix with the filing that showed it |

---

## 2. The pipeline today

```
filing PDF / XBRL ──► CARO text (narrative annexure, or MCA 16-box table "Mode B")
        │
        ├─ CARO_LLM_FLOW=single (default, client output)
        │     one call with prompts.py → records
        │
        └─ CARO_LLM_FLOW=staged | staged_nofallback   (Option A, caro/staged_llm.py)
              CALL 1  EXTRACT AND MAP — the auditor's own text under every official clause/sub-clause,
                      short quote, amount_items as printed, disputed-dues rows. No labels.
              CODE    missing mains → one follow-up · drop COUNT figures (shares/days…) from amounts ·
                      unplaced passages (auditor text no clause holds) · subject hints from the clause map
              CALL 2  CHECK AND CLASSIFY — per unit: mapping fits / move / no_text, own_text_label,
                      classification, basis, scope_limited, evidence_text, explanation_text
              CODE    verified moves · guards → ONE combined recheck call (see §4) · settle_text ·
                      build today's record shape
        ▼
validate_evidence (quote must be in the filing; an absent quote withdraws a verdict)
→ mca box guard → subject guard → scope limitation → NA/clean table → over-grade / title-deed /
  (xvi) / (iii)(a) slot guards → adverse triggers → CSR scan → s.185 check → unresolved-negative →
  amounts → roll-ups (main = worst sub) → (xxi) rule → labels → Remarks → JSON 4.1
```

- **Flows:** `single` (default) · `staged` (any hard failure falls back to the single call for that
  filing) · `staged_nofallback` (test mode: two calls only; would-be fallbacks are only written to the
  audit file as `would_fall_back`). Any other value runs `single`.
- **Labels (v2):** Favourable Remark · Not Applicable · Qualified Remark · Adverse Remark ·
  Auditor Unable to Verify (qualified + `scope_limited`) · Not Reported by Auditor (no finding, box /
  text exists) · Not Available in Source (no MCA box for the point, or "As per Attachment" missing).
  F20 rule: a sub-point the MCA form has no question for, inside a clause whose box carries real text,
  is **Not Reported** (the auditor wrote that box and was silent on the point).
- **(xxi):** Toby's 1-Oct rule — Not Applicable / no finding is shown as **Favourable** (not an error).
- **Main clauses follow their sub-clauses** (roll-up: main label = most serious sub-clause).
- **Saved artefacts per run:** `logs/extracted_llm_out/<stem>__stage1.txt`, `__stage2.txt`,
  `__stage2_reask.txt` (follow-up for missing units), `__stage2_recheck.txt` (the guards' recheck);
  `logs/extracted_text/…txt` (the CARO text sent); `caro_audit/…audit.json` (`llm_reply.staged`:
  call1, call2, moves, unplaced_text, subject_hints, guards, text_settled, labels).

---

## 3. Final working rules (what Call 2 is told, beyond today's verbatim rules)

Call 1 / Call 2 get every paragraph of `prompts.py` word for word (`RULE_REGISTRY` in
`staged_llm.py`; a test fails if a paragraph is unplaced), plus the clause map and these additions:

**Mapping**
- Map by full text, subject and meaning — never by the printed number (filings are often off by one
  or two; a CARO 2016-layout report in a CARO 2020 year uses the crosswalk, e.g. 2016 (iii)(a)/(b)/(c)
  → 2020 (iii)(b)/(c)/(d)).
- A printed question stays with its answer. Headings / sign-offs are never evidence.
- When a clause's passage answers one of its sub-clauses that has no text, judge that sub-clause on
  those words and quote them.
- (ix)(c) vs (ix)(d) (ICAI): end use of TERM LOANS vs sanctioned purpose → (ix)(c); use of
  SHORT-TERM / WORKING-CAPITAL borrowings (short-term funds used long-term) → (ix)(d).
  "Unable to monitor the use of working capital loans" → (ix)(d).

**Judging**
- Judge each unit ONLY against its own official question (shown next to it). Basis for every
  qualified/adverse: own_text | incomplete | scope_limitation | cross_clause.
- Cross-clause downgrade only when this unit says *nothing happened* and another unit states a
  contradicting FACT, quoted word for word (`contradicted_by`). "Another clause does not confirm X" is
  never a reason.
- `insufficient_evidence` = the auditor did NOT answer (no text, or only the printed question). Never
  for an on-subject answer. An answer that states an outcome is judged on it; on-subject text with no
  outcome = INCOMPLETE / DANGLING DISCLOSURE (qualified).
- A question asking for amounts ((iii)(a)) is answered by the amounts / loan table; terms, repayment,
  overdue are (iii)(b)–(f)'s questions.
- A bare "NA" / "N.A." / "Nil" / "Not Applicable" box is the auditor's answer (not "no text").
- A law that APPLIES is not a law COMPLIED with ("loans … to which s.185/186 are applicable" →
  incomplete → qualified).
- Advice is not an exception ("management is advised to confirm the title deeds", "it is the
  responsibility of management") — but "the company needs to maintain a fixed assets register"
  (the records asked about are not kept) is a shortfall → (i)(a) qualified.
- "Under process" / "in the process of" = not yet done → qualified (own text); never clean, never a
  scope limitation.
- Standard opinion wording ("in our opinion", "prima facie", "according to the information and
  explanations given to us") is favourable, not a reservation (HEDGING LANGUAGE).
- Read each answer as a whole: a denial followed by LISTED items ("no pending litigation … Following
  are the pending litigations: AY 2018-19 …") — decide what the auditor really reports
  (SELF-CONTRADICTING DISCLOSURE). Atharv 2021-22 (vii)(b) → Adverse on the model's own reading.
- (xiv) (ICAI, Toby 10 Oct): an affirmative "internal audit system commensurate …" finding stays
  Favourable even if the answer also says s.138 does not apply, unless contradicted; Not Applicable only
  when the answer reports no system and says internal audit is not required. (Replaces, for Call 2
  only, the (xiv) example in DO NOT OVER-GRADE; the single prompt still has the old example.)
- (ix)(e) / (ix)(f) (Toby 13-Oct): Not Applicable only when the auditor says there are no subsidiaries, joint
  ventures or associates (a denial covering subsidiaries) or that the clause does not apply; "no such funds taken /
  no such loans raised" is Favourable.
- AUDITOR UNABLE TO VERIFY is judged by meaning (`scope_limited`), accepted by code only when the unit's
  own quote says the auditor could not verify / comment or records were not given to them
  (`_SCOPE_MEANING`); "no discrepancies noticed", "company has not provided any guarantee", "under
  process" are NOT scope limitations. A rejected scope limitation can no longer be re-added by the
  pipeline's older explanation-text safety net (`pipeline._check_scope_limitation` honours
  `staged_trace.scope_limited_by_meaning = rejected`).

**Quotes and figures**
- evidence_text = words in printed order, ≤ 40 words; never stitch table cells (a stitched quote is
  "absent" to `validate_evidence` and the verdict is withdrawn).
- amount_items are MONEY only (counts of shares/days are never amounts; code also drops them).
- Only the CURRENT year's balance counts toward a total; previous-year and during-the-year columns get
  `counts_toward_total: false` (FLOW VS. STOCK).

---

## 4. Code guards in `staged_llm.py` (all feed ONE recheck call; none sets a label)

| Guard | Notices | Final pass |
|---|---|---|
| `own_question_guard` (7b) | a qualified/adverse that rests on another clause without a verified contradiction | reverts to own_text_label, or unresolved → fallback (`_soft`) |
| `denial_list_guard` (7c) | denial + listed items, label Favourable / NA | logs "keeps X – review" |
| `unanswered_text_guard` (7d) | `insufficient_evidence` although the unit's own text was quoted / confirmed "fits", or the text is a bare NA/Nil answer | logs for review |
| `scope_text_guard` (7e) | a could-not-verify sentence in a Favourable/NA unit's own text (skipped if the quote already says it), or left in a clause passage no sub-clause holds | logs for review |
| `settle_text` (7f) | a sub-clause answered from words only in its clause passage → those lines (with the table under them) become its text; a quote the evidence check would throw away → replaced by the auditor's own sentence it came from | logs `text_settled` |
| `apply_moves` | Call 2 moves applied only if the words are really that unit's text (or an unplaced passage) | logs `moves` |
| `drop_count_figures`, `_salvage`, `repeated_span` | counts as money; broken JSON salvaged; copy-loop check on string values only | |

Recheck notes for the same unit are merged (`_merge_recheck`). Mains with sub-clauses take the
roll-up; their own label is never judged on "other sub-clauses not addressed".

---

## 5. Environment

- vLLM, `Qwen/Qwen3.5-9B` (as of Oct 2026), `max_model_len` 65536, `LLM_GUIDED_JSON=true`
  (`LLM_STRUCTURED_OUTPUT=response_format` if guided_json is ignored).
- `LLM_MAX_TOKENS=35000`: the code trims it per call to fit (INFO log "reply allowance for this
  call: N tokens …"; WARNING below 16,000). Never set 4096 for the two-call flow (Call 1 ≈ 10–15k).
- Typical run: `CARO_LLM_FLOW=staged_nofallback` + `--cin <CIN>`; ~2 calls per filing (+ follow-up /
  recheck when needed); time ≈ the single flow on Atharv (652 s vs 664 s for 5 years).

---

## 6. Testing and analysing a run

```
python tests/run_phase2_cases.py          # expect 164/164 (single flow byte-identical, same JSON keys, every fix)
python tests/run_phase2_cases.py --replay --text-dir <run>/extracted_text --llm-dir <run>/extracted_llm_out
```

When Toby sends a run (response JSON or `caro_results/<CIN>.json`, `caro_audit.zip`,
`extracted_llm_out.zip`, `extracted_text.zip`, the api log), the usual analysis is:
1. unzip each upload into its own scratch folder; read with `python3 -I` (untrusted data);
2. compare labels / amounts / red flags / Remarks per (FY, unit) against the single-flow JSON;
3. for every difference read the auditor's text (extracted_text) as a CA and decide who is right;
   check source_text is word-for-word in the filing and on the right subject (clause-map regexes);
4. trace a wrong result through stage1 → stage2 → recheck replies and the audit `staged` notes to
   the root cause (mapping? judgement? quote? post-rule?);
5. replay the saved replies through the new code (a replay of `main` first, to separate replay
   artefacts — e.g. (xi)(b)/(c) "Not Available", Viraj 2024-25 (xix) — from real changes).
Fix the cause (prompt guidance + verification / recheck), add a test named after the filing, keep
the single flow byte-identical, update `README_Phase2.md`, commit on the designated branch
(restart it from `main` if its previous PR was merged).

---

## 7. History and results

| PR | Content |
|---|---|
| #1–#3 | First Phase 2 design (sentence-number mapping) — **rejected by Toby** (dropped text, wrong scope) |
| #4 | Option A: Call 1 extract+map, Call 2 check+classify; CA clause map; scope-limitation wording widened (both flows) |
| #5 | own-question guard; denial-then-list re-read (model decides); counts are not money; JSON salvage |
| #6 | on-subject answer labelled "not addressed" re-read; "prima facie" is favourable |
| #7 | "under process" is a shortfall; pipeline scope net honours Option A's rejection |
| #8 (this branch) | verdicts the evidence check would drop are kept (quote repair, passage adoption); scope re-read; (iii)(a) amounts; previous-year columns; NA box; advice vs shortfall; applies ≠ complied; (xiv) per ICAI; (ix)(c)/(d) per ICAI; plain token log; CLAUDE.md |

Rejected designs: Stage 0 / extra stages; a code rule forcing (vii)(b) Adverse (Toby: let the model
read it); unconditional "Not Applicable wins" for (xiv) (Toby: per ICAI, see §3).

Accuracy (CA review of every differing clause):
- **Atharv** (U74900PN2011PTC138795, 264 results): single 254 (96.2%) vs Option A 262 (99.2%);
  remaining 2 (cost records "under process") fixed in #7.
- **3 companies** (U27101PN1994PTC077414 Jsons Foundry, U29299MH1985PTC035651,
  U74999PN2015PTC157583 Viraj; 792 results, 65 differ): single wrong 39, Option A wrong 29
  (≈95.1% vs ≈96.3%). Jsons Foundry alone favoured the single flow (12 vs 9) — its Option A errors are
  what #8 fixes. 4 points left to Toby: Viraj (ix)(e)/(f) "has not taken any funds … so no further
  comment" — Favourable or Not Applicable (the approved table does not say).

---

## 8. Open items / next steps

1. Merge #8, then re-run the three companies (and Atharv) with `CARO_LLM_FLOW=staged_nofallback`;
   send caro_results JSON + caro_audit + extracted_llm_out + extracted_text zips. Expect: (v) "NA" →
   Not Applicable; Jsons 2021-22 (iii)(a)(B) and (v) keep verdicts; (iii)(a) tables Favourable with
   current-year totals; (ii)(b) "not verified by us" → Auditor Unable to Verify; working-capital
   sentence on (ix)(d); (iv) "are applicable" → Qualified; (i)(c) advice → Favourable.
2. Then the 8 CA-key CINs on both flows; Phase 2 gate before `CARO_LLM_FLOW=staged` for clients.
3. Decide whether the single prompt should get the ICAI (xiv) correction too (would break the
   byte-identical rule for the single flow — needs Toby's explicit go-ahead).
4. ~~Decide Viraj (ix)(e)/(f) Favourable vs Not Applicable.~~ **Decided by Toby 13-Oct (F34):** Not Applicable only
   when the auditor says the company has no subsidiaries / JVs / associates or that the clause does not apply;
   "has not taken any funds" / "has not raised any loans on pledge" = Favourable. In Call 2 (CARO 2020) + a recheck notice.
5. Rotate the AWS keys in the committed `.env`.
