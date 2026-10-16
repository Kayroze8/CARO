# v10, production Step 1: inputs, outputs, logs and retry (15 Oct 2026)

This pack is **cumulative**: it contains v9 (Fixes 1–4, two-call flow only) plus Step 1. Copy every file over the repository, keeping the same paths. The v9 README is included.

**No label logic changed in Step 1.** On a replay of the 158 saved filings, v9 and v10 give identical results on all 8,508 units: labels, amounts, red flags, review flags, texts and explanations.

## What Step 1 does

### 1. Filings are read from the server folder, not S3
Production setting:

```
CIN_SOURCE=local
CIN_FOLDER_PATH=/path/to/mca_data
```

`CIN_FOLDER_PATH` is the folder that contains the CIN folders, laid out as `<CIN_FOLDER_PATH>/<CIN>/Annual Returns and Balance Sheet eForms/...`. Files are read in place; nothing in that folder is copied, moved or deleted.

Changes in this step:
- **Missing source is now an error.** If neither `CIN_SOURCE` nor `CIN_FOLDER_PATH` is set, the run stops with a clear message. It no longer quietly tries S3; S3 is used only with `CIN_SOURCE=s3`.
- **`boto3` is loaded only for S3.** The server needs neither the AWS package nor AWS keys.
- **The CIN-folder search is limited to 3 levels** below `CIN_FOLDER_PATH` (`CIN_SEARCH_DEPTH`). Before, it searched the whole storage tree, which is slow on large storage.
- **The debug block printed on screen for every CIN has been removed.**

### 2. Optional output folders: all OFF by default
The API log and the result JSON are always written. Everything else is off unless you switch it on:

| Switch | Folder |
|---|---|
| `CARO_SAVE_XML_OUTPUT` | `xml_caro_output/` (a temporary copy is still used during the run and deleted afterwards) |
| `CARO_SAVE_EXTRACTED_TEXT` | `logs/extracted_text/` |
| `CARO_SAVE_LLM_REPLIES` | `logs/extracted_llm_out/` (needed for a replay) |
| `CARO_SAVE_AUDIT` | `logs/caro_audit/` |
| `CARO_SAVE_PERF` | `logs/perf/` |
| `CARO_SAVE_FY_TRACKING` | `fy_tracking.csv` |
| `CARO_SAVE_CIN_LOGS` | `logs/cin/` |
| `CARO_SAVE_ALL=true` | Every folder above. Use this for a local rerun when investigating a problem |

`CARO_WRITE_FILES=false` still turns all of them off.

### 3. Short logs (`LOG_STYLE=summary`, the default)
**Nothing from the extracted document is printed any more.** Three debug prints were removed:
- the whole CARO annexure text printed for every filing;
- the order year;
- the "Filings found" list.

In summary mode, the terminal and the API log show only the per-CIN block and errors. Warnings are counted and shown as a number. Example:

```
[CIN] U51909DL2004PTC127807 | source=local
  company: COMMITTED ISPAT PRIVATE LIMITED | filings: 5
  FY 2024-25  CARO 2020  OK      ADV 0  QUAL 2  UTV 0  NR 3  review 4
  FY 2023-24  CARO 2020  OK      ADV 1  QUAL 3  UTV 0  NR 2  review 6
  FY 2022-23  CARO 2020  OK      ADV 2  QUAL 2  UTV 0  NR 2  review 5
  FY 2021-22  CARO 2020  OK      ADV 1  QUAL 4  UTV 0  NR 1  review 6
  FY 2020-21  CARO 2016  OK      ADV 0  QUAL 3  UTV 0  NR 4  review 4
  red flags: 2023-24: (i) | 2022-23: (vii) | 2021-22: (vii)
  Result: SUCCESS 5/5 filings | 4m 36s | model calls 11 (server retries 0) | warnings 3
```

A failed year shows as one line with the reason, for example `FY 2022-23 FAILED <reason>`. The block has at most about 12 lines for a 5-year company.

Set `LOG_STYLE=detailed` to see every step again when investigating.

### 4. One retry on model-server errors
If the model server cannot be reached, times out, or returns HTTP 5xx or 429, the same request is sent **once more** after 30 seconds. The retry is shown in the summary as `model server error (…) - retrying in 30 s`. If the retry also fails, the filing is reported Failed.

- An HTTP 4xx error (a problem with the request itself) is not retried.
- A bad answer (broken JSON, a cut-off reply) is not handled here; the two-call flow has its own handling for that.
- Settings: `LLM_SERVER_RETRIES=1`, `LLM_SERVER_RETRY_WAIT_S=30`.

### 5. `.env.example`
Updated with the production values: two-call flow only, retry, `LOG_STYLE`, local source and all output switches off. It contains no credentials. Do not ship `.env`, and rotate the AWS keys.

## Proof

| Check | Result |
|---|---|
| Replay of 158 filings (8,508 units), v9 against v10 | **Identical**: labels, amounts, red flags, review flags, texts and explanations |
| New tests V10-1 … V10-16 (output switches, source rules, layouts and search depth, no `boto3` for local, retry on 503 / no retry on 400 / server down, summary-only log, per-CIN block, no `print()` in the production code) | 16/16 pass |
| V9 tests | 28/28 pass |
| Existing suite, every check that could run here | Results identical to v9. The rest needs `payload.json` and `caro_section_debug_json_foundry.txt`, so please run the full suite on your machine |

Run the tests:

```
python tests/run_phase2_cases.py --v10-only   # V9 + V10 checks: expect 44/44
python tests/run_phase2_cases.py              # full suite
```

## Not covered by the proof here
The proof does not include a full run from the server folder through the model: the AOC-4 forms and the model server are not available here. Please run 2 CINs on the server with the production `.env` and check four things:
1. only `logs/api_<time>.log` and the results are created;
2. the terminal shows the summary block only;
3. no `xml_caro_output/` folder is created;
4. the labels match your current 12-CIN run.

## Rollback
- Detailed logs: `LOG_STYLE=detailed`.
- All debug folders on: `CARO_SAVE_ALL=true`.
- No retry: `LLM_SERVER_RETRIES=0`.
- S3 source: `CIN_SOURCE=s3`.
- Full rollback: reinstall the v9 pack.
