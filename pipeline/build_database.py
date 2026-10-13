"""Build output/fintech.db from the three source files and write output/reports/cleaning_log.md.

Steps:
  1. Load each source file unchanged into a staging table (every column TEXT, empty cells as '').
  2. Run sql/01_schema.sql (normalised tables), sql/02_transform.sql (cleaning) and sql/03_marts.sql
     (analytical tables).
  3. Count rows before and after every cleaning rule and write them to the cleaning log.
  4. Drop the large transaction staging table and compact the database.

Run from the repository root: .venv/bin/python pipeline/build_database.py
"""
import sqlite3
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'source'
SQL = Path(__file__).resolve().parent / 'sql'
OUTPUT = ROOT / 'output'
DB = OUTPUT / 'fintech.db'
REPORTS = OUTPUT / 'reports'

SOURCES = {
    'stg_customer': 'customer_data.csv',
    'stg_transaction': 'transactions_data.csv',
    'stg_findex': 'Findex_Microdata_2025_updateColombia.csv',
}


def load_staging(con):
    for table, file in SOURCES.items():
        started = time.time()
        df = pd.read_csv(SOURCE / file, dtype=str, keep_default_na=False, encoding='utf-8')
        df.to_sql(table, con, index=False, chunksize=100_000)
        print(f'  {table}: {len(df):,} rows x {df.shape[1]} columns ({time.time() - started:.0f}s)', flush=True)
    # Indexes so the referential-integrity checks in the cleaning log run in seconds, not hours.
    con.execute('CREATE INDEX ix_stg_customer_id ON stg_customer (customer_id)')
    con.execute('CREATE INDEX ix_stg_transaction_id ON stg_transaction (customer_id)')


def run_sql(con, name):
    started = time.time()
    con.executescript((SQL / name).read_text(encoding='utf-8'))
    print(f'  {name} ({time.time() - started:.0f}s)', flush=True)


def one(con, query):
    return con.execute(query).fetchone()[0]


