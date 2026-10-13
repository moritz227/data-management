"""Write the churned customers to churned_customers.csv.

A customer counts as churned when their last transaction in transactions_data.csv
is at least 60 days before the reference date. The data ends in December 2023, so
the reference date is the latest transaction date in the file, not today.
Only transactions_data.csv is used; the dates and churn fields in customer_data.csv
are unreliable and ignored.

The CSV has one row per churned customer (customer_id, last_transaction_date,
last_transaction_month as 1-12), followed by a final row with the total number of
churned customers.

Run: uv run --no-project --with pandas python churned_customers.py
"""
import csv
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent
OUTPUT = DATA / 'churned_customers.csv'
CHURN_DAYS = 60

t = pd.read_csv(DATA / 'transactions_data.csv', usecols=['customer_id', 'date'], parse_dates=['date'])

reference_date = t.date.max()
last_tx = t.groupby('customer_id').date.max()
days_since = (reference_date - last_tx).dt.days

churned = last_tx[days_since >= CHURN_DAYS]

with open(OUTPUT, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['customer_id', 'last_transaction_date', 'last_transaction_month'])
    for customer_id, date in churned.items():
        writer.writerow([customer_id, date.date().isoformat(), date.month])
    writer.writerow(['total_churned', len(churned), ''])

print(f'{len(churned)} churned customers written to {OUTPUT}')
