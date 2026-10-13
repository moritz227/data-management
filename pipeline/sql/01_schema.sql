-- Logical database model for the Story 1 data journey (MySQL 8.0 or later).
--
-- Three layers:
--   stg_*   raw staging copies of the three source files, every value as text (loaded by build_database.py)
--   core    normalised tables (this file), populated from staging by 02_transform.sql
--   mart    analytical tables built from core by 03_marts.sql (one row per customer, per customer-month, ...)
--
-- Types: dates are DATE, money is DECIMAL(14,0) (whole Colombian pesos; every source amount is an integer),
-- yes/no flags are BOOLEAN (TINYINT(1) in MySQL) with a CHECK limiting them to 0/1.
-- The database uses utf8mb4 with an accent- and case-sensitive collation, so 'Bogotá' <> 'Bogota'.

-- ---------------------------------------------------------------------------------------------
-- Shared (conformed) dimensions: the bridge between the fintech files and the Findex survey.
-- Both sources map their own codes onto these tables, which is what makes KPI 8 comparable.
-- ---------------------------------------------------------------------------------------------

CREATE TABLE gender (
    gender_id    TINYINT UNSIGNED PRIMARY KEY,
    gender_name  VARCHAR(20) NOT NULL UNIQUE                -- 'Female', 'Male', 'Other'
);

CREATE TABLE education_group (
    education_group_id    TINYINT UNSIGNED PRIMARY KEY,     -- same codes as Findex educ: 1, 2, 3
    education_group_name  VARCHAR(30) NOT NULL UNIQUE       -- 'Primary or less', 'Secondary', 'Tertiary'
);

-- ---------------------------------------------------------------------------------------------
-- Fintech: customers and transactions (Mendeley Data, Muñoz Guerrero et al., 2025)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE department (
    department_id    SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    department_name  VARCHAR(60) NOT NULL UNIQUE
);

CREATE TABLE city (
    city_id        SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    city_name      VARCHAR(60) NOT NULL,
    department_id  SMALLINT UNSIGNED NOT NULL,
    UNIQUE (city_name, department_id),
    FOREIGN KEY (department_id) REFERENCES department (department_id)
);

CREATE TABLE acquisition_channel (
    channel_id    TINYINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    channel_name  VARCHAR(30) NOT NULL UNIQUE                -- 'Organic', 'Referral', 'Paid Ad', 'Partnership'
);

CREATE TABLE education_level (
    education_level_id    TINYINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    education_level_name  VARCHAR(30) NOT NULL UNIQUE,       -- 'High School', 'Bachelor', 'Master', 'PhD'
    rank_order            TINYINT UNSIGNED NOT NULL,
    education_group_id    TINYINT UNSIGNED NOT NULL,
    FOREIGN KEY (education_group_id) REFERENCES education_group (education_group_id)
);

CREATE TABLE customer (
    customer_id         INT UNSIGNED PRIMARY KEY,             -- natural key from the source, 8 digits
    gender_id           TINYINT UNSIGNED NOT NULL,
    city_id             SMALLINT UNSIGNED NOT NULL,
    education_level_id  TINYINT UNSIGNED NOT NULL,
    channel_id          TINYINT UNSIGNED NOT NULL,
    income_bracket      VARCHAR(10) NOT NULL CHECK (income_bracket IN ('Low', 'Medium', 'High', 'Very High')),
    -- Not kept: age (only six distinct values) and churn_probability (undocumented model output;
    -- check_kpis.py tests it against the raw staging copy as evidence for the exclusion).
    FOREIGN KEY (gender_id)          REFERENCES gender (gender_id),
    FOREIGN KEY (city_id)            REFERENCES city (city_id),
    FOREIGN KEY (education_level_id) REFERENCES education_level (education_level_id),
    FOREIGN KEY (channel_id)         REFERENCES acquisition_channel (channel_id)
);

CREATE TABLE transaction_type (
    transaction_type_id    TINYINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    transaction_type_name  VARCHAR(20) NOT NULL UNIQUE      -- 'Deposit', 'Payment', 'Transfer', 'Withdrawal'
);

