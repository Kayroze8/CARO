# v5 trial on a smaller lot (14 Oct 2026)

**Goal:** prove in a live run that v5 is at least as accurate as the 13-Oct run before it goes to production.

## Code

Use the `caro/` files in this zip (v5). Do not change `.env` except:

```
CARO_LLM_FLOW=staged_nofallback
```

Use `staged_nofallback` for the trial so that every filing shows its own two-call result. Production uses `staged`.

## CINs

These 6 CINs, 29 filings, are in `cin-list-v5-trial.txt`. They are the companies where the 14-Oct run lost the most, so they are the hardest test:

| CIN | Company | Why it is in the lot |
|---|---|---|
| U15319DL1987PLC027698 | K.R. Foods | Printed-question / P7E / F35 cases, the fallback guard, the loop limit |
| U15422PN2011PLC138871 | Viraj Canes | P8 "going concern assumption" (xix) ×4, P7C false Adverse |
| U45200PN2011PLC138765 | Siddhivinayak | P2 (ix)(c) ×4, NA push-down, "following: Nil" |
| U35204KA1996PTC020766 | Aditya Auto | (iii) loans read as "no loans", (vii)(b) disputed dues, (i)(b) |
| U27101PN1994PTC077414 | Western Precicast | Text placed on the wrong unit, false Unable to Verify, loan-table amounts |
| U17290PN2012PLC145174 | Ichalkaranji | NPA Adverse (ix)(a), (vii)(a) arrears, (ii)(a) |

The run should take about 1 h 40 min (the 14-Oct run averaged about 3.4 min a filing).

## Pass criteria (independent units of these 29 filings)

| Measure | 13-Oct run | 14-Oct run | v5 expected | Pass if v5 live is |
|---|---|---|---|---|
| Accuracy, your reviewed truth (889 units) | 97.19% | 91.68% | 97.64% | at least 97.19% |
| Accuracy, CA-corrected truth (896 units) | 94.42% | 93.08% | 94.87% | at least 94.42% |
| Correct red flags lost vs 13-Oct | - | 5 | 0 | 0 |
| False Adverse / UTV added by a recheck note | - | 4 | 0 | 0 |

## Send back

Send the same five things as before:
- caro_results
- caro_audit
- extracted_llm_out
- extracted_text
- the api log

I will check:
1. that the requests matched 13-Oct;
2. accuracy on both truth files;
3. every changed label, read as a CA;
4. amounts;
5. red flags;
6. each note-driven change.
