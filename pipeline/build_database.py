"""Build the MySQL database from the three source files and write output/reports/cleaning_log.md.

Steps:
  1. Drop every table in the configured database, so each run starts clean.
  2. Load each source file unchanged into a staging table (every value as text, empty cells as '')
     with LOAD DATA LOCAL INFILE.
  3. Run sql/01_schema.sql (normalised tables), sql/02_transform.sql (cleaning) and sql/03_marts.sql
     (analytical tables).
  4. Count rows before and after every cleaning rule and write them to the cleaning log.
  5. Drop the large transaction staging table.

Run from the repository root: .venv/bin/python pipeline/build_database.py
"""
import csv
import time
from pathlib import Path

import db

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'source'
SQL = Path(__file__).resolve().parent / 'sql'
REPORTS = ROOT / 'output' / 'reports'

SOURCES = {
    'stg_customer': 'customer_data.csv',
    'stg_transaction': 'transactions_data.csv',
    'stg_findex': 'Findex_Microdata_2025_updateColombia.csv',
}


LINE_ENDINGS = {}   # file -> (header ending, data ending), reported in the cleaning log


def line_endings(path):
    """Line ending of the header and of the first data row. The Findex file mixes them (header CRLF, data LF),
    so the data rows decide what LOAD DATA splits on; the header is skipped anyway."""
    with open(path, 'rb') as f:
        header, first_row = f.readline(), f.readline()
    name = lambda line: 'CRLF' if line.endswith(b'\r\n') else 'LF'
    return name(header), name(first_row)


def column_widths(path):
    """Header and the longest value per column. Staging columns are sized to the data: InnoDB rows are limited
    to about 8 KB, and 199 TEXT columns (Findex) would exceed that limit."""
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f)
        header = next(reader)
        widths = [1] * len(header)
        for row in reader:
            widths = [max(w, len(v)) for w, v in zip(widths, row)]
    return header, widths


def reset_database(con):
    name = db.settings()['database']
    with con.cursor() as cur:
        cur.execute(f'ALTER DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs')
        cur.execute('SET FOREIGN_KEY_CHECKS = 0')
        cur.execute('SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()')
        for (table,) in cur.fetchall():
            cur.execute(f'DROP TABLE `{table}`')
        cur.execute('SET FOREIGN_KEY_CHECKS = 1')


def load_staging(con):
    for table, file in SOURCES.items():
        started = time.time()
        path = SOURCE / file
        header, widths = column_widths(path)
        LINE_ENDINGS[file] = line_endings(path)
        line_end = r'\r\n' if LINE_ENDINGS[file][1] == 'CRLF' else r'\n'
        columns = ', '.join(f'`{c}` VARCHAR({w}) NOT NULL' for c, w in zip(header, widths))
        with con.cursor() as cur:
            cur.execute(f'CREATE TABLE `{table}` ({columns})')
            cur.execute(f"""LOAD DATA LOCAL INFILE '{path.as_posix()}' INTO TABLE `{table}`
                            CHARACTER SET utf8mb4
                            FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
                            LINES TERMINATED BY '{line_end}'
                            IGNORE 1 LINES""")
            cur.execute('SHOW COUNT(*) WARNINGS')
            warnings = cur.fetchone()[0]
        loaded = db.value(con, f'SELECT COUNT(*) FROM `{table}`')
        print(f'  {table}: {loaded:,} rows x {len(header)} columns, {warnings} warnings '
              f'({time.time() - started:.0f}s)', flush=True)
        if warnings:
            raise SystemExit(f'LOAD DATA reported {warnings} warnings for {file}; check the file format.')
    # Indexes so the referential-integrity checks in the cleaning log run in seconds.
    with con.cursor() as cur:
        cur.execute('CREATE INDEX ix_stg_customer_id ON stg_customer (customer_id)')
        cur.execute('CREATE INDEX ix_stg_transaction_id ON stg_transaction (customer_id)')


def run_sql(con, name):
    started = time.time()
    db.run_script(con, SQL / name)
    print(f'  {name} ({time.time() - started:.0f}s)', flush=True)


