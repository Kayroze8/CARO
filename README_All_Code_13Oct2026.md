# CARO pipeline – all code changed on 13 Oct 2026 (final package of the day)

Copy these complete files over the repo:

| File | What is in it |
|---|---|
| `caro/amounts.py` | All amount fixes in one file (Part A). `amount_checks.py` is gone; delete it if you installed it earlier. |
| `caro/staged_llm.py` | Call-1 amount rules; mapping / crosswalk / Option C (Part B); classification notes, prompt lines, loop limit and trial flags (Part C) |
| `caro/post_rules.py` | Option C gate (`own_clean_text`); P2, P10, P11 in the NA/clean table; P7A CSR trigger; P11 pointer rule |
| `caro/pipeline.py` | One line: the CARO 2016-layout subject guard follows the corrected crosswalk |
| `caro/audit.py` | One key added to the audit file (`own_text_kept`) |
| `tests/run_phase2_cases.py` | 213 checks: AM1–AM7 for amounts, MAP1–MAP7 for mapping, and the 11 checks of Part C |
| `tools/check_vllm_maxlength.py` | **New.** A 1-minute server check for the loop limit (P13) |
| `all_changes_13Oct2026.diff` | Every changed line, compared with the code of the 13-Oct run |

What did not change:
- the single flow (byte-identical);
- the final JSON keys (schema 4.1);
- the Remarks (no new ones);
- the `.env` switches (no new ones).

Every fix has its own on/off switch.

# Part A – Amount fixes, explained

## How an amount is worked out (two-call flow)

1. **Call 1** copies the money figures the auditor printed for each clause, exactly as printed (`amount_items`). It also marks each figure "counts toward the total: yes/no".
2. **Code** (`caro/amounts.py`) converts each figure to rupees using the unit printed with it ("Rs. in lakhs" and so on). It drops figures that cannot count and adds up the rest.
3. **Roll-up:** a main clause takes the amount of its most serious sub-clause.

The 13-Oct run had 34 of 162 amounts wrong. Each fix below removes one cause. A fix can only:
- remove a figure that cannot count;
- add a missing row of the same table column;
- read a table the auditor printed.

No fix sets a label. Each one can be turned off in `AMOUNT_SWITCHES` at the top of the amount section in `amounts.py`.

## Prompt change (Call 1, switch `AMT_PROMPT` in staged_llm.py)

The model is told which printed figures to list and which count:

- **List only the figures that answer this clause's question.** A figure printed under another clause is never this clause's amount.
- **Listed, but not counted:**
  - amounts paid or deposited under protest;
  - previous-year columns;
  - opening balances;
  - "given during the year" when the year-end balance is also printed;
  - a guarantee when a loan balance is printed for the same party;
  - "Total" rows.
- **Never listed:** limits (Rs 5 crore), areas (acre), counts, percentages, dates.
- **Tables:** the whole answering column, row by row (0.00 included), never mixing columns.
- **Clause placement of figures:**
  - (vii)(a) undisputed arrears go only on (vii)(a), and disputed dues only on (vii)(b);
  - in the (iii)(a) table, the subsidiaries / JV / associates row goes to (A), and the others / employees row to (B);
  - (ix)(a): every unpaid instalment, principal and interest, never the loan balance;
  - (ii)(b): the difference figures.

## Code fixes, one by one

