-- Clean the raw staging tables and load the normalised core tables (see 01_schema.sql).
-- Staging tables hold the source files exactly as delivered: every value as text, empty cells as ''.
-- Every rule below is listed, with row counts, in output/reports/cleaning_log.md.

-- ---------------------------------------------------------------------------------------------
-- Conformed dimensions (fixed reference data)
-- ---------------------------------------------------------------------------------------------

INSERT INTO gender (gender_id, gender_name) VALUES
    (1, 'Female'),
    (2, 'Male'),
    (3, 'Other');           -- only in the fintech file; Findex records female/male only

INSERT INTO education_group (education_group_id, education_group_name) VALUES
    (1, 'Primary or less'),
    (2, 'Secondary'),
    (3, 'Tertiary');        -- codes follow Findex educ, so respondents map one to one

-- ---------------------------------------------------------------------------------------------
-- Fintech dimensions
-- ---------------------------------------------------------------------------------------------

-- location arrives as one text field 'City, Department'. Department depends on city, not on the
-- customer, so storing both on the customer would break third normal form: split it out.
INSERT INTO department (department_name)
SELECT DISTINCT TRIM(SUBSTRING_INDEX(location, ',', -1)) AS department_name
FROM stg_customer
ORDER BY department_name;

INSERT INTO city (city_name, department_id)
SELECT DISTINCT TRIM(SUBSTRING_INDEX(s.location, ',', 1)) AS city_name, d.department_id
FROM stg_customer s
JOIN department d ON d.department_name = TRIM(SUBSTRING_INDEX(s.location, ',', -1))
ORDER BY city_name;

INSERT INTO acquisition_channel (channel_name)
SELECT DISTINCT TRIM(acquisition_channel) AS channel_name FROM stg_customer ORDER BY channel_name;

-- High School maps to Findex 'Secondary'; Bachelor, Master and PhD to 'Tertiary'.
INSERT INTO education_level (education_level_name, rank_order, education_group_id) VALUES
    ('High School', 1, 2),
    ('Bachelor',    2, 3),
    ('Master',      3, 3),
    ('PhD',         4, 3);

INSERT INTO transaction_type (transaction_type_name)
SELECT DISTINCT TRIM(type) AS transaction_type_name FROM stg_transaction ORDER BY transaction_type_name;

-- ---------------------------------------------------------------------------------------------
-- Customer: keep the 6 columns Story 1 needs, drop the other 48 (see column selection note)
-- ---------------------------------------------------------------------------------------------

INSERT INTO customer (customer_id, gender_id, city_id, education_level_id, channel_id, income_bracket)
SELECT CAST(s.customer_id AS UNSIGNED),
       g.gender_id,
       c.city_id,
       e.education_level_id,
       ch.channel_id,
       TRIM(s.income_bracket)
FROM stg_customer s
JOIN gender g               ON g.gender_name = TRIM(s.gender)
JOIN department d           ON d.department_name = TRIM(SUBSTRING_INDEX(s.location, ',', -1))
JOIN city c                 ON c.city_name = TRIM(SUBSTRING_INDEX(s.location, ',', 1))
                           AND c.department_id = d.department_id
JOIN education_level e      ON e.education_level_name = TRIM(s.education_level)
JOIN acquisition_channel ch ON ch.channel_name = TRIM(s.acquisition_channel);

-- ---------------------------------------------------------------------------------------------
-- Transactions: remove exact duplicate rows (group decision in Story 1), cast types.
-- The FK on customer_id rejects any transaction whose customer does not exist (none do).
-- ---------------------------------------------------------------------------------------------

INSERT INTO customer_transaction (customer_id, transaction_date, amount_cop, transaction_type_id)
SELECT CAST(d.customer_id AS UNSIGNED),
       CAST(d.date AS DATE),
       CAST(d.amount AS DECIMAL(14,0)),
       tt.transaction_type_id