def cleaning_log(con):
    """Row counts for every cleaning rule, read back from the database."""
    q = lambda sql: db.value(con, sql)
    raw_tx = q('SELECT COUNT(*) FROM stg_transaction')
    distinct_tx = q('SELECT COUNT(*) FROM (SELECT DISTINCT customer_id, date, amount, type FROM stg_transaction) d')
    dup_rows = raw_tx - distinct_tx
    dup_customers = q('''SELECT COUNT(DISTINCT customer_id) FROM (
                           SELECT customer_id FROM stg_transaction
                           GROUP BY customer_id, date, amount, type HAVING COUNT(*) > 1) d''')
    dup_volume = q('''SELECT SUM(CAST(amount AS UNSIGNED) * (n - 1)) FROM (
                        SELECT amount, COUNT(*) AS n FROM stg_transaction
                        GROUP BY customer_id, date, amount, type HAVING n > 1) d''')
    total_volume = q('SELECT SUM(amount_cop) FROM customer_transaction')
    orphans = q('''SELECT COUNT(*) FROM stg_transaction t
                   WHERE NOT EXISTS (SELECT 1 FROM stg_customer c WHERE c.customer_id = t.customer_id)''')
    never_tx = q('''SELECT COUNT(*) FROM stg_customer c
                    WHERE NOT EXISTS (SELECT 1 FROM stg_transaction t WHERE t.customer_id = c.customer_id)''')
    bad_dates = q('''SELECT COUNT(*) FROM stg_transaction
                     WHERE STR_TO_DATE(date, '%Y-%m-%d') IS NULL
                        OR DATE_FORMAT(STR_TO_DATE(date, '%Y-%m-%d'), '%Y-%m-%d') <> date''')
    bad_amounts = q("SELECT COUNT(*) FROM stg_transaction WHERE amount NOT REGEXP '^[0-9]+$' "
                    "OR CAST(amount AS UNSIGNED) = 0")
    findex_educ_missing = q("SELECT COUNT(*) FROM stg_findex WHERE educ = ''")
    con9_dk = q("SELECT COUNT(*) FROM stg_findex WHERE con9 IN ('8', '9')")
    con9_not_asked = q("SELECT COUNT(*) FROM stg_findex WHERE con9 = ''")
    barrier_rows = q('SELECT COUNT(*) FROM respondent_barrier')
    barrier_respondents = q('SELECT COUNT(DISTINCT respondent_id) FROM respondent_barrier')
    barrier_dk = q("SELECT COUNT(*) FROM respondent_barrier WHERE answer IN ('Don''t know', 'Refused')")
    findex_columns = db.rows(con, '''SELECT column_name FROM information_schema.columns
                                     WHERE table_schema = DATABASE() AND table_name = 'stg_findex'
                                     ORDER BY ordinal_position''')
    empty_findex_cols = [c for (c,) in findex_columns
                         if q(f"SELECT COUNT(*) FROM stg_findex WHERE `{c}` <> ''") == 0]
    cities = q('SELECT COUNT(*) FROM city')
    departments = q('SELECT COUNT(*) FROM department')

    rows = [
        ('Load', 'customer_data.csv into stg_customer', q('SELECT COUNT(*) FROM stg_customer'),
         q('SELECT COUNT(*) FROM stg_customer'), 'Loaded as text, unchanged'),
        ('Load', 'transactions_data.csv into stg_transaction', raw_tx, raw_tx, 'Loaded as text, unchanged'),
        ('Load', 'Findex file into stg_findex', q('SELECT COUNT(*) FROM stg_findex'),
         q('SELECT COUNT(*) FROM stg_findex'), 'Loaded as text, unchanged (199 columns)'),
        ('Validate', 'Line endings (header / data rows)', None, None,
         '; '.join(f'{file}: {h} / {d}' + (' (mixed)' if h != d else '') for file, (h, d) in LINE_ENDINGS.items())),
        ('Validate', 'Transaction dates not valid YYYY-MM-DD', bad_dates, 0, 'None found; stored as DATE'),
        ('Validate', 'Amounts missing, zero, negative or not whole pesos', bad_amounts, 0,
         'None found; stored as DECIMAL(14,0) pesos'),
        ('Validate', 'Transactions whose customer is missing from customer file', orphans, 0,
         'None found; FK customer_transaction.customer_id enforces it'),
        ('Validate', 'Customers with no transactions', never_tx, 0, 'None found'),
        ('Deduplicate', 'Exact duplicate transaction rows removed', raw_tx, distinct_tx,
         f'{dup_rows} rows from {dup_customers} customers, COP {dup_volume:,} '
         f'({dup_volume / total_volume:.4%} of volume)'),
        ('Normalise', "Split 'City, Department' into city and department tables",
         q('SELECT COUNT(DISTINCT location) FROM stg_customer'), cities,
         f'{cities} cities in {departments} departments'),
        ('Normalise', 'Customer columns kept (of 54)', 54, 6,
         'See column selection note; age and churn_probability dropped'),
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
             'Every count is read back from the MySQL database.', '',
             '| Step | Rule | Before | After | Note |', '|---|---|---:|---:|---|']
    for step, rule, before, after, note in rows:
        fmt = lambda v: '' if v is None else f'{v:,}'
        lines.append(f'| {step} | {rule} | {fmt(before)} | {fmt(after)} | {note} |')

    lines += ['', '## Rows per table', '', '| Table | Rows |', '|---|---:|']
    tables = db.rows(con, '''SELECT table_name FROM information_schema.tables
                             WHERE table_schema = DATABASE() ORDER BY table_name''')
    for (name,) in tables:
        lines.append(f'| {name} | {q(f"SELECT COUNT(*) FROM `{name}`"):,} |')
    return '\n'.join(lines) + '\n'


def main():
    REPORTS.mkdir(parents=True, exist_ok=True)
    con = db.connect()
    try:
        print(f'Resetting database {db.settings()["database"]}', flush=True)
        reset_database(con)
        print('Loading staging tables', flush=True)
        load_staging(con)
        print('Running SQL', flush=True)
        for name in ('01_schema.sql', '02_transform.sql', '03_marts.sql'):
            run_sql(con, name)
        (REPORTS / 'cleaning_log.md').write_text(cleaning_log(con), encoding='utf-8')
        print('Dropping stg_transaction', flush=True)
        with con.cursor() as cur:
            cur.execute('DROP TABLE stg_transaction')
    finally:
        con.close()
    print('Done', flush=True)


if __name__ == '__main__':
    main()