| # | Switch | The problem (filing) | What the code now does | Example |
|---|---|---|---|---|
| 1 | `TABLE_COLUMNS` | A "deposited under protest" column was added to the disputed dues (Aditya 2020-21 (vii)(b)) | Reads the table header and works out which column a figure sits in. Paid / protest, opening and previous-year columns never count. | Aditya (vii)(b) → **₹293.49 lakh** (the protest column left out) |
| 2 | `TABLE_COLUMNS` (loans / deposits) | "Given during the year" was added to the closing balance (Western (v), K.R. Foods 2020-21 (iii)(b)) | In loan and deposit tables, only the closing (year-end) column counts | K.R. Foods (iii)(b) → **₹195.69 lakh** (year-end only) |
| 3 | `NOT_MONEY` | "110 acre" counted as ₹110 (Shri Dutt (i)(c)); the ₹5 crore working-capital limit shown as the (ii)(b) amount (Refit, Apollo) | Areas and the ₹5 crore threshold are never amounts | Shri Dutt (i)(c) → no amount |
| 4 | `OPENING_PROSE` | "Outstanding at the beginning of the year was …" added to the year-end figure (Garden Court (iii)(a)(A)) | An opening balance in a sentence never counts. "Includes opening balance" is kept, because there the figure *is* the year-end balance. | Garden Court (iii)(a)(A) → **₹5,769.91 lakh** |
| 5 | `BALANCE_NOT_DEFAULT` | A loan's outstanding balance shown as the default amount (Viraj Canes (ix)(a), 5 years) | On (ix)(a) / 2016 (viii), a figure introduced as a "balance" is not a default unless the sentence says overdue / not paid / delay | Viraj Canes (ix)(a) → no default amount |
| 6 | `UNDISPUTED_IN_VII_B` | Undisputed arrears printed under the auditor's "(b)" were added to the disputed dues (Shri Dutt, Apollo) | Such figures leave (vii)(b) and move to (vii)(a) when (vii)(a) is a finding with no amount | Shri Dutt 2022-23 (vii)(a) → **₹4.14 lakh**; Apollo 2020-21 (vii)(a) **₹3.61 lakh**, (vii)(b) **₹208.54 lakh** |
| 7 | `OWN_CLAUSE_ONLY` | The (v) deposits figure was taken as the (vii)(a) arrears (Ichalkaranji 2022-23) | A figure (4 or more digits) printed only in another clause's passage is dropped | Ichalkaranji (vii)(a) → no amount |
| 8 | `INTEREST_TEXT` | Interest already included in "outstanding … including interest" was added again (Ichalkaranji (ix)(a)) | The interest is not added twice, also when the amount is read from the text | Ichalkaranji 2022-23 (ix)(a) → **₹9,060.87 lakh** |
| 9 | `UNIT_CARRY` | "Rs.1009 thousand … total outstanding Rs.9563" – the second figure was read as rupees | A bare figure in the same sentence takes the unit printed just before it | Ichalkaranji 2023-24 (v) → **₹95.63 lakh** |
| 10 | `ROW_COMPLETE` | The model copied only some rows of a column | The missing cells of the same column are added. Rows are matched by value count, so a figure is never added twice. | Western 2020-21 (v) → **₹719.81 lakh** |
| 11 | `III_A_TABLE` | (iii)(a) rows put on the wrong slot (WickedRide, 4 years) | The (iii)(a) table is read by row label: subsidiaries / JV / associates go to (A), and others / employees go to (B). The **balance-outstanding row** is used, loans only; a guarantee is used only when no loan is printed. | WickedRide (A) 2021-22 to 2024-25: **2,358.39 / 2,744.41 / 2,564.91 / 2,449.91 lakh**; Shri Dutt 2023-24 (B) **4,863.70 lakh** |
| 12 | LOAN VS. GUARANTEE (approved rule) | A guarantee was added to a loan (Western 2023-24 (iii), Shri Dutt (iii)(a)(B)) | Never adds a guarantee to a funded loan balance | Western (iii) → **₹5,213.40 lakh** |
| 13 | `II_B_DIFF` | (ii)(b) quarter differences added together, or not read at all | Takes the difference column at the year-end quarter. If only "books" and "statement" are printed, uses their difference. Otherwise takes the largest figure – never a sum. | Shri Dutt (ii)(b): **20,096.09 L / 14,705 L / 2,298 L / ₹109.50 cr**; Saspack **359.08 L**; Ichalkaranji **85 L** |
| 14 | `FAV_NO_AMOUNT` | A Favourable answer carried an amount, e.g. "terms are not prejudicial" with ₹… (Garden Court (iii)(b), Refit (ii)(b)) | A Favourable answer keeps an amount only for exposure clauses: (iii), (iii)(a)(A)/(B), (iii)(f), (iv), (v) | Garden Court (iii)(b) → no amount |
| 15 | `SIBLING_DUP` | The same figure was shown on (xx)(a) and (xx)(b), or on (iii)(a)(A) and (B) | One printed figure stays on one sub-clause: (xx)(a) keeps it; for (iii)(a), the slot the auditor's text names keeps it | Viraj Alchohol, Western, Jsons, Rama (xx)(b) → none |

