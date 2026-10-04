"""Validate COFINFAD (customer_data.csv, transactions_data.csv) against its Data in Brief paper.

Muñoz-Guerrero, Ceballos and Trejos-Rojas (2026), doi:10.1016/j.dib.2026.112484
Data: Mendeley Data, doi:10.17632/mhb4zn3258.1

Run: uv run --no-project --with pandas python validate_data.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent

c = pd.read_csv(DATA / 'customer_data.csv')
t = pd.read_csv(DATA / 'transactions_data.csv')


def section(title):
    print(f'\n== {title} ==')


section('Shape')
print('customer_data rows x cols:', c.shape)
print('transactions rows:', len(t), '| date range:', t.date.min(), 'to', t.date.max())
print('transaction types:', t.type.value_counts().to_dict())

section('Identifiers and demographics (paper: id 1-48723, age binned 18-65)')
print('customer_id range:', c.customer_id.min(), '-', c.customer_id.max(), '| unique:', c.customer_id.nunique())
print('age range:', c.age.min(), '-', c.age.max(), '| share multiple of 5:', round((c.age % 5 == 0).mean(), 3))
print('income_bracket (paper: 12% non-response):', c.income_bracket.value_counts(dropna=False).to_dict())

section('Missing values (paper: only complaint_topics, credit_utilization_ratio, feature_requests)')
missing = c.isna().mean()
print(missing[missing > 0].round(4).to_dict())

section('Survey fields (paper: NPS 0-10, 6,965 respondents, surveys in Feb/May/Aug/Nov)')
outside = ((c.nps_score < 0) | (c.nps_score > 10)).mean()
print('nps_score range:', c.nps_score.min(), '-', c.nps_score.max(), '| share outside 0-10:', round(outside, 3))
print('non-null nps / satisfaction / survey date:',
      c.nps_score.notna().sum(), c.satisfaction_score.notna().sum(), c.last_survey_date.notna().sum())
print('satisfaction_score range:', c.satisfaction_score.min(), '-', c.satisfaction_score.max())
print('last_survey_date by month:',
      pd.to_datetime(c.last_survey_date).dt.month.value_counts().sort_index().to_dict())

section('Duplicate columns vs aggregates from transactions_data.csv')
agg = t.groupby('customer_id').agg(n=('amount', 'size'), first=('date', 'min'),
                                   last=('date', 'max'), vol=('amount', 'sum'))
m = c.set_index('customer_id').join(agg)
print('customers with transactions:', agg.shape[0])
print('tx_count == row count:', round((m.tx_count == m.n).mean(), 3))
print('first_tx == first date:', round((m.first_tx == m['first']).mean(), 3),
      '| first_transaction_date:', round((m.first_transaction_date == m['first']).mean(), 3))
print('last_tx == last date:', round((m.last_tx == m['last']).mean(), 3),
      '| last_transaction_date:', round((m.last_transaction_date == m['last']).mean(), 3))
print('total_tx_volume == sum:', round(np.isclose(m.total_tx_volume, m.vol).mean(), 3),
      '| total_transaction_volume:', round(np.isclose(m.total_transaction_volume, m.vol).mean(), 3))
print('avg_tx_value == average_transaction_value:',
      round(np.isclose(c.avg_tx_value, c.average_transaction_value).mean(), 3))

section('Left-censoring: first transaction month and tenure')
print('first_tx by month:',
      pd.to_datetime(c.first_tx).dt.strftime('%Y-%m').value_counts().sort_index().to_dict())
print('customer_tenure range:', c.customer_tenure.min(), '-', c.customer_tenure.max())

section('Location (paper: values under 1% suppressed)')
dept = c.location.str.split(',').str[-1].str.strip()
share = dept.value_counts(normalize=True)
print('departments:', dept.nunique(), '| under 1%:', share[share < 0.01].round(4).to_dict())
print('Cundinamarca locations:', c.location[dept == 'Cundinamarca'].value_counts().to_dict())
