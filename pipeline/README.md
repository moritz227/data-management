# Pipeline: from the three source files to Tableau

Builds a normalised SQLite database from the three Story 1 sources, recalculates the eight KPIs and exports clean CSVs for Tableau Public. All cleaning and modelling is in SQL (`sql/`). Python only loads the files, runs the SQL and writes the reports.

## Run it

From the repository root:

```bash
.venv/bin/python pipeline/run_pipeline.py
```

This takes about 35 seconds. It needs only pandas, which is already in `.venv`. Without the venv:

```bash
uv run --no-project --with pandas python pipeline/run_pipeline.py
```

Add `--skip-transactions` to leave out the 175 MB transaction-level export.

## What runs, in order

| Step | File | What it does |
|---|---|---|
| 1 | `build_database.py` | Loads `source/*.csv` unchanged into staging tables (all text), then runs the three SQL files |
| 1a | `sql/01_schema.sql` | Logical model: tables, primary and foreign keys, data types, CHECK constraints |
| 1b | `sql/02_transform.sql` | Cleaning and integration: de-duplication, splitting city and department, lookup tables, Findex recoding, wide-to-long barriers, harmonised gender and education |
| 1c | `sql/03_marts.sql` | Analysis tables: customer-month grid with growth states, customer summary (status, value tier, median gap), cohorts, churn sensitivity, Findex funnel, barriers, profile comparison |
| 2 | `check_kpis.py` | Recalculates every Story 1 number and the data-note checks, then writes `output/reports/kpi_check.md` |
| 3 | `export_tableau.py` | Writes `output/tableau/*.csv` |
| - | `erd.dbml` | ERD of the core model; paste it into [dbdiagram.io](https://dbdiagram.io) to draw it |

## Outputs

| Path | Contents |
|---|---|
| `output/fintech.db` | SQLite database (about 320 MB, git-ignored). Open it with DB Browser for SQLite or `sqlite3` to run your own queries |
| `output/reports/cleaning_log.md` | Every cleaning rule with row counts before and after |
| `output/reports/kpi_check.md` | Story 1 numbers vs recalculated values, plus the data-quality checks |
| `output/tableau/*.csv` | Tableau data sources (see the docstring in `export_tableau.py`) |

## Definitions used (from Story 1)

- **Active in a month:** at least one transaction in that calendar month.
- **Reference date:** 29 Dec 2023, the last date in the transaction file.
- **Churned:** 60 or more days since the last transaction. **At risk:** 30 to 59 days. **Active:** under 30 days.
- **Growth states per month:** New (first month with a transaction), Retained (active this and last month), Returning (active now, not last month), Lost (active last month, not now), Dormant. January is `Baseline`, because the data starts there.
- **Quick Ratio:** (New + Returning) ÷ Lost.
- **Top 1%:** the 487 customers with the highest 2023 volume.
- **Findex shares:** weighted with `wgt`. Counts of adults are scaled to `pop_adult` (41,544,122).
- **Reachable unbanked:** `account = 0`, `con9 = 1` (smartphone) and `internet_use = 1`.