**Two small fixes added today, found by the tests:**
- A header line "Amount (Rs.)" is now read as a column; it was being skipped as a unit note.
- When a figure sits inside a running sentence, the checks read that sentence, not the table above it.

`EVIDENCE_SHOWS_AMOUNT` stays **off**. Showing the figure's row in the evidence needs the placement fixes of Part B to be confirmed live first.

## Not solved by the amount code (prompt / live run)

- **Viraj Alchohol 2020-21 (viii):** the model dropped the principal instalments. Prompt rule (ix)(a) / 2016 (viii) addresses this.
- **Starlite 2020-21 (vii)(b):** the disputed-dues table sits under the wrong letter. Prompt rule (vii) plus Part B address this.
- **Main-clause tie:** two sub-clauses equally serious → the first one's amount. Unchanged by design.

---

# Part B – Mapping, crosswalk and Option C (judge on source_text)

## What changed, in plain words

Each change has an on/off switch in `PATCH_SWITCHES` (staged_llm.py).

### 1. Crosswalk only where the report really uses the CARO 2016 layout (`XWALK_LAYOUT`)

**Problem:** the layout reader called a report "hybrid" when it found no CARO 2016 clause numbers and the 2016 phrase "initial public offer … and term loans" (many CARO 2020 reports copy it). So 8 filings written in the CARO 2020 layout were told "printed (iii)(a) → (iii)(b)", "printed (xiv) → (x)(b)", and so on:
- Saspack 2022-23 to 2024-25 (numbered 1–21);
- Corporate Infotech 2021-22 to 2024-25 (cites "clause 3(x)(a)", "3(xvi)(d)");
- Garden Court 2024-25.

**Fix:** a report keeps the crosswalk only if:
- it cites fewer than two CARO 2020 sub-clauses such as 3(x)(a) or 3(xvi)(d); **and**
- it does not have 8 or more CARO 2020-only subjects while showing no CARO 2016 numbering.

**Result:**
- **Real hybrids keep the crosswalk:** Western and Jsons 2021-22, Atharv 2021-22 to 2024-25.
- **CARO 2016-layout years keep it too:** Viraj Canes 2021-22 to 2024-25.
- **Mixed numbering:** reports that follow neither Order get a subject-only note instead. Prasad 2022-23 and 2024-25 print (x) public offer, (xi) fraud, (xii) s.197 and (xiii) Nidhi.
- **Checked on every filing of both runs:** only these 10 filings change, and with no label change in the replay.

### 2. Subject words for CARO 2016 years (`SUBJ_2016`)

Every CARO 2016 unit now uses the subject words of its CARO 2020 successor named in your clause map. For example, 2016 (viii) uses (ix)(a)'s words, and 2016 (ix) uses those of (x)(a) and (ix)(c). Clause (xi) managerial remuneration has no successor, so it uses the words of rule M9.

Before this, the placement checks (M10/M11) never ran for CARO 2016 filings.

### 3. Call 1 prompt: "ONE UNIT, ONE ANSWER" (`ONE_UNIT`)

1. A sub-clause's source_text holds only the sentences that answer its own question, stopping where another unit's answer starts.
2. When the auditor's letters are out of step, follow the subject. For example, a printed "(c)" about short-term funds is (ix)(d).
3. A sentence goes on one unit only. The exceptions are blanket statements and the twin markers (A)/(B).
4. A printed question with nothing after it means the source_text is "-".
5. Wording such as "except as given in Para (b) below": the referred paragraph is part of this unit's answer when it is on this unit's subject (Shri Dutt (vii)(a)).
6. The main clause still keeps its full passage.

### 4. Call 2 prompt: "WHOLE TEXT, OWN QUESTION" (`ONE_UNIT`), which is Option C

1. Read every sentence before answering "fits"; move the sentences that answer another unit.
2. **Classify on the whole text of the unit**, not on one sentence. An exception, shortfall, could-not-verify statement or listed default anywhere in it decides the label. evidence_text only shows where those words are.

### 5. Code check of the text on each unit (`F35`, recheck note – the model decides)

A unit gets **one recheck note naming both questions** in two cases:
- its own text has none of its subject words but has another unit's subject words;
- its own text is only the printed question.

The model then moves the words or keeps them and says why. Code never moves words or sets a label.

The check skips:
- bare "Not applicable" or "Nil" answers;
- blanket "has not granted any loans" statements;
- the shared clause passage.

