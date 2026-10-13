"""Recalculate the eight Story 1 KPIs from output/fintech.db and compare them with the numbers in the Story 1 doc.

Also runs the data checks behind the Story 1 data notes (customer-file reconciliation, churn_probability AUC,
minimum activity per customer) and writes everything to output/reports/kpi_check.md.

Run from the repository root after build_database.py: .venv/bin/python pipeline/check_kpis.py
"""
import sqlite3
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'output' / 'fintech.db'
REPORT = ROOT / 'output' / 'reports' / 'kpi_check.md'


def to_markdown(df, index=True, digits=3):
    """Markdown table without the optional tabulate dependency."""
    if index:
        df = df.reset_index()
    fmt = lambda x: f'{x:,.{digits}f}' if isinstance(x, float) else (f'{x:,}' if isinstance(x, int) else str(x))
    rows = ['| ' + ' | '.join(map(str, df.columns)) + ' |', '|' + '---|' * df.shape[1]]
    rows += ['| ' + ' | '.join(fmt(x) for x in r) + ' |' for r in df.astype(object).itertuples(index=False)]
    return '\n'.join(rows)


def auc(scores, labels):
    """Area under the ROC curve via the Mann-Whitney rank formula (0.5 = no better than chance)."""
    ranks = scores.rank()
    pos = labels.sum()
    neg = len(labels) - pos
    return (ranks[labels == 1].sum() - pos * (pos + 1) / 2) / (pos * neg)


