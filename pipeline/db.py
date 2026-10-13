"""MySQL connection and query helpers shared by the pipeline scripts.

Login details come from pipeline/db_config.ini, which every team member creates locally from
db_config.example.ini and which git ignores, so no password ends up in the repository.
"""
import configparser
import datetime
import decimal
import re
from pathlib import Path

import pandas as pd
import pymysql
from pymysql.cursors import SSCursor

CONFIG = Path(__file__).resolve().parent / 'db_config.ini'


def settings():
    if not CONFIG.exists():
        raise SystemExit('Missing pipeline/db_config.ini: copy pipeline/db_config.example.ini to '
                         'pipeline/db_config.ini and fill in your MySQL password.')
    parser = configparser.ConfigParser(interpolation=None)   # no interpolation: passwords may contain '%'
    parser.read(CONFIG, encoding='utf-8')
    return parser['mysql']


def connect():
    s = settings()
    return pymysql.connect(host=s.get('host', 'localhost'), port=s.getint('port', 3306),
                           user=s['user'], password=s['password'], database=s['database'],
                           charset='utf8mb4', local_infile=True, autocommit=True,
                           # Same collation as the tables, so text literals in the SQL compare and UNION cleanly
                           init_command='SET NAMES utf8mb4 COLLATE utf8mb4_0900_as_cs')


def statements(sql):
    """Split an SQL script into statements. Comments go first, because some contain ';' or quotes."""
    sql = re.sub(r'--[^\n]*', '', sql)
    return [s.strip() for s in sql.split(';') if s.strip()]


def run_script(con, path):
    with con.cursor() as cur:
        for statement in statements(Path(path).read_text(encoding='utf-8')):
            cur.execute(statement)


def _plain(value):
    """MySQL returns DECIMAL for money and for SUM/AVG results; make them int or float for pandas."""
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _normalise(df):
    """Turn DECIMAL columns into int/float and DATE columns into ISO text ('2023-01-31')."""
    for col in df.columns:
        values = df[col].dropna()
        if df[col].dtype != object or values.empty:
            continue
        if isinstance(values.iloc[0], decimal.Decimal):
            if all(v == v.to_integral_value() for v in values):
                df[col] = df[col].map(lambda v: None if v is None else int(v))
                df[col] = df[col].astype('Int64' if df[col].isna().any() else 'int64')
            else:
                df[col] = df[col].astype(float)
        elif isinstance(values.iloc[0], datetime.date):
            df[col] = df[col].map(lambda v: None if v is None else v.isoformat())
    return df


def value(con, sql):
    """First column of the first row."""
    with con.cursor() as cur:
        cur.execute(sql)
        return _plain(cur.fetchone()[0])


def rows(con, sql):
    with con.cursor() as cur:
        cur.execute(sql)
        return [tuple(_plain(v) for v in row) for row in cur.fetchall()]


def query(con, sql):
    """Run a SELECT and return a DataFrame."""
    with con.cursor() as cur:
        cur.execute(sql)
        columns = [d[0] for d in cur.description]
        return _normalise(pd.DataFrame(list(cur.fetchall()), columns=columns))


def query_chunks(con, sql, size=250_000):
    """Stream a large SELECT as DataFrames of `size` rows (server-side cursor, low memory)."""
    with con.cursor(SSCursor) as cur:
        cur.execute(sql)
        columns = [d[0] for d in cur.description]
        while batch := cur.fetchmany(size):
            yield _normalise(pd.DataFrame(list(batch), columns=columns))