**Measured:** 16 notes in the 13-Oct replies and 14 in the 11-Oct replies, about 0.3% of answered units. Reading them, most are real misplacements:
- Jsons 2021-22 (xvii) holds the (xix) sentence;
- Committed (iv) holds the (iii) sentence;
- Studio XP (ix)(c) holds the (ix)(d) answer;
- Prasad, Shri Dutt and K.R. Foods (iii)(e) hold the (iii)(f) answer;
- Viraj Alchohol (iii)(e) holds the (iii)(d) answer;
- Universal Contractors (xx)(b) holds the (xx)(a) answer;
- Garden Court (iii)(a)(B) holds the (A) row.

When the model moves the words, the move is applied and checked word for word, as for the existing notes.

### 6. Moves of passage text (`MOVE_PASSAGE`)

If Call 2 moves words that sit in the clause passage, the words go to the unit they answer and nothing is removed. This is the case when the sub-clause text was shown as "(same as passage)".

Example: Prasad 2023-24 (iii)(a)(A) → (iii). A move to the clause itself is logged as "already there".

### 7. Code checks follow the whole own text (`OWN_TEXT`), which is Option C

Two rules used to change the model's label from the quote alone:
- the NA/clean table;
- the over-grade guard (Qualified → Favourable).

Now, for two-call records, they change it only when the unit's **own text** agrees.

"Own text" means only the sentences of its source_text that:
- are on its own subject;
- are not on a sibling's subject;
- are not on another clause's subject.

So a contaminated text never decides. When the unit has no own text, the quote is read, as before.

### 8. Table quotes the model rewrote (`TABLE_QUOTE`)

If every figure in the quote is in the unit's own text, the quote is replaced by the auditor's own lines that hold those figures, instead of the verdict being withdrawn.

Example: WickedRide 2022-23 (iii)(a)(A). The model added "(Guarantees)" and "(Loans)" to its quote, the evidence check failed, and the unit showed Not Reported.


---

# Part C – Classification fixes and loop limit (13-Oct evening batch)

All of these apply to the two-call flow only and each has its own switch in `PATCH_SWITCHES` (`caro/staged_llm.py`).

Four groups are left for later, as you decided:
- P12 (copying a blanket statement down to its sub-clauses);
- P14 (reading the XML before OCR);
- P17 (the (vii)(a) arrears convention);
- P7 as a hard rule beyond the precise CSR trigger below.

## Trial flags (remove after the next run is checked)

`TRIAL_FIXES = {"P1", "F35", "P16", "P7A", "P7B", "P7C", "P7D", "P7E"}`. F35 is the P3 check.

- **What the flag does:** every unit one of these fixes touched carries the review reason `trial:<fix>`. That is an existing review value, so the unit shows as flagged for review. No new JSON key is added.
- **How to remove it:** take a fix out of the set once the run confirms it, or empty the set to remove every flag.

## What each fix does

