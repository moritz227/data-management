-- Analytical tables ("marts") built from the core tables. They feed the KPI checks and the Tableau exports.
-- Definitions follow Story 1:
--   active in a month  = at least one transaction that calendar month
--   reference date     = last date in the transaction file (2023-12-29), not today
--   churned            = 60+ days since last transaction; at risk = 30-59 days; active = under 30 days
--   top 1%             = the 487 customers (1% of 48,723, rounded down) with the highest 2023 volume

DROP TABLE IF EXISTS mart_customer_month;
DROP TABLE IF EXISTS mart_customer_month_status;
DROP TABLE IF EXISTS mart_monthly_growth;
DROP TABLE IF EXISTS mart_customer_summary;
DROP TABLE IF EXISTS mart_cohort_retention;
DROP TABLE IF EXISTS mart_churn_threshold;
DROP TABLE IF EXISTS mart_findex_respondent;
DROP TABLE IF EXISTS mart_findex_funnel;
DROP TABLE IF EXISTS mart_barrier_share;
DROP TABLE IF EXISTS mart_profile_comparison;

-- ---------------------------------------------------------------------------------------------
-- Part A: customers and transactions
-- ---------------------------------------------------------------------------------------------

-- One row per customer per month in which they transacted.
CREATE TABLE mart_customer_month AS
SELECT customer_id,
       substr(transaction_date, 1, 7) || '-01' AS month,
       COUNT(*)                                 AS tx_count,
       SUM(amount_cop)                          AS volume_cop
FROM customer_transaction
GROUP BY customer_id, month;

CREATE UNIQUE INDEX ix_mcm ON mart_customer_month (customer_id, month);

-- Full grid: every customer x every month (48,723 x 12 rows), with a growth-accounting state.
-- January is the first month of data, so nobody can be "new" or "returning" there: state 'Baseline'.
CREATE TABLE mart_customer_month_status AS
WITH RECURSIVE months(month) AS (
    SELECT '2023-01-01'
    UNION ALL
    SELECT date(month, '+1 month') FROM months WHERE month < '2023-12-01'
),
firsts AS (
    SELECT customer_id, MIN(month) AS first_active_month FROM mart_customer_month GROUP BY customer_id
),
grid AS (
    SELECT c.customer_id, m.month, f.first_active_month,
           CASE WHEN cm.customer_id IS NULL THEN 0 ELSE 1 END AS is_active,
           COALESCE(cm.tx_count, 0)   AS tx_count,
           COALESCE(cm.volume_cop, 0) AS volume_cop
    FROM customer c
    CROSS JOIN months m
    JOIN firsts f ON f.customer_id = c.customer_id
    LEFT JOIN mart_customer_month cm ON cm.customer_id = c.customer_id AND cm.month = m.month
),
lagged AS (
    SELECT *, LAG(is_active) OVER (PARTITION BY customer_id ORDER BY month) AS was_active_prev
    FROM grid
)
SELECT customer_id, month, first_active_month, is_active, tx_count, volume_cop,
       CASE
           WHEN month = '2023-01-01' THEN CASE WHEN is_active = 1 THEN 'Baseline' ELSE 'Not yet active' END
           WHEN month < first_active_month              THEN 'Not yet active'
           WHEN month = first_active_month              THEN 'New'
           WHEN is_active = 1 AND was_active_prev = 1   THEN 'Retained'
           WHEN is_active = 1 AND was_active_prev = 0   THEN 'Returning'
           WHEN is_active = 0 AND was_active_prev = 1   THEN 'Lost'
           ELSE 'Dormant'
       END AS growth_state
FROM lagged;

-- KPI 1 and KPI 2: one row per month.
CREATE TABLE mart_monthly_growth AS
SELECT month,
       SUM(is_active)                                  AS active_customers,
       SUM(growth_state = 'New')                       AS new_customers,
       SUM(growth_state = 'Retained')                  AS retained_customers,
       SUM(growth_state = 'Returning')                 AS returning_customers,
       SUM(growth_state = 'Lost')                      AS lost_customers,
       CASE WHEN SUM(growth_state = 'Lost') > 0
            THEN ROUND(1.0 * (SUM(growth_state = 'New') + SUM(growth_state = 'Returning'))
                           / SUM(growth_state = 'Lost'), 4) END AS quick_ratio,
       SUM(tx_count)                                   AS transactions,
       SUM(volume_cop)                                 AS volume_cop
FROM mart_customer_month_status
GROUP BY month
ORDER BY month;