def top_share_excluding_heavy(summary, max_tx=1000):
    """Sensitivity for KPI 5: re-rank without possible business accounts (over max_tx transactions a year)."""
    rest = summary[summary.tx_count <= max_tx].sort_values('volume_cop', ascending=False)
    return rest.volume_cop.head(len(rest) // 100).sum() / rest.volume_cop.sum()


def main():
    con = sqlite3.connect(DB)
    q = lambda sql: pd.read_sql_query(sql, con)
    v = lambda sql: con.execute(sql).fetchone()[0]

    growth = q('SELECT * FROM mart_monthly_growth ORDER BY month')
    summary = q('SELECT * FROM mart_customer_summary')
    n_customers = len(summary)
    total_volume = summary.volume_cop.sum()
    funnel = q('SELECT * FROM mart_findex_funnel ORDER BY step').set_index('step')
    barriers = q('SELECT * FROM mart_barrier_share').set_index('findex_code')
    profile = q('SELECT * FROM mart_profile_comparison')
    share = lambda pop, trait, cat: profile.query(
        'population == @pop and trait == @trait and category == @cat').share.sum()
    resp = q('SELECT * FROM mart_findex_respondent')
    wshare = lambda df, col: (df[col] * df.weight).sum() / df.weight.sum()

    # Part A
    mau = growth.active_customers
    qr = growth.quick_ratio.dropna()
    churned = summary.status_60d.str.startswith('Churned')
    at_risk = summary.status_60d.str.startswith('At risk')
    active = summary.status_60d.str.startswith('Active')
    top = summary.value_tier == 'Top 1%'
    bottom90 = summary.value_tier == 'Bottom 90%'
    tickets = q('SELECT amount_cop FROM customer_transaction').amount_cop
    threshold = q('SELECT * FROM mart_churn_threshold').set_index('threshold_days')

    # Part B
    reach = resp[resp.is_reachable_unbanked == 1]
    women = resp[resp.gender == 'Female']
    men = resp[resp.gender == 'Male']

    checks = [
        # (KPI, measure, Story 1 value, recalculated value, format)
        ('1', 'Average monthly active customers', 38977, mau.mean(), '{:,.0f}'),
        ('1', 'MAU as share of all customers', 0.80, mau.mean() / n_customers, '{:.1%}'),
        ('1', 'Lowest monthly MAU', 38019, mau.min(), '{:,.0f}'),
        ('1', 'Highest monthly MAU', 39513, mau.max(), '{:,.0f}'),
        ('2', 'Average monthly Quick Ratio (Feb-Dec)', 1.01, qr.mean(), '{:.2f}'),
        ('2', 'Lowest monthly Quick Ratio', 0.92, qr.min(), '{:.2f}'),
        ('2', 'Highest monthly Quick Ratio', 1.21, qr.max(), '{:.2f}'),
        ('2', 'Average customers lost per month', 7000, growth.lost_customers[1:].mean(), '{:,.0f}'),
        ('2', 'New customers in July', 8, growth.set_index('month').new_customers['2023-07-01'], '{:,.0f}'),
        ('2', 'New customers in August', 1, growth.set_index('month').new_customers['2023-08-01'], '{:,.0f}'),
        ('2', 'New customers September-December', 0, growth.new_customers[8:].sum(), '{:,.0f}'),
        ('3', 'Churned customers (60+ days)', 2736, churned.sum(), '{:,.0f}'),
        ('3', '60-day churn rate', 0.056, churned.mean(), '{:.1%}'),
        ('3', '30-day churn rate', 0.200, threshold.churn_rate[30], '{:.1%}'),
        ('3', '90-day churn rate', 0.016, threshold.churn_rate[90], '{:.1%}'),
        ('3', 'Median of customers\' median gap (days)', 13, summary.median_gap_days.median(), '{:.1f}'),
        ('3', 'Customers whose median gap is 30 days or less', 0.95,
         (summary.median_gap_days <= 30).mean(), '{:.1%}'),
        ('3', 'Customers whose median gap is 60+ days', 0, (summary.median_gap_days >= 60).sum(), '{:,.0f}'),
        ('4', 'At-risk customers (30-59 days)', 7019, at_risk.sum(), '{:,.0f}'),
        ('4', 'At-risk share of customers', 0.144, at_risk.mean(), '{:.1%}'),
        ('4', 'At-risk share of 2023 volume', 0.030, summary.volume_cop[at_risk].sum() / total_volume, '{:.1%}'),
        ('4', 'Transactions per at-risk customer', 15, summary.tx_count[at_risk].mean(), '{:.1f}'),
        ('4', 'Transactions per active customer', 77, summary.tx_count[active].mean(), '{:.1f}'),
        ('4', 'Top-1% customers who are at risk', 0, (top & at_risk).sum(), '{:,.0f}'),
        ('5', 'Customers in the top 1%', 487, top.sum(), '{:,.0f}'),
        ('5', 'Top-1% share of volume', 0.530, summary.volume_cop[top].sum() / total_volume, '{:.1%}'),
        ('5', 'Total volume (COP trillion)', 12.5, total_volume / 1e12, '{:.1f}'),
        ('5', 'Top-1% mean volume / bottom-90% mean volume', 212,
         summary.volume_cop[top].mean() / summary.volume_cop[bottom90].mean(), '{:.0f}'),
        ('5', 'Average ticket (COP million)', 3.97, tickets.mean() / 1e6, '{:.2f}'),
        ('5', 'Median ticket (COP million)', 1.79, tickets.median() / 1e6, '{:.2f}'),
        ('6', 'Adults without an account (million)', 17.8, funnel.adults[2] / 1e6, '{:.1f}'),
        ('6', 'Share of adults without an account', 0.429, funnel.adults[2] / funnel.adults[1], '{:.1%}'),
        ('6', '...and own a smartphone (million; 25.37% x 41.54m = 10.54m)', 10.6, funnel.adults[3] / 1e6, '{:.1f}'),
        ('6', 'Reachable unbanked adults (million)', 9.8, funnel.adults[4] / 1e6, '{:.1f}'),
        ('6', 'Reachable unbanked share of adults', 0.236, funnel.adults[4] / funnel.adults[1], '{:.1%}'),
        ('6', 'Reachable respondents (unweighted)', 228, funnel.respondents[4], '{:,.0f}'),
        ('6', 'Customers acquired by referral', 14546,
         v("SELECT COUNT(*) FROM customer JOIN acquisition_channel USING (channel_id) "
           "WHERE channel_name = 'Referral'"), '{:,.0f}'),
        ('7', 'Respondents asked the barrier questions', 585, barriers.respondents_asked.max(), '{:,.0f}'),
        ('7', 'Too expensive (fin11b)', 0.641, barriers.share_yes['fin11b'], '{:.1%}'),
        ('7', 'Too far away (fin11a)', 0.395, barriers.share_yes['fin11a'], '{:.1%}'),
        ('7', 'Lack of trust (fin11f per codebook)', 0.341, barriers.share_yes['fin11f'],
         '{:.1%}'),
        ('7', 'Not enough money (fin11d per codebook)', 0.336, barriers.share_yes['fin11d'], '{:.1%}'),
        ('7', 'Lack of documents (fin11c)', 0.329, barriers.share_yes['fin11c'], '{:.1%}'),
        ('7', 'Family member has one (fin11e)', 0.381, barriers.share_yes['fin11e'], '{:.1%}'),
        ('8', 'Customers with a university degree', 0.605, share('Fintech customers', 'Education', 'Tertiary'),
         '{:.1%}'),
        ('8', 'Reachable unbanked with tertiary education', 0.029,
         share('Reachable unbanked', 'Education', 'Tertiary'), '{:.1%}'),
        ('8', 'Women among customers', 0.493, share('Fintech customers', 'Gender', 'Female'), '{:.1%}'),
        ('8', 'Women among reachable unbanked', 0.587, share('Reachable unbanked', 'Gender', 'Female'), '{:.1%}'),
        ('8', 'Reachable unbanked living in rural areas', 0.362, share('Reachable unbanked', 'Area', 'Rural'),
         '{:.1%}'),
        ('8', 'Women with any account', 0.510, wshare(women, 'has_account'), '{:.1%}'),
        ('8', 'Men with any account', 0.637, wshare(men, 'has_account'), '{:.1%}'),
    ]

    def status(story, actual, fmt):
        if story is None:
            return 'not in Story 1'
        if fmt.format(story) == fmt.format(actual):
            return 'match'
        # Story 1 rounds some figures ("about 7,000"): within 2% counts as a rounding difference.
        return 'rounded' if abs(actual - story) <= 0.02 * abs(story) else 'CHECK'

    lines = ['# KPI check against Story 1', '',
             f'Generated by `pipeline/check_kpis.py` on {time.strftime("%Y-%m-%d")} from `output/fintech.db`. '
             '"match" means the recalculated value rounds to the Story 1 figure.', '',
             '| KPI | Measure | Story 1 | Recalculated | Status |', '|---|---|---:|---:|---|']
    for kpi, measure, story, actual, fmt in checks:
        story_txt = '' if story is None else fmt.format(story)
        lines.append(f'| {kpi} | {measure} | {story_txt} | {fmt.format(actual)} | {status(story, actual, fmt)} |')

    # ------------------------------------------------------------------ data-note checks
    cust = q('''SELECT s.customer_id, s.last_transaction_date, s.first_transaction_date, s.tx_count,
                       s.customer_segment, s.churn_probability, s.nps_score, s.age, s.clv_segment
                FROM stg_customer s''')
    cust['customer_id'] = cust.customer_id.astype(int)
    m = cust.merge(summary, on='customer_id', suffixes=('_src', ''))
    last_match = (m.last_transaction_date == m.last_tx_date).mean()
    first_match = (m.first_transaction_date == m.first_tx_date).mean()
    count_match = (m.tx_count_src.astype(int) == m.tx_count).mean()
    churn_auc = auc(m.churn_probability.astype(float), m.status_60d.str.startswith('Churned').astype(int))
    min_tx = summary.tx_count.min()
    at_min = (summary.tx_count == min_tx).sum()
    seg = m.groupby('customer_segment').agg(customers=('customer_id', 'size'),
                                            median_tx=('tx_count', 'median'),
                                            churned=('status_60d', lambda s: s.str.startswith('Churned').mean()))
    nps = m.nps_score.astype(int)
    first_month = q('SELECT substr(first_tx_date, 1, 7) AS month, COUNT(*) AS customers '
                    'FROM mart_customer_summary GROUP BY month ORDER BY month')

    lines += ['', '## Data-note checks', '',
              '| Check | Result |', '|---|---|',
              f'| Customer file `last_transaction_date` equals last date in transactions | {last_match:.1%} of customers '
              f'(Story 1: disagrees for 96.4%) |',
              f'| Customer file `first_transaction_date` equals first date in transactions | {first_match:.1%} |',
              f'| Customer file `tx_count` equals transactions after de-duplication | {count_match:.2%} '
              '(the mismatches are exactly the customers with duplicate rows: `tx_count` counted them) |',
              f'| AUC of `churn_probability` against observed 60-day churn | {churn_auc:.3f} (Story 1: 0.51) |',
              f'| Fewest transactions any customer has | {min_tx} ({at_min:,} customers have exactly {min_tx}) |',
              f'| `nps_score` range | {nps.min()} to {nps.max()} (a single person\'s NPS answer is 0-10, '
              'so this is not a raw survey answer) |',
              f'| Distinct ages in customer file | {sorted(int(a) for a in m.age.unique())} |',
              f'| Largest single customer: share of all volume | {summary.volume_cop.max() / total_volume:.1%} '
              f'({summary.tx_count[summary.volume_cop.idxmax()]:,} transactions, '
              f'{summary.tx_count[summary.volume_cop.idxmax()] / 360:.0f} a day) |',
              f'| Customers with more than 1,000 transactions in 2023 (about 3 a day) | '
              f'{(summary.tx_count > 1000).sum():,}, together {summary.volume_cop[summary.tx_count > 1000].sum() / total_volume:.1%} of volume |',
              f'| Top-1% share of volume without customers over 1,000 transactions | '
              f'{top_share_excluding_heavy(summary):.1%} |',
              f'| `clv_segment` group sizes | {m.clv_segment.value_counts().to_dict()} (exact quartiles) |',
              '', '### Company segment label vs behaviour', '',
              to_markdown(seg), '',
              '### Customers by month of first transaction', '',
              to_markdown(first_month, index=False), '',
              '### Monthly growth accounting', '',
              to_markdown(growth, index=False, digits=2), '']
    REPORT.write_text('\n'.join(lines), encoding='utf-8')
    con.close()
    print(f'Written {REPORT}')
    print('\n'.join(lines[6:6 + len(checks)]))


if __name__ == '__main__':
    main()