| Switch | Register problem (filing) | What changed | Code may set a label? |
|---|---|---|---|
| **P1** (trial) | Starlite 2020-21: (vii)(b) holds only the arrears sentence; the disputed-dues table sits under the auditor's "(c)". (vii)(b) showed Favourable and the red flag was lost. | When (vii)(b)'s own text has no dispute words, the code looks in the (vii) paragraph for a dispute sentence or table that no sub-clause holds. Denials such as "no dues … on account of any dispute" never count. It quotes that text in **one recheck note**. If the model moves the words, the move is checked word for word. | No |
| **P3 / F35** (trial) | Wrong-subject text on a unit (17 units) | Built this morning: one note naming both questions. "Only a pointer to the audit report" is now also noticed (P11). | No |
| **P16** (trial) | Shri Dutt (vii)(a): "except as given in Para(b) below" | The note quotes the paragraph it points to, **from the same clause only**, and asks the model to judge with it. | No |
| **P7A** (trial) | Universal Contractors 2021-22 / 2022-23 (xx): "s.135 applicable but has not been complied with" showed Qualified (CA: Adverse) | A precise adverse trigger: "135 … has not been complied" / "not complied with … 135". It does not fire on "no unspent", "not applicable" or "duly complied". A search of both runs' texts found it only in these 2 filings. | Yes (adverse trigger, review flag `trial:P7A`) |
| **P7B** (trial) | Particulars announced but not given | Note when a line ends "as under / as follows / the following:" and fewer than 6 words follow (Prasad 2020-21 (v): "compliance … is as follows" and nothing after it) | No |
| **P7C** (trial) | Arrears over 6 months listed under (vii)(a) | Note only when the arrears sentence with figures is in (vii)(a)'s own text **and** the existing arrears trigger cannot already read it | No |
| **P7D** (trial) | Internal audit needed but not carried out | Note when (xiv)(a)'s text says the internal audit was not carried out and does not say it is not required | No |
| **P7E** (trial) | The model's own explanation calls the answer incomplete, but the label is "insufficient" | Note quoting the rule: "insufficient" means no answer at all; an incomplete answer is qualified | No |
| **P2** | WickedRide 2024-25 (xi)(b)/(c): "no fraud … Hence 3(xi)(a)–(c) not applicable" showed NA (CA: Favourable) | (a) Prompt line: the (ix)(e)/(f) NA rule is **only** for (ix)(e)/(f). (b) The NA/clean table reads the unit's own text, or the clause's statement, for a favourable finding, for (ix)(a)–(d), (xi), (xiv)(a) and (xv). (c) Note for (ix)(c) and (xiv)(a). | Yes, the existing NA/clean table only, never on "does not have / not availed" |
| **P4** | (iii)(a) qualified for terms or percentages | Prompt line: (iii)(a) asks only the amounts. If no amount is given at all, the answer is incomplete. | No |
| **P5** | The recheck changed a unit it wasn't asked about | **Log only**. The replay showed that blocking it would lose a correct answer (Jsons 2023-24 (iii)(a)(B), CA Favourable). It now writes a review trace instead. | No |
| **P6** | F16 false alarm | The note now says "move ONLY the words that name subsidiaries; keep (B)'s own denial". The 90-character look-back is **unchanged**: in the replay a wider one removed a correct note (Shri Dutt 2024-25 (B)). | No |
| **P8** | Explanations that guess ("implying", "presumably", "by default") | Note on Favourable / NA units only | No |
| **P10** | NA taken from a carve-out or from another sub-clause's sentence | The NA/clean table does not turn Favourable into NA when the matching sentence has "except … below / following", starts with another sub-clause's letter, or is about "repayable on demand" for (iii)(b)–(e). | Prevents a change only |
| **P11** | Wordings the rules missed | NA: "has not any intangible assets"; NA for (iv): "not given any loans to directors or any other person in whom the director is interested". Unable to Verify: "not provided by the management … unable to say". Not Available: a box holding only "As mentioned in the Audit report". | Yes (existing rules, wider wording) |
| **P13** | K.R. Foods 2021-22 loop (171,603 characters) | Call-1 `source_text` limit = **6,000 characters** (your decision, checked with your script; the longest real field in both runs is 5,234), sent as `maxLength` in the guided-JSON schema. A field at the limit that repeats is cut at the first repeated sentence. A long field that does not repeat is asked once more at **7,200** (+20%), then kept and logged. **2 attempts at most.** | No |
| **P15, P18** | (viii) convention; rights issue / allotment with no s.42/62 statement | Prompt lines | No |

### Before the live run

Run `python tools/check_vllm_maxlength.py` once.
- **PASS:** your vLLM enforces the limit.
- **FAIL:** your vLLM ignores it. P13 then simply has no effect; nothing breaks, and the old cut-off salvage still works.

## Proof (replay of the saved 13-Oct and 11-Oct replies)

| Check | Result |
|---|---|
| Tests | **213/213** (test G1 now allows the one P13 re-ask) |
| Single flow | Byte-identical |
| Final JSON keys | Unchanged. Test G2 checks this, and all new marks are internal or audit-only (`own_text_kept` added to the audit file). |
| 13-Oct replay: label changes | 16, all to the CA / review label except one that stays wrong (see below) |
| 11-Oct replay: label changes | 9, same |
| Amount changes | 0 |

The 16 changes in the 13-Oct replay:
- **Universal Contractors:**
  - (xx), (xx)(a), (xx)(b): Qualified → **Adverse** (CA key Adverse);
  - (i)(a)(B), 3 years: Favourable → **NA** (CA key NA).