-- "transaction" is an SQL keyword, hence customer_transaction.
-- The source has no transaction ID, so a surrogate key is added. (customer_id, date, amount, type) is not
-- declared UNIQUE: two identical transactions on one day are possible in principle (see cleaning log).
CREATE TABLE customer_transaction (
    transaction_id       INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    customer_id          INT UNSIGNED NOT NULL,
    transaction_date     DATE NOT NULL,
    amount_cop           DECIMAL(14,0) NOT NULL CHECK (amount_cop > 0),
    transaction_type_id  TINYINT UNSIGNED NOT NULL,
    INDEX ix_transaction_customer_date (customer_id, transaction_date),
    INDEX ix_transaction_date (transaction_date),
    FOREIGN KEY (customer_id)         REFERENCES customer (customer_id),
    FOREIGN KEY (transaction_type_id) REFERENCES transaction_type (transaction_type_id)
);

-- ---------------------------------------------------------------------------------------------
-- Findex: Global Findex 2025 microdata, Colombia (World Bank, fieldwork 2024)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE survey_wave (
    survey_wave_id  SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    economy_code    CHAR(3) NOT NULL,                        -- 'COL'
    economy_name    VARCHAR(60) NOT NULL,
    survey_year     SMALLINT UNSIGNED NOT NULL,
    pop_adult       INT UNSIGNED NOT NULL,                   -- adults 15+ the weights scale to (WDI 2023)
    UNIQUE (economy_code, survey_year)
);

CREATE TABLE respondent (
    respondent_id       INT UNSIGNED PRIMARY KEY,            -- wpid_random
    survey_wave_id      SMALLINT UNSIGNED NOT NULL,
    weight              DOUBLE NOT NULL CHECK (weight > 0),   -- wgt
    gender_id           TINYINT UNSIGNED NOT NULL,
    age                 TINYINT UNSIGNED NOT NULL CHECK (age BETWEEN 15 AND 100),
    education_group_id  TINYINT UNSIGNED NULL,               -- NULL = not stated
    income_quintile     TINYINT UNSIGNED NOT NULL CHECK (income_quintile BETWEEN 1 AND 5),  -- inc_q
    is_rural            BOOLEAN NOT NULL CHECK (is_rural IN (0, 1)),            -- urbanicity 1 = rural
    in_workforce        BOOLEAN NOT NULL CHECK (in_workforce IN (0, 1)),        -- emp_in 1 = in workforce
    has_account         BOOLEAN NOT NULL CHECK (has_account IN (0, 1)),         -- account
    has_fin_account     BOOLEAN NOT NULL CHECK (has_fin_account IN (0, 1)),     -- account_fin
    has_mobile_money    BOOLEAN NOT NULL CHECK (has_mobile_money IN (0, 1)),    -- account_mob
    owns_mobile_phone   BOOLEAN NULL CHECK (owns_mobile_phone IN (0, 1)),       -- con1; NULL = don't know/refused
    owns_smartphone     BOOLEAN NULL CHECK (owns_smartphone IN (0, 1)),         -- con9; 0 also when no phone
    used_internet_3m    BOOLEAN NOT NULL CHECK (used_internet_3m IN (0, 1)),    -- internet_use
    FOREIGN KEY (survey_wave_id)     REFERENCES survey_wave (survey_wave_id),
    FOREIGN KEY (gender_id)          REFERENCES gender (gender_id),
    FOREIGN KEY (education_group_id) REFERENCES education_group (education_group_id)
);

CREATE TABLE account_barrier (
    barrier_id     TINYINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    findex_code    VARCHAR(10) NOT NULL UNIQUE,              -- 'fin11a' ... 'fin11f'
    barrier_label  VARCHAR(60) NOT NULL                      -- label from the World Bank codebook
);

-- fin11a-fin11f turned from six wide columns into rows (first normal form: no repeating group).
-- Only respondents without an account at a financial institution were asked, so only they have rows.
CREATE TABLE respondent_barrier (
    respondent_id  INT UNSIGNED NOT NULL,
    barrier_id     TINYINT UNSIGNED NOT NULL,
    answer         VARCHAR(12) NOT NULL CHECK (answer IN ('Yes', 'No', 'Don''t know', 'Refused')),
    PRIMARY KEY (respondent_id, barrier_id),
    FOREIGN KEY (respondent_id) REFERENCES respondent (respondent_id),
    FOREIGN KEY (barrier_id)    REFERENCES account_barrier (barrier_id)
);
