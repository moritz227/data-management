"""Export the Tableau data sources from output/fintech.db to output/tableau/*.csv (UTF-8, comma-separated).

Fintech data source (relate in Tableau on customer_id):
  customers.csv              one row per customer: profile + 2023 behaviour (status, volume, value tier)
  customer_month_status.csv  one row per customer per month (48,723 x 12): activity and growth state
  transactions.csv           one row per transaction (3.16M rows, ~150 MB; skip with --skip-transactions)
Pre-aggregated helpers (stand-alone sheets):
  monthly_growth.csv, cohort_retention.csv, churn_threshold.csv
Findex data source:
  findex_respondents.csv     one row per respondent with weight and adults_represented
  findex_barriers.csv        one row per respondent per barrier question (relate on respondent_id)
  findex_funnel.csv          KPI 6 funnel steps
Integration:
  profile_comparison.csv     customers vs Findex groups on gender, education and area (KPI 8)

Run from the repository root after build_database.py: .venv/bin/python pipeline/export_tableau.py
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'output' / 'fintech.db'
OUT = ROOT / 'output' / 'tableau'

HEAVY_ACCOUNT_TX = 1000   # more than ~3 transactions a day all year: possibly a business account (KPI 5 caveat)

EXPORTS = {
    'customers': f'''
        SELECT c.customer_id,
               g.gender_name                              AS gender,
               c.age,
               ci.city_name                               AS city,
               d.department_name                          AS department,
               c.income_bracket,
               el.education_level_name                    AS education_level,
               el.rank_order                              AS education_level_order,
               eg.education_group_name                    AS education_group,
               CASE WHEN eg.education_group_id = 3 THEN 1 ELSE 0 END AS has_degree,
               ch.channel_name                            AS acquisition_channel,
               s.first_tx_date,
               substr(s.first_tx_date, 1, 7) || '-01'     AS first_active_month,
               s.last_tx_date,
               s.days_since_last_tx,
               s.status_60d,
               s.tx_count,
               s.active_days,
               s.volume_cop,
               s.median_gap_days,
               s.volume_rank,
               s.value_tier,
               CASE WHEN s.tx_count > {HEAVY_ACCOUNT_TX} THEN 1 ELSE 0 END AS is_heavy_account
        FROM customer c
        JOIN gender g               ON g.gender_id = c.gender_id
        JOIN city ci                ON ci.city_id = c.city_id
        JOIN department d           ON d.department_id = ci.department_id
        JOIN education_level el     ON el.education_level_id = c.education_level_id
        JOIN education_group eg     ON eg.education_group_id = el.education_group_id
        JOIN acquisition_channel ch ON ch.channel_id = c.channel_id
        JOIN mart_customer_summary s ON s.customer_id = c.customer_id
        ORDER BY c.customer_id''',
    'customer_month_status': '''
        SELECT customer_id, month, is_active, tx_count, volume_cop, growth_state
        FROM mart_customer_month_status ORDER BY customer_id, month''',
    'transactions': '''
        SELECT t.transaction_id, t.customer_id, t.transaction_date,
               substr(t.transaction_date, 1, 7) || '-01' AS month,
               t.amount_cop, tt.transaction_type_name AS transaction_type
        FROM customer_transaction t
        JOIN transaction_type tt ON tt.transaction_type_id = t.transaction_type_id
        ORDER BY t.transaction_id''',
    'monthly_growth': 'SELECT * FROM mart_monthly_growth ORDER BY month',
    'cohort_retention': 'SELECT * FROM mart_cohort_retention ORDER BY cohort_month, months_since_first',
    'churn_threshold': 'SELECT * FROM mart_churn_threshold ORDER BY threshold_days',
    'findex_respondents': 'SELECT * FROM mart_findex_respondent ORDER BY respondent_id',
    # Weight and filter fields are repeated here because Tableau cannot mix fields from two related tables
    # in one row-level calculation (weighted share = SUM(IF yes THEN weight) / SUM(weight)).
    'findex_barriers': '''
        SELECT rb.respondent_id, b.findex_code, b.barrier_label, rb.answer,
               CASE WHEN rb.answer = 'Yes' THEN 1 ELSE 0 END AS is_yes,
               r.weight, r.adults_represented, r.gender, r.area, r.education_group,
               r.income_quintile, r.is_reachable_unbanked
        FROM respondent_barrier rb
        JOIN account_barrier b         ON b.barrier_id = rb.barrier_id
        JOIN mart_findex_respondent r  ON r.respondent_id = rb.respondent_id
        ORDER BY rb.respondent_id, b.findex_code''',
    'findex_funnel': 'SELECT * FROM mart_findex_funnel ORDER BY step',
    'profile_comparison': 'SELECT * FROM mart_profile_comparison',
}


def main():
    skip = {'transactions'} if '--skip-transactions' in sys.argv else set()
    OUT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    try:
        for name, sql in EXPORTS.items():
            if name in skip:
                continue
            df = pd.read_sql_query(sql, con)
            # 0/1 columns with NULLs come back as floats (1.0); write them as whole numbers.
            for col in df.select_dtypes('float').columns:
                if (df[col].dropna() % 1 == 0).all():
                    df[col] = df[col].astype('Int64')
            path = OUT / f'{name}.csv'
            df.to_csv(path, index=False, encoding='utf-8')
            print(f'  {path.name}: {len(df):,} rows, {path.stat().st_size / 1e6:.1f} MB', flush=True)
    finally:
        con.close()


if __name__ == '__main__':
    main()