- **Sunstar (iv)**, 2 years: Favourable → **NA** (CA key NA).
- **Siddhivinayak (ix)(c)**, 4 years: NA → **Favourable** (review: Favourable).
- **WickedRide:**
  - 2021-22 (xiv)(a): NA → **Favourable**;
  - 2024-25 (xi)(b) and (xi)(c): NA → **Favourable** (all CA key Favourable).
- **Starlite 2024-25 (iii)(c):** NA → Favourable. The review says Not Reported: the text Call 1 placed there is really the (iii)(f) answer, so both labels are wrong. This is a placement problem; the new Call-1 "ONE UNIT" rule addresses it in the live run.

In the first draft, the replay showed 4 regressions, which I **removed before packaging**:
- Jsons 2023-24 (iii)(a)(B), caused by P5 blocking;
- Shri Dutt 2024-25 (A)/(B), caused by P6's wider look-back;
- Apollo 2023-24 (x)(b) and Atharv (iv), caused by the P2 reading applied to every clause and reading the printed question.

All 4 are back to their earlier labels.

### Notes per run (each joins the one existing recheck call)

| Note | 13-Oct replies | 11-Oct replies |
|---|---|---|
| F35 | 18 | 14 |
| P8 | 19 | 27 |
| P7C | 9 | 11 |
| P2 | 11 | 5 |
| P7E | 6 | 8 |
| P7B | 3 | 3 |
| P1 | 3 | 2 |
| P16 | 1 | 0 |

Filings with a recheck call (counting the 12/13-Oct notes): 60 → 77 of 138 on the 13-Oct replies, 64 → 84 on the 11-Oct replies. That is about 20 more recheck calls per 138 filings; other checks already send some of these filings to a recheck.

**Limit of the replay:** it re-uses the saved model replies, so it cannot show what the model does with the new prompt lines and notes. That is what the live run will show.

---

# Part D – Live run, what to send back, rollback

## Before the run
1. Copy the files.
2. Run `python tools/check_vllm_maxlength.py` and note PASS or FAIL.
   (Your own script settled the limit at 6,000 characters; this check only confirms that the server enforces it.)
3. Run `python tests/run_phase2_cases.py`. Expect **213/213**.
4. Run with `CARO_LLM_FLOW=staged_nofallback`.

## CINs to run

| CIN | Company | Check |
|---|---|---|
| U45204UP2009PLC132673 | Universal Contractors | (xx) Adverse, review reason `trial:P7A`; (i)(a)(B) NA; (xx)(a)/(b) placement |
| U15100WB2012PTC184651 | Shri Dutt | (vii)(a) "Para (b)" note (P16) → arrears on (vii)(a); (ii)(b), (iii)(a)(B) amounts; (iii)(a) A/B |
| L17110TG1962PLC000915 | Starlite | 2020-21 (vii)(b) P1 note → disputed APGST 24.27 lakh; 2024-25 (iii)(c) placement |
| U74140KA2014PTC076210 | WickedRide | (xi)(b)/(c) Favourable; (iii)(a)(A) amounts; (xiv)(a) |
| U45200PN2011PLC138765 | Siddhivinayak | (ix)(c) Favourable; (iv) Favourable; (vii)(b) P1 notes |
| U01110PN2016PTC166701 | Sunstar | (iv) NA (P11) |
| U74999PN2015PTC157583 / U15511PN2002PLC016762 | Viraj Refinery / Viraj Alchohol | (xiv)(a) P2; (iii)(e); amounts |
| U15421PN2005PLC021744 | Prasad | (v) P7B note; numbering note; (vii)(a) P7C |
| U17290PN2012PLC145174 | Ichalkaranji | (vii)(a) P7C; amounts |
| U15319DL1987PLC027698 | K.R. Foods | **2021-22 loop (P13)**: time and the `p13_runaway_cut` entry in the audit; (iii)(e)/(f) |
| U29299MH1985PTC035651 | Jsons | (iii)(a)(B) 2023-24 stays Favourable; (xvii) 2021-22; hybrid crosswalk |
| U27101PN1994PTC077414 | Western | 2023-24 (iii)(a)(B); hybrid control |
| U74899DL1994PLC061080 | Apollo | (x)(b) 2023-24 stays NA; (vii) amounts; P1 2020-21 |
| U74900PN2011PTC138795 | Atharv | (iv) stays Qualified; hybrid control |
| U21000PN2017PTC169319 | Saspack | No crosswalk; (ii)(b) amount |
| U72900DL2007PTC162708 | Corporate Infotech | No crosswalk |
| U15510MH1988PTC045769 | Garden Court | No crosswalk (2024-25); (ix)(c)/(d); amounts |
| U74900DL2011PTC227729 | Studio XP | (ix)(c)/(d)/(e) placement |
| U51909DL2004PTC127807 | Committed Ispat | (iv) placement; (ii)(b) amounts |
| U26305DL2012PTC240966 / U52100DL2012PTC240966 | Refit | (x)(b), (xiv); (ii)(b) no ₹5 crore |
| U45309PN2022PTC217191 | Sahyadri | 2024-25 (x)(b) "As mentioned in the Audit report" → Not Available in Source |
| U15422PN2011PLC138651 | Shripati | (iv) |
| U35204KA1996PTC020766 | Aditya | Amounts (vii)(b) |
| L36911KA1995PLC017077 | Rajesh Exports | (iii)(a)(A) ₹28.33 lakh |
| U15422PN2011PLC138871 | Viraj Canes | CARO 2016-layout crosswalk kept; (ix)(a) amounts |

