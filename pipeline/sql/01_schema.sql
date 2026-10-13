-- Logical database model for the Story 1 data journey (SQLite).
--
-- Three layers:
--   stg_*   raw staging copies of the three source files, every column TEXT (loaded by build_database.py)
--   core    normalised tables (this file), populated from staging by 02_transform.sql
--   mart    analytical tables built from core by 03_marts.sql (one row per customer, per customer-month, ...)
--
-- SQLite has no DATE or DECIMAL type: dates are stored as ISO-8601 TEXT ('YYYY-MM-DD'), money as INTEGER
-- (whole Colombian pesos, every amount in the source is an integer), booleans as INTEGER 0/1.
-- In a server database (PostgreSQL, SQL Server) these would be DATE, NUMERIC(14,0) and BOOLEAN.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------------------------
-- Shared (conformed) dimensions: the bridge between the fintech files and the Findex survey.
-- Both sources map their own codes onto these tables, which is what makes KPI 8 comparable.
-- ---------------------------------------------------------------------------------------------

CREATE TABLE gender (
    gender_id    INTEGER PRIMARY KEY,
    gender_name  TEXT NOT NULL UNIQUE                -- 'Female', 'Male', 'Other'
);

CREATE TABLE education_group (
    education_group_id    INTEGER PRIMARY KEY,       -- same codes as Findex educ: 1, 2, 3
    education_group_name  TEXT NOT NULL UNIQUE       -- 'Primary or less', 'Secondary', 'Tertiary'
);

-- ---------------------------------------------------------------------------------------------
-- Fintech: customers and transactions (Mendeley Data, Muñoz Guerrero et al., 2025)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE department (
    department_id    INTEGER PRIMARY KEY,
    department_name  TEXT NOT NULL UNIQUE
);

CREATE TABLE city (
    city_id        INTEGER PRIMARY KEY,
    city_name      TEXT NOT NULL,
    department_id  INTEGER NOT NULL REFERENCES department (department_id),
    UNIQUE (city_name, department_id)
);

CREATE TABLE acquisition_channel (
    channel_id    INTEGER PRIMARY KEY,
    channel_name  TEXT NOT NULL UNIQUE                -- 'Organic', 'Referral', 'Paid Ad', 'Partnership'
);

CREATE TABLE education_level (
    education_level_id    INTEGER PRIMARY KEY,
    education_level_name  TEXT NOT NULL UNIQUE,       -- 'High School', 'Bachelor', 'Master', 'PhD'
    rank_order            INTEGER NOT NULL,
    education_group_id    INTEGER NOT NULL REFERENCES education_group (education_group_id)
);

CREATE TABLE customer (
    customer_id               INTEGER PRIMARY KEY,    -- natural key from the source, 8 digits
    gender_id                 INTEGER NOT NULL REFERENCES gender (gender_id),
    city_id                   INTEGER NOT NULL REFERENCES city (city_id),
    education_level_id        INTEGER NOT NULL REFERENCES education_level (education_level_id),
    channel_id                INTEGER NOT NULL REFERENCES acquisition_channel (channel_id),
    income_bracket            TEXT NOT NULL CHECK (income_bracket IN ('Low', 'Medium', 'High', 'Very High'))
    -- Not kept: age (only six distinct values) and churn_probability (undocumented model output;
    -- check_kpis.py tests it against the raw staging copy as evidence for the exclusion).
);

CREATE TABLE transaction_type (
    transaction_type_id    INTEGER PRIMARY KEY,
    transaction_type_name  TEXT NOT NULL UNIQUE      -- 'Deposit', 'Payment', 'Transfer', 'Withdrawal'
);

-- "transaction" is an SQL keyword, hence customer_transaction.
-- The source has no transaction ID, so a surrogate key is added. (customer_id, date, amount, type) is not
-- declared UNIQUE: two identical transactions on one day are possible in principle (see cleaning log).
CREATE TABLE customer_transaction (
    transaction_id       INTEGER PRIMARY KEY,
    customer_id          INTEGER NOT NULL REFERENCES customer (customer_id),
    transaction_date     TEXT NOT NULL CHECK (transaction_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    amount_cop           INTEGER NOT NULL CHECK (amount_cop > 0),
    transaction_type_id  INTEGER NOT NULL REFERENCES transaction_type (transaction_type_id)
);

-- ---------------------------------------------------------------------------------------------
-- Findex: Global Findex 2025 microdata, Colombia (World Bank, fieldwork 2024)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE survey_wave (
    survey_wave_id  INTEGER PRIMARY KEY,
    economy_code    TEXT NOT NULL,                    -- 'COL'
    economy_name    TEXT NOT NULL,
    survey_year     INTEGER NOT NULL,
    pop_adult       INTEGER NOT NULL,                 -- adults 15+ the weights scale to (WDI 2023)
    UNIQUE (economy_code, survey_year)
);

CREATE TABLE respondent (
    respondent_id        INTEGER PRIMARY KEY,         -- wpid_random
    survey_wave_id       INTEGER NOT NULL REFERENCES survey_wave (survey_wave_id),
    weight               REAL NOT NULL CHECK (weight > 0),   -- wgt
    gender_id            INTEGER NOT NULL REFERENCES gender (gender_id),
    age                  INTEGER NOT NULL CHECK (age BETWEEN 15 AND 100),
    education_group_id   INTEGER REFERENCES education_group (education_group_id),   -- NULL = not stated
    income_quintile      INTEGER NOT NULL CHECK (income_quintile BETWEEN 1 AND 5),  -- inc_q, within Colombia
    is_rural             INTEGER NOT NULL CHECK (is_rural IN (0, 1)),               -- urbanicity 1 = rural
    in_workforce         INTEGER NOT NULL CHECK (in_workforce IN (0, 1)),           -- emp_in 1 = in workforce
    has_account          INTEGER NOT NULL CHECK (has_account IN (0, 1)),            -- account
    has_fin_account      INTEGER NOT NULL CHECK (has_fin_account IN (0, 1)),        -- account_fin
    has_mobile_money     INTEGER NOT NULL CHECK (has_mobile_money IN (0, 1)),       -- account_mob
    owns_mobile_phone    INTEGER CHECK (owns_mobile_phone IN (0, 1)),               -- con1; NULL = don't know/refused
    owns_smartphone      INTEGER CHECK (owns_smartphone IN (0, 1)),                 -- con9; 0 also when no phone
    used_internet_3m     INTEGER NOT NULL CHECK (used_internet_3m IN (0, 1))        -- internet_use
);

CREATE TABLE account_barrier (
    barrier_id     INTEGER PRIMARY KEY,
    findex_code    TEXT NOT NULL UNIQUE,              -- 'fin11a' ... 'fin11f'
    barrier_label  TEXT NOT NULL                      -- label from the World Bank codebook
);

-- fin11a-fin11f turned from six wide columns into rows (first normal form: no repeating group).
-- Only respondents without an account at a financial institution were asked, so only they have rows.
CREATE TABLE respondent_barrier (
    respondent_id  INTEGER NOT NULL REFERENCES respondent (respondent_id),
    barrier_id     INTEGER NOT NULL REFERENCES account_barrier (barrier_id),
    answer         TEXT NOT NULL CHECK (answer IN ('Yes', 'No', 'Don''t know', 'Refused')),
    PRIMARY KEY (respondent_id, barrier_id)
);
