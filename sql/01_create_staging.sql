-- =====================================================================================
-- WorkforceIQ | 01 | STAGING layer (raw landing zone for the synthesized IBM HR dataset)
-- Re-runnable: uses IF NOT EXISTS. Runs as-is in MySQL Workbench (File > Open SQL Script).
-- Staging is intentionally loose (no FKs, no uniqueness): it holds raw rows, including the
-- duplicates / inconsistent casing / orphans that the ETL CTEs and window functions clean up.
-- Constant IBM columns (EmployeeCount, StandardHours, Over18) are dropped: they carry no information.
-- =====================================================================================
CREATE DATABASE IF NOT EXISTS wfiq_staging CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS wfiq_staging.stg_employee_snapshot (
    stg_row_id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source_employee_number      INT NULL,
    first_name                  VARCHAR(80),
    last_name                   VARCHAR(80),
    email                       VARCHAR(160),
    snapshot_date               DATE,
    hire_date                   DATE,
    termination_date            DATE,
    age                         SMALLINT,
    attrition                   VARCHAR(10),
    business_travel             VARCHAR(30),
    daily_rate                  INT,
    department                  VARCHAR(80),
    distance_from_home          INT,
    education                   INT,
    education_field             VARCHAR(60),
    environment_satisfaction    INT,
    gender                      VARCHAR(20),
    hourly_rate                 INT,
    job_involvement             INT,
    job_level                   INT,
    job_role                    VARCHAR(80),
    job_satisfaction            INT,
    marital_status              VARCHAR(20),
    monthly_income              INT,
    monthly_rate                INT,
    num_companies_worked        INT,
    over_time                   VARCHAR(5),
    percent_salary_hike         INT,
    performance_rating          INT,
    relationship_satisfaction   INT,
    stock_option_level          INT,
    total_working_years         INT,
    training_times_last_year    INT,
    work_life_balance           INT,
    years_at_company            INT,
    years_in_current_role       INT,
    years_since_last_promotion  INT,
    years_with_curr_manager     INT,
    record_source               VARCHAR(20) NOT NULL DEFAULT 'SYNTHETIC',   -- IBM_ORIGINAL | SYNTHETIC
    batch_id                    VARCHAR(40),
    ingested_at                 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_stg_emp_number (source_employee_number)
) ENGINE=InnoDB;

-- One row per employee *version* (SCD Type 2 source). The first row of an employee is the hire version.
CREATE TABLE IF NOT EXISTS wfiq_staging.stg_employee_history (
    stg_row_id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source_employee_number  INT NULL,
    effective_date          DATE,
    department              VARCHAR(80),
    job_role                VARCHAR(80),
    job_level               INT,
    monthly_income          INT,
    change_type             VARCHAR(30),   -- Hire | Promotion | Transfer | Salary Increase
    batch_id                VARCHAR(40),
    ingested_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_stg_hist_emp (source_employee_number, effective_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_staging.stg_project (
    stg_row_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    project_code      VARCHAR(20),
    project_name      VARCHAR(150),
    department        VARCHAR(80),
    status            VARCHAR(20),
    priority          VARCHAR(20),
    start_date        DATE,
    planned_end_date  DATE,
    actual_end_date   DATE,
    budget            DECIMAL(14,2),
    batch_id          VARCHAR(40),
    ingested_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_stg_proj_code (project_code)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_staging.stg_assignment (
    stg_row_id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source_employee_number  INT NULL,
    project_code            VARCHAR(20),
    role_on_project         VARCHAR(60),
    allocation_pct          INT,
    start_date              DATE,
    end_date                DATE,
    batch_id                VARCHAR(40),
    ingested_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_stg_asg_emp (source_employee_number),
    KEY ix_stg_asg_proj (project_code)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_staging.stg_review (
    stg_row_id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source_employee_number  INT NULL,
    project_code            VARCHAR(20),
    review_date             DATE,
    performance_rating      INT,
    overall_score           DECIMAL(6,2),
    goal_completion_pct     INT,
    collaboration_score     INT,
    batch_id                VARCHAR(40),
    ingested_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_stg_rev_emp (source_employee_number, review_date)
) ENGINE=InnoDB;

-- ETL control table shared by the OLTP and DW load procedures
CREATE TABLE IF NOT EXISTS wfiq_staging.etl_run_log (
    run_id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_ts         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    layer          VARCHAR(10)  NOT NULL,          -- OLTP | DW
    step           VARCHAR(60)  NOT NULL,
    rows_affected  INT          NOT NULL DEFAULT 0,
    status         VARCHAR(10)  NOT NULL,          -- OK | WARN | FAILED
    message        VARCHAR(500) NULL,
    duration_ms    INT          NULL,
    KEY ix_log_ts (run_ts)
) ENGINE=InnoDB;