## Send back
- `caro_results` JSON
- `caro_audit.zip`
- `extracted_llm_out.zip`
- `extracted_text.zip`
- the api / run log
- the output of `tools/check_vllm_maxlength.py`

## What I will check on that run (errors caused by today's fixes)
1. **Every unit with a `trial:<fix>` review reason:** was the note right, and was the model's new label right on the auditor's text?
2. **Any label that differs from the 13-Oct run on a unit no fix touched.** This catches side effects of the new prompt lines.
3. **Red flags:** none lost.
4. **Amounts:** each listed CIN against the CA values in Part A.
5. **Moves:** each "placed from the clause passage" or "moved" entry; no correct text lost from a unit.
6. **The P5 trace** ("label changed by the recheck although no words reached the unit"): is the recheck answer right?
7. **P13:** runaway fields cut, long fields asked again, time per filing.
8. **JSON keys and the single flow:** unchanged.

## Rollback
- **Amounts:** `AMOUNT_SWITCHES` in `caro/amounts.py`; `PATCH_SWITCHES["AMT_PROMPT"]`.
- **Mapping / Option C:** `XWALK_LAYOUT`, `SUBJ_2016`, `ONE_UNIT`, `F35`, `MOVE_PASSAGE`, `OWN_TEXT`, `TABLE_QUOTE`.
- **Classification batch:** `P1`, `P2`, `P4`, `P5`, `P6`, `P7A`–`P7E`, `P8`, `P10`, `P11`, `P13`, `P15`, `P16`, `P18`.
- **Trial flags:** remove entries from `TRIAL_FIXES`.


---

# Part E – P20: truth table updated with the 13-Oct review (scoring only, no pipeline code)

Files are in the folder `P20_truth_update/` of this package (copy `batch/rerun_check/` into your `batch/rerun_check/`).

| File | Role |
|---|---|
| `build_truth_reviewed.py` | Builds the new truth file from `truth_13Oct.csv` and the review workbook. Rerunnable. |
| `truth_13Oct_reviewed.csv` | The same 7,380 rows, plus `truth_prev`, `truth_source_prev`, `review_label` and `review_status` |
| `rerun_verdict.py` | Uses the new file. It shows accuracy on the reviewed truth **and** on the previous truth. |
| `CARO_Truth_Update_P20_13Oct2026.xlsx` | Every changed entry with the auditor's text and the reason, for your sign-off |

What the update does:
- **124 truth entries changed.** They are marked `13-Oct review (pending CA sign-off)`.
- **8 kept, waiting for a decision:**
  - P17, the (vii)(a) convention: 7;
  - Apollo 2020-21 (vii)(b): 1.
- **14 kept, flagged for a CA read.**
- **An existing CA-review truth is never overwritten.**

The 13-Oct run, re-scored:

| Truth used | Units | Old single | 11-Oct | 13-Oct run |
|---|---|---|---|---|
| Reviewed truth | 4,200 | 93.40% | 93.69% | **96.90%** |
| Previous truth | 4,123 | 96.24% | 96.48% | **98.23%** |

The previous truth was more lenient because it copied old mistakes ("both flows agree").