FROM (SELECT DISTINCT customer_id, date, amount, type FROM stg_transaction) d
JOIN transaction_type tt ON tt.transaction_type_name = TRIM(d.type)
ORDER BY CAST(d.customer_id AS UNSIGNED), CAST(d.date AS DATE);

-- ---------------------------------------------------------------------------------------------
-- Findex: 1,000 respondents, 24 of 199 columns kept; survey codes recoded to 0/1 and NULL
-- Codes (World Bank codebook): 1 = yes, 2 = no, 3/8 = don't know, 4/9 = refused.
-- ---------------------------------------------------------------------------------------------

INSERT INTO survey_wave (economy_code, economy_name, survey_year, pop_adult)
SELECT DISTINCT economycode, economy, CAST(year AS UNSIGNED), CAST(pop_adult AS UNSIGNED)
FROM stg_findex;

INSERT INTO respondent (respondent_id, survey_wave_id, weight, gender_id, age, education_group_id,
                        income_quintile, is_rural, in_workforce, has_account, has_fin_account,
                        has_mobile_money, owns_mobile_phone, owns_smartphone, used_internet_3m)
SELECT CAST(f.wpid_random AS UNSIGNED),
       w.survey_wave_id,
       CAST(f.wgt AS DOUBLE),
       CASE f.female WHEN '1' THEN 1 WHEN '2' THEN 2 END,
       CAST(f.age AS UNSIGNED),
       CASE WHEN f.educ IN ('1', '2', '3') THEN CAST(f.educ AS UNSIGNED) END,   -- '' -> NULL
       CAST(f.inc_q AS UNSIGNED),
       CASE f.urbanicity WHEN '1' THEN 1 ELSE 0 END,
       CASE f.emp_in WHEN '1' THEN 1 ELSE 0 END,
       CAST(f.account AS UNSIGNED),
       CAST(f.account_fin AS UNSIGNED),
       CAST(f.account_mob AS UNSIGNED),
       CASE f.con1 WHEN '1' THEN 1 WHEN '2' THEN 0 END,
       -- con9 is only asked of phone owners: no phone means no smartphone; don't know/refused -> NULL
       CASE WHEN f.con9 = '1' THEN 1
            WHEN f.con9 = '2' OR f.con1 = '2' THEN 0 END,
       CAST(f.internet_use AS UNSIGNED)
FROM stg_findex f
JOIN survey_wave w ON w.economy_code = f.economycode AND w.survey_year = CAST(f.year AS UNSIGNED);

INSERT INTO account_barrier (findex_code, barrier_label) VALUES
    ('fin11a', 'Too far away'),
    ('fin11b', 'Too expensive'),
    ('fin11c', 'Lack of documents'),
    ('fin11d', 'Not enough money'),
    ('fin11e', 'Family member already has one'),
    ('fin11f', 'Lack of trust');

-- Wide to long: one row per respondent per barrier question they answered.
INSERT INTO respondent_barrier (respondent_id, barrier_id, answer)
SELECT r.respondent_id, b.barrier_id,
       CASE x.code WHEN '1' THEN 'Yes' WHEN '2' THEN 'No' WHEN '3' THEN 'Don''t know' WHEN '4' THEN 'Refused' END
FROM (
    SELECT wpid_random, 'fin11a' AS findex_code, fin11a AS code FROM stg_findex UNION ALL
    SELECT wpid_random, 'fin11b', fin11b FROM stg_findex UNION ALL
    SELECT wpid_random, 'fin11c', fin11c FROM stg_findex UNION ALL
    SELECT wpid_random, 'fin11d', fin11d FROM stg_findex UNION ALL
    SELECT wpid_random, 'fin11e', fin11e FROM stg_findex UNION ALL
    SELECT wpid_random, 'fin11f', fin11f FROM stg_findex
) x
JOIN respondent r      ON r.respondent_id = CAST(x.wpid_random AS UNSIGNED)
JOIN account_barrier b ON b.findex_code = x.findex_code
WHERE x.code IN ('1', '2', '3', '4');