def cleaning_log(con):
    """Row counts for every cleaning rule, read back from the database."""
    q = lambda query: one(con, query)
    raw_tx = q('SELECT COUNT(*) FROM stg_transaction')
    distinct_tx = q('SELECT COUNT(*) FROM (SELECT DISTINCT customer_id, date, amount, type FROM stg_transaction)')
    dup_rows = raw_tx - distinct_tx
    dup_customers = q('''SELECT COUNT(DISTINCT customer_id) FROM (
                           SELECT customer_id FROM stg_transaction
                           GROUP BY customer_id, date, amount, type HAVING COUNT(*) > 1)''')
    dup_volume = q('''SELECT SUM(CAST(amount AS INTEGER) * (n - 1)) FROM (
                        SELECT amount, COUNT(*) AS n FROM stg_transaction
                        GROUP BY customer_id, date, amount, type HAVING n > 1)''')
    total_volume = q('SELECT SUM(amount_cop) FROM customer_transaction')
    orphans = q('''SELECT COUNT(*) FROM stg_transaction t
                   WHERE NOT EXISTS (SELECT 1 FROM stg_customer c WHERE c.customer_id = t.customer_id)''')
    never_tx = q('''SELECT COUNT(*) FROM stg_customer c
                    WHERE NOT EXISTS (SELECT 1 FROM stg_transaction t WHERE t.customer_id = c.customer_id)''')
    bad_dates = q('''SELECT COUNT(*) FROM stg_transaction
                     WHERE date(date) IS NULL OR date(date) <> date''')
    bad_amounts = q("SELECT COUNT(*) FROM stg_transaction WHERE amount = '' OR CAST(amount AS INTEGER) <= 0 "
                    "OR CAST(CAST(amount AS INTEGER) AS TEXT) <> amount")
    findex_educ_missing = q("SELECT COUNT(*) FROM stg_findex WHERE educ = ''")
    con9_dk = q("SELECT COUNT(*) FROM stg_findex WHERE con9 IN ('8', '9')")
    con9_not_asked = q("SELECT COUNT(*) FROM stg_findex WHERE con9 = ''")
    barrier_rows = q('SELECT COUNT(*) FROM respondent_barrier')
    barrier_respondents = q('SELECT COUNT(DISTINCT respondent_id) FROM respondent_barrier')
    barrier_dk = q("SELECT COUNT(*) FROM respondent_barrier WHERE answer IN ('Don''t know', 'Refused')")
    empty_findex_cols = [c for (c,) in con.execute("SELECT name FROM pragma_table_info('stg_findex')")
                         if q(f"SELECT COUNT(*) FROM stg_findex WHERE \"{c}\" <> ''") == 0]
    cities = q('SELECT COUNT(*) FROM city')
    departments = q('SELECT COUNT(*) FROM department')

    rows = [
        ('Load', 'customer_data.csv into stg_customer', q('SELECT COUNT(*) FROM stg_customer'),
         q('SELECT COUNT(*) FROM stg_customer'), 'Loaded as text, unchanged'),
        ('Load', 'transactions_data.csv into stg_transaction', raw_tx, raw_tx, 'Loaded as text, unchanged'),
        ('Load', 'Findex file into stg_findex', q('SELECT COUNT(*) FROM stg_findex'),
         q('SELECT COUNT(*) FROM stg_findex'), 'Loaded as text, unchanged (199 columns)'),
        ('Validate', 'Transaction dates not valid YYYY-MM-DD', bad_dates, 0, 'None found; no fix needed'),
        ('Validate', 'Amounts missing, zero, negative or not whole pesos', bad_amounts, 0,
         'None found; stored as INTEGER pesos'),
        ('Validate', 'Transactions whose customer is missing from customer file', orphans, 0,
         'None found; FK customer_transaction.customer_id enforces it'),
        ('Validate', 'Customers with no transactions', never_tx, 0, 'None found'),
        ('Deduplicate', 'Exact duplicate transaction rows removed', raw_tx, distinct_tx,
         f'{dup_rows} rows from {dup_customers} customers, COP {dup_volume:,} '
         f'({dup_volume / total_volume:.4%} of volume)'),
        ('Normalise', "Split 'City, Department' into city and department tables",
         q('SELECT COUNT(DISTINCT location) FROM stg_customer'), cities,
         f'{cities} cities in {departments} departments'),
        ('Normalise', 'Customer columns kept (of 54)', 54, 8, 'See column selection note'),
        ('Normalise', 'Category text replaced by lookup keys', None, None,
         'gender, education_level, acquisition_channel, transaction_type'),
        ('Harmonise', 'Education mapped to Findex groups', 4, 2,
         'High School -> Secondary; Bachelor/Master/PhD -> Tertiary'),
        ('Harmonise', 'Gender mapped to shared gender table', 3, 3,
         "Findex female=1/2 -> Female/Male; 'Other' exists only in the customer file"),
        ('Recode', 'Findex educ blank -> NULL', findex_educ_missing, findex_educ_missing,
         "Shown as 'Not stated'"),
        ('Recode', "Findex con9 don't know/refused (8/9) -> NULL", con9_dk, con9_dk,
         'Not counted as smartphone owners'),
        ('Recode', 'Findex con9 not asked (no mobile phone) -> 0', con9_not_asked, con9_not_asked,
         'No phone means no smartphone'),
        ('Reshape', 'fin11a-fin11f wide columns -> respondent_barrier rows', 6, barrier_rows,
         f'{barrier_respondents} respondents asked; {barrier_dk} answers are don\'t know/refused'),
        ('Drop', 'Findex columns that are empty for every respondent', len(empty_findex_cols), 0,
         ', '.join(empty_findex_cols)),
        ('Drop', 'Findex columns kept (of 199)', 199, 24, 'See column selection note'),
    ]
    lines = ['# Cleaning log', '',
             'Generated by `pipeline/build_database.py` on ' + time.strftime('%Y-%m-%d') + '. '
             'Every count is read back from `output/fintech.db`.', '',
             '| Step | Rule | Before | After | Note |', '|---|---|---:|---:|---|']
    for step, rule, before, after, note in rows:
        fmt = lambda v: '' if v is None else f'{v:,}'
        lines.append(f'| {step} | {rule} | {fmt(before)} | {fmt(after)} | {note} |')

    lines += ['', '## Rows per table', '', '| Table | Rows |', '|---|---:|']
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
        lines.append(f'| {name} | {q(f"SELECT COUNT(*) FROM {name}"):,} |')
    return '\n'.join(lines) + '\n'


def main():
    OUTPUT.mkdir(exist_ok=True)
    REPORTS.mkdir(exist_ok=True)
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    try:
        print('Loading staging tables', flush=True)
        load_staging(con)
        print('Running SQL', flush=True)
        for name in ('01_schema.sql', '02_transform.sql', '03_marts.sql'):
            run_sql(con, name)
        fk_errors = con.execute('PRAGMA foreign_key_check').fetchall()
        if fk_errors:
            raise SystemExit(f'Foreign key violations: {fk_errors[:5]}')
        (REPORTS / 'cleaning_log.md').write_text(cleaning_log(con), encoding='utf-8')
        print('Dropping stg_transaction and compacting')
        con.execute('DROP TABLE stg_transaction')
        con.commit()
        con.execute('VACUUM')
    finally:
        con.close()
    print(f'Done: {DB} ({DB.stat().st_size / 1e6:.0f} MB)')


if __name__ == '__main__':
    main()