-- KPIs 3, 4 and 5: one row per customer.
CREATE TABLE mart_customer_summary AS
WITH ref AS (SELECT MAX(transaction_date) AS ref_date FROM customer_transaction),
totals AS (
    SELECT customer_id,
           MIN(transaction_date)            AS first_tx_date,
           MAX(transaction_date)            AS last_tx_date,
           COUNT(*)                         AS tx_count,
           COUNT(DISTINCT transaction_date) AS active_days,
           SUM(amount_cop)                  AS volume_cop
    FROM customer_transaction
    GROUP BY customer_id
),
days AS (SELECT DISTINCT customer_id, transaction_date AS d FROM customer_transaction),
gaps AS (
    SELECT customer_id,
           julianday(d) - julianday(LAG(d) OVER (PARTITION BY customer_id ORDER BY d)) AS gap
    FROM days
),
ranked_gaps AS (
    SELECT customer_id, gap,
           ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY gap) AS rn,
           COUNT(*)     OVER (PARTITION BY customer_id)              AS n
    FROM gaps
    WHERE gap IS NOT NULL
),
median_gap AS (
    SELECT customer_id, AVG(gap) AS median_gap_days
    FROM ranked_gaps
    WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
    GROUP BY customer_id
),
ranked AS (
    SELECT t.*,
           CAST(julianday(ref.ref_date) - julianday(t.last_tx_date) AS INTEGER) AS days_since_last_tx,
           ROW_NUMBER() OVER (ORDER BY t.volume_cop DESC, t.customer_id) AS volume_rank,
           COUNT(*)     OVER ()                                           AS n_customers
    FROM totals t CROSS JOIN ref
)
SELECT r.customer_id, r.first_tx_date, r.last_tx_date, r.days_since_last_tx,
       r.tx_count, r.active_days, r.volume_cop, mg.median_gap_days,
       CASE WHEN r.days_since_last_tx >= 60 THEN 'Churned (60+ days)'
            WHEN r.days_since_last_tx >= 30 THEN 'At risk (30-59 days)'
            ELSE 'Active (under 30 days)' END                     AS status_60d,
       r.volume_rank,
       ROUND(1.0 * r.volume_rank / r.n_customers, 6)              AS volume_rank_pct,
       CASE WHEN r.volume_rank <= r.n_customers / 100 THEN 'Top 1%'
            WHEN r.volume_rank <= r.n_customers / 10  THEN 'Next 9%'
            ELSE 'Bottom 90%' END                                 AS value_tier
FROM ranked r
LEFT JOIN median_gap mg ON mg.customer_id = r.customer_id;

CREATE UNIQUE INDEX ix_mcs ON mart_customer_summary (customer_id);

-- Cohort retention curve (brief, Retention section): cohort = first month with a transaction.
-- The January cohort is left-censored: it mixes genuinely new customers with existing ones.
CREATE TABLE mart_cohort_retention AS
WITH cohort_size AS (
    SELECT first_active_month AS cohort_month, COUNT(DISTINCT customer_id) AS cohort_customers
    FROM mart_customer_month_status
    GROUP BY first_active_month
)
SELECT s.first_active_month                                                     AS cohort_month,
       CAST(strftime('%m', s.month) AS INTEGER)
           - CAST(strftime('%m', s.first_active_month) AS INTEGER)              AS months_since_first,  -- all 2023
       cs.cohort_customers,
       SUM(s.is_active)                                                         AS active_customers,
       ROUND(1.0 * SUM(s.is_active) / cs.cohort_customers, 4)                   AS retention_rate
FROM mart_customer_month_status s
JOIN cohort_size cs ON cs.cohort_month = s.first_active_month
WHERE s.month >= s.first_active_month
GROUP BY s.first_active_month, s.month
ORDER BY 1, 2;

-- Sensitivity of the churn rate to the silence threshold (KPI 3 justification).
CREATE TABLE mart_churn_threshold AS
WITH RECURSIVE weekly(days) AS (
    SELECT 7 UNION ALL SELECT days + 7 FROM weekly WHERE days < 119
),
thresholds(days) AS (
    SELECT days FROM weekly UNION SELECT 30 UNION SELECT 60 UNION SELECT 90
)
SELECT t.days                                                     AS threshold_days,
       SUM(s.days_since_last_tx >= t.days)                        AS churned_customers,
       ROUND(1.0 * SUM(s.days_since_last_tx >= t.days) / COUNT(*), 4) AS churn_rate
FROM thresholds t CROSS JOIN mart_customer_summary s
GROUP BY t.days
ORDER BY t.days;

-- ---------------------------------------------------------------------------------------------
-- Part B: Findex survey (all shares weighted with wgt; population counts scaled to pop_adult)
-- ---------------------------------------------------------------------------------------------

