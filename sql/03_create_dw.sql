-- =====================================================================================
-- WorkforceIQ | 03 | DATA WAREHOUSE -- Kimball star schema
--   Dimensions (all with AUTO_INCREMENT surrogate keys + a -1 "Unknown" member):
--     dim_date, dim_department, dim_project (Type 1), dim_employee (SCD Type 2)
--   Facts: fact_performance_reviews (required), fact_project_assignments (bottleneck analysis)
-- =====================================================================================
CREATE DATABASE IF NOT EXISTS wfiq_dw CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS wfiq_dw.dim_date (
    date_key      INT NOT NULL AUTO_INCREMENT PRIMARY KEY,             -- surrogate
    full_date     DATE NULL,
    day_of_month  TINYINT, day_name VARCHAR(10), week_of_year TINYINT,
    month_num     TINYINT, month_name VARCHAR(10), quarter_num TINYINT,
    year_num      SMALLINT, month_label CHAR(7), is_weekend TINYINT(1),
    UNIQUE KEY uq_dim_date_full (full_date),
    KEY ix_dim_date_year (year_num)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_dw.dim_department (
    department_key   INT NOT NULL AUTO_INCREMENT PRIMARY KEY,          -- surrogate
    department_id    INT NOT NULL,                                     -- natural key from OLTP
    department_name  VARCHAR(80) NOT NULL,
    department_code  VARCHAR(10) NOT NULL,
    UNIQUE KEY uq_dim_department_nk (department_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_dw.dim_project (
    project_key        INT NOT NULL AUTO_INCREMENT PRIMARY KEY,        -- surrogate
    project_id         INT NOT NULL,                                   -- natural key from OLTP
    project_code       VARCHAR(20) NOT NULL,
    project_name       VARCHAR(150) NOT NULL,
    owning_department  VARCHAR(80) NOT NULL,
    status             VARCHAR(20) NOT NULL,
    priority           VARCHAR(20) NOT NULL,
    start_date         DATE NULL,
    planned_end_date   DATE NULL,
    actual_end_date    DATE NULL,
    budget             DECIMAL(14,2) NOT NULL DEFAULT 0,
    is_overdue         TINYINT(1) NOT NULL DEFAULT 0,
    UNIQUE KEY uq_dim_project_nk (project_id),
    KEY ix_dim_project_status (status)
) ENGINE=InnoDB;

-- SCD Type 2: tracked attributes = department, job role, job level, monthly income (see row_hash).
CREATE TABLE IF NOT EXISTS wfiq_dw.dim_employee (
    employee_key               INT NOT NULL AUTO_INCREMENT PRIMARY KEY, -- surrogate (one per VERSION)
    employee_id                INT NOT NULL,                            -- natural key from OLTP
    employee_number            INT NOT NULL,
    full_name                  VARCHAR(170) NOT NULL,
    email                      VARCHAR(160) NULL,
    gender                     VARCHAR(10) NULL,
    age_band                   VARCHAR(10) NULL,
    marital_status             VARCHAR(10) NULL,
    education_level            TINYINT NULL,
    education_field            VARCHAR(60) NULL,
    hire_date                  DATE NULL,
    termination_date           DATE NULL,
    is_active                  TINYINT(1) NOT NULL DEFAULT 1,
    attrition_flag             CHAR(3) NOT NULL DEFAULT 'No',
    business_travel            VARCHAR(20) NULL,
    over_time                  CHAR(3) NULL,
    distance_from_home         SMALLINT NULL,
    stock_option_level         TINYINT NULL,
    years_since_last_promotion TINYINT NULL,
    job_satisfaction           TINYINT NULL,
    environment_satisfaction   TINYINT NULL,
    work_life_balance          TINYINT NULL,
    -- ---- SCD Type 2 tracked attributes ----
    department_id              INT NULL,
    department_name            VARCHAR(80) NULL,
    job_role                   VARCHAR(80) NULL,
    job_level                  TINYINT NULL,
    monthly_income             INT NULL,
    salary_band                VARCHAR(10) NULL,
    row_hash                   CHAR(32) NOT NULL,                       -- MD5 of tracked attributes (change detection)
    -- ---- SCD Type 2 housekeeping ----
    start_date                 DATE NOT NULL,
    end_date                   DATE NOT NULL DEFAULT '9999-12-31',
    is_current                 TINYINT(1) NOT NULL DEFAULT 1,
    version_no                 SMALLINT NOT NULL DEFAULT 1,
    created_at                 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_dim_emp_version (employee_id, start_date),
    KEY ix_dim_emp_current (employee_id, is_current),
    KEY ix_dim_emp_pit (employee_id, start_date, end_date),
    KEY ix_dim_emp_dept (department_name),
    KEY ix_dim_emp_flags (is_current, is_active)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_dw.fact_performance_reviews (
    review_fact_key      BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    review_id            BIGINT UNSIGNED NOT NULL,                      -- degenerate dimension (OLTP key); makes loads idempotent
    employee_key         INT NOT NULL,                                  -- employee VERSION valid on the review date
    department_key       INT NOT NULL,                                  -- department on the review date
    project_key          INT NOT NULL,
    date_key             INT NOT NULL,
    performance_rating   TINYINT NOT NULL,
    overall_score        DECIMAL(5,2) NOT NULL,
    goal_completion_pct  TINYINT NOT NULL,
    collaboration_score  TINYINT NOT NULL,
    review_count         TINYINT NOT NULL DEFAULT 1,
    UNIQUE KEY uq_fact_review (review_id),
    KEY ix_fact_rev_emp (employee_key),
    KEY ix_fact_rev_dept (department_key),
    KEY ix_fact_rev_proj (project_key),
    KEY ix_fact_rev_date (date_key),
    CONSTRAINT fk_frev_emp  FOREIGN KEY (employee_key)   REFERENCES wfiq_dw.dim_employee (employee_key),
    CONSTRAINT fk_frev_dept FOREIGN KEY (department_key) REFERENCES wfiq_dw.dim_department (department_key),
    CONSTRAINT fk_frev_proj FOREIGN KEY (project_key)    REFERENCES wfiq_dw.dim_project (project_key),
    CONSTRAINT fk_frev_date FOREIGN KEY (date_key)       REFERENCES wfiq_dw.dim_date (date_key)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_dw.fact_project_assignments (
    assignment_fact_key  BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    assignment_id        BIGINT UNSIGNED NOT NULL,                      -- degenerate dimension
    employee_key         INT NOT NULL,                                  -- employee VERSION valid at assignment start
    department_key       INT NOT NULL,
    project_key          INT NOT NULL,
    start_date_key       INT NOT NULL,
    end_date_key         INT NOT NULL,                                  -- -1 while open-ended
    allocation_pct       TINYINT NOT NULL,
    assigned_days        INT NOT NULL,
    is_active            TINYINT(1) NOT NULL,
    UNIQUE KEY uq_fact_assignment (assignment_id),
    KEY ix_fasg_emp (employee_key),
    KEY ix_fasg_dept (department_key),
    KEY ix_fasg_proj (project_key),
    KEY ix_fasg_start (start_date_key),
    KEY ix_fasg_end (end_date_key),
    CONSTRAINT fk_fasg_emp   FOREIGN KEY (employee_key)   REFERENCES wfiq_dw.dim_employee (employee_key),
    CONSTRAINT fk_fasg_dept  FOREIGN KEY (department_key) REFERENCES wfiq_dw.dim_department (department_key),
    CONSTRAINT fk_fasg_proj  FOREIGN KEY (project_key)    REFERENCES wfiq_dw.dim_project (project_key),
    CONSTRAINT fk_fasg_sdate FOREIGN KEY (start_date_key) REFERENCES wfiq_dw.dim_date (date_key),
    CONSTRAINT fk_fasg_edate FOREIGN KEY (end_date_key)   REFERENCES wfiq_dw.dim_date (date_key)
) ENGINE=InnoDB;

-- "Unknown member" rows (key -1) so facts never carry NULL foreign keys.
INSERT IGNORE INTO wfiq_dw.dim_date (date_key, full_date, day_name, month_name, month_label)
VALUES (-1, NULL, 'Unknown', 'Unknown', 'Unknown');
INSERT IGNORE INTO wfiq_dw.dim_department (department_key, department_id, department_name, department_code)
VALUES (-1, -1, 'Unknown', 'UNK');
INSERT IGNORE INTO wfiq_dw.dim_project (project_key, project_id, project_code, project_name, owning_department, status, priority)
VALUES (-1, -1, 'NONE', 'No project', 'Unknown', 'N/A', 'N/A');
INSERT IGNORE INTO wfiq_dw.dim_employee (employee_key, employee_id, employee_number, full_name, department_name, row_hash, start_date, end_date, is_current, version_no)
VALUES (-1, -1, -1, 'Unknown', 'Unknown', REPEAT('0', 32), '1900-01-01', '9999-12-31', 1, 1);
