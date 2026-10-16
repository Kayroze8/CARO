# v6 trial: the same 6 CINs (14 Oct 2026)

**Code:** the `caro/` files in this zip (v6). The only `.env` setting to change is `CARO_LLM_FLOW=staged_nofallback`.

**CINs:** `cin-list-v6-trial.txt`. These are the same 6 CINs and 29 filings as the v5 trial, so the two runs compare directly.

**Expected time:** about 1 h 10 min, which is what these filings took on 13 Oct. If a step crawls in the last part of the run while its reply is identical to 13 Oct, that is the server, not the code.

## Pass criteria (29 filings, independent units)

Scored on `truth_14Oct_decided.csv`, which carries your 14-Oct decisions (P17 Adverse, Apollo, WickedRide):

| Measure | 13-Oct run | v5 trial | v6 expected | Pass if v6 live is |
|---|---|---|---|---|
| Reviewed truth (889 units) | 96.74% | 91.68%* | 97.64% | at least 96.74%; target 97.64% |
| CA-corrected truth (900 units) | 94.00% | 89.67%* | 94.89% | at least 94.00%; target 94.89% |
| Failed filings | 0 | 1 | 0 | 0 |
| Recheck requests identical to 13-Oct | - | no | yes | yes (I check this from the files) |
| Any single LLM call over 5 minutes | - | 1 (600 s) | 0 | 0 |
| Listed (vii)(a) arrears shown Adverse | - | - | Ichalkaranji 2021-22 / 2022-23 / 2023-24, Aditya 2021-22 / 2024-25 | all 5 |

*v5 trial on the decided truth, with the failed filing (Siddhivinayak 2024-25) counted wrong.

## Send back

Send the same five things:
- caro_results
- caro_audit
- extracted_llm_out
- extracted_text
- the api log

I will check, in this order:
1. Request identity: every request against the 13-Oct request.
2. Reply identity: every reply against the 13-Oct reply.
3. Accuracy on both truth files.
4. Every changed label, read as a CA.
5. Amounts.
6. Timing per step.