-- One row per respondent with readable labels and the number of Colombian adults they stand for.
CREATE TABLE mart_findex_respondent AS
WITH tot AS (SELECT SUM(weight) AS w FROM respondent)
SELECT r.respondent_id,
       r.weight,
       r.weight / tot.w * sw.pop_adult                          AS adults_represented,
       g.gender_name                                            AS gender,
       r.age,
       COALESCE(eg.education_group_name, 'Not stated')          AS education_group,
       r.income_quintile,
       CASE r.is_rural WHEN 1 THEN 'Rural' ELSE 'Urban' END     AS area,
       r.in_workforce, r.has_account, r.has_fin_account, r.has_mobile_money,
       r.owns_mobile_phone, r.owns_smartphone, r.used_internet_3m,
       CASE WHEN r.has_account = 0 THEN 1 ELSE 0 END            AS is_unbanked,
       CASE WHEN r.has_account = 0 AND r.owns_smartphone = 1 AND r.used_internet_3m = 1
            THEN 1 ELSE 0 END                                   AS is_reachable_unbanked
FROM respondent r
CROSS JOIN tot
JOIN survey_wave sw          ON sw.survey_wave_id = r.survey_wave_id
JOIN gender g                ON g.gender_id = r.gender_id
LEFT JOIN education_group eg ON eg.education_group_id = r.education_group_id;

-- KPI 6: the reachable-market funnel.
CREATE TABLE mart_findex_funnel AS
SELECT 1 AS step, 'All adults (15+)' AS label, COUNT(*) AS respondents,
       SUM(adults_represented) AS adults
FROM mart_findex_respondent
UNION ALL
SELECT 2, 'Have no account', COUNT(*), SUM(adults_represented)
FROM mart_findex_respondent WHERE has_account = 0
UNION ALL
SELECT 3, '...and own a smartphone', COUNT(*), SUM(adults_represented)
FROM mart_findex_respondent WHERE has_account = 0 AND owns_smartphone = 1
UNION ALL
SELECT 4, '...and used the internet (past 3 months)', COUNT(*), SUM(adults_represented)
FROM mart_findex_respondent WHERE is_reachable_unbanked = 1;

-- KPI 7: share answering 'Yes' to each barrier among those asked (no account at a financial institution).
CREATE TABLE mart_barrier_share AS
SELECT b.findex_code, b.barrier_label,
       COUNT(*)                                                                  AS respondents_asked,
       ROUND(SUM(CASE WHEN rb.answer = 'Yes' THEN r.weight ELSE 0 END) / SUM(r.weight), 4) AS share_yes,
       SUM(CASE WHEN rb.answer = 'Yes' THEN r.adults_represented ELSE 0 END)     AS adults_yes
FROM respondent_barrier rb
JOIN account_barrier b         ON b.barrier_id = rb.barrier_id
JOIN mart_findex_respondent r  ON r.respondent_id = rb.respondent_id
GROUP BY b.barrier_id
ORDER BY share_yes DESC;

-- KPI 8: the integration step. Customers (unweighted counts) and Findex groups (weighted) side by side,
-- on the two traits both sources record comparably: gender and education group.
CREATE TABLE mart_profile_comparison AS
WITH customers AS (
    SELECT g.gender_name AS gender, eg.education_group_name AS education_group
    FROM customer c
    JOIN gender g           ON g.gender_id = c.gender_id
    JOIN education_level el ON el.education_level_id = c.education_level_id
    JOIN education_group eg ON eg.education_group_id = el.education_group_id
),
findex AS (
    SELECT 'Colombian adults' AS population, 2 AS population_order, * FROM mart_findex_respondent
    UNION ALL
    SELECT 'Unbanked adults', 3, * FROM mart_findex_respondent WHERE is_unbanked = 1
    UNION ALL
    SELECT 'Reachable unbanked', 4, * FROM mart_findex_respondent WHERE is_reachable_unbanked = 1
)
SELECT 'Fintech customers' AS population, 1 AS population_order, 'Gender' AS trait, gender AS category,
       COUNT(*) AS base_n, ROUND(1.0 * COUNT(*) / (SELECT COUNT(*) FROM customers), 4) AS share
FROM customers GROUP BY gender
UNION ALL
SELECT 'Fintech customers', 1, 'Education', education_group,
       COUNT(*), ROUND(1.0 * COUNT(*) / (SELECT COUNT(*) FROM customers), 4)
FROM customers GROUP BY education_group
UNION ALL
SELECT population, population_order, 'Gender', gender, COUNT(*),
       ROUND(SUM(weight) / (SELECT SUM(weight) FROM findex f2 WHERE f2.population = f.population), 4)
FROM findex f GROUP BY population, gender
UNION ALL
SELECT population, population_order, 'Education', education_group, COUNT(*),
       ROUND(SUM(weight) / (SELECT SUM(weight) FROM findex f2 WHERE f2.population = f.population), 4)
FROM findex f GROUP BY population, education_group
UNION ALL
SELECT population, population_order, 'Area', area, COUNT(*),
       ROUND(SUM(weight) / (SELECT SUM(weight) FROM findex f2 WHERE f2.population = f.population), 4)
FROM findex f GROUP BY population, area
ORDER BY population_order, trait, category;
