# v10: dues-table amounts in absolute rupees (15 Oct 2026)

This pack is cumulative: v9 (Fixes 1–4, two-call flow only) + v10 Step 1 (inputs, outputs, logs, retry) + the table amounts described here. Copy every file over the repository, keeping the same paths.

## What changed

**1. Table amounts in rupees** (`caro/amounts.py`, rule `TABLE_ABSOLUTE`)

Every row of `disputed_dues_table` now shows `amount` in absolute rupees, the same way `amount_total` does.

Example, Apollo 2020-21 (vii), where the auditor's table is headed "(Rs. In Lacs)":

| As printed | Now in the JSON |
|---|---|
| `"22.95*"` | `2295000.0` |
| `"185.59*"` | `18559000.0` |
| `amount_total` | `20854000.0` (unchanged) |

How a row is converted (first match wins):
1. **Same figure as `amount_total`:** if the code already converted this figure for `amount_total`, that value is reused, so the rows always agree with the total.
2. **Unit in the cell, or full rupees:** `"12.5 lakh"`, `"15,410/-"`, `"1,00,65,542"`, `"714,067"`.
3. **Table heading:** the heading of the table the figure sits in, such as "(Rs. in lakhs)", or a unit word printed right after the figure.
4. **Whole number with no heading anywhere:** taken as rupees, as printed.

Two more rules:
- **Several figures in one cell are added up.** K.R. Foods prints "Rs. 20,95,74,867 and Rs. 1,00,65,542 respectively" in one cell; the row becomes 21,96,40,409.
- **Nothing is guessed.** A decimal figure with no unit anywhere, or a cell that holds a period ("2011-12") or a heading ("Rs. in lakhs") instead of an amount, keeps the auditor's text. That unit is sent for review with reason `table_amount_unit_unclear`. This is the same rule already used for `amount_total`.

Other row fields (nature of dues, forum, period) are not changed. The schema already allows a number in `amount`.

**2. Missed unit heading `"Rs.( Lakh)"`** (rule `HEADING_RS_PAREN`)

This heading form was not recognised before. As a result, K C India's disputed income tax showed `amount_total = 149.23` instead of `14923000.0`. This affected 4 years: 2021-22 to 2024-25, both (vii) and (vii)(b).

## Proof (replay of 158 filings, 8,508 units, v10 Step 1 vs this pack)

| Check | Result |
|---|---|
| Labels, source texts, explanations, red flags, review flags | 0 changes |
| `amount_total` | 8 changes, all K C India (the heading fix): 149.23 → 14,923,000; 162.02 → 16,202,000; 149.24 → 14,924,000 |
| Dues-table rows | 294 rows; 242 converted to rupees |
| Sub-clauses where every row is a number | The rows add up to `amount_total` in every case (0 differences) |
| Rows left as printed (sent for review) | 44, all Aditya Auto 2021-22 to 2024-25. The model put the heading text ("Rs. in lakhs") or the period ("2011-12, 2012-13") in the amount column. These units are already Adverse and therefore already reviewed. This is a Call 1 extraction problem, not a conversion problem |
| Tests | V10-17 … V10-23 (real wordings: Apollo, K.R. Foods, Aditya, K C India): 7/7 pass. V9 + V10 together: 51/51 pass. The existing suite gives the same results as v9 on every check that could run here |

**Note on main clauses:** on a main clause such as (vii), `amount_total` is the amount of its most serious sub-clause. That is the existing agreed rule. So the main clause's table rows (disputed dues) may not add up to its `amount_total` when (vii)(a) arrears is the more serious finding (Aditya 2020-21, Rama 2024-25). The sub-clause (vii)(b) rows always add up to (vii)(b)'s total.

## Rollback
In `caro/amounts.py`, `AMOUNT_SWITCHES`:
- `"TABLE_ABSOLUTE": False` restores the printed row text;
- `"HEADING_RS_PAREN": False` restores the old heading behaviour.

## Tests

```
python tests/run_phase2_cases.py --v10-only   # expect 51/51
python tests/run_phase2_cases.py              # full suite, on your machine
```
