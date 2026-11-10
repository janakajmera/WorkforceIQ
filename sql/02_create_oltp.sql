-- =====================================================================================
-- WorkforceIQ | 02 | OLTP layer -- normalized (3NF) operational schema
-- Lookups: departments, job_roles, education_fields.  Core: employees, employee_surveys,
-- employee_history (effective-dated versions = SCD2 source), projects, assignments, reviews.
-- =====================================================================================
CREATE DATABASE IF NOT EXISTS wfiq_oltp CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS wfiq_oltp.departments (
    department_id    SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    department_name  VARCHAR(80) NOT NULL,
    department_code  VARCHAR(10) NOT NULL,
    UNIQUE KEY uq_department_name (department_name),
    UNIQUE KEY uq_department_code (department_code)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_oltp.job_roles (
    job_role_id  SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    role_title   VARCHAR(80) NOT NULL,
    UNIQUE KEY uq_role_title (role_title)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_oltp.education_fields (
    education_field_id  SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    field_name          VARCHAR(60) NOT NULL,
    UNIQUE KEY uq_field_name (field_name)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_oltp.employees (
    employee_id               INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    employee_number           INT UNSIGNED NOT NULL,                  -- business key
    first_name                VARCHAR(80)  NOT NULL,
    last_name                 VARCHAR(80)  NOT NULL,
    email                     VARCHAR(160) NOT NULL,
    gender                    ENUM('Male','Female','Other') NOT NULL,
    date_of_birth             DATE NOT NULL,
    marital_status            ENUM('Single','Married','Divorced') NOT NULL,
    education_level           TINYINT UNSIGNED NOT NULL,              -- 1 Below College .. 5 Doctor
    education_field_id        SMALLINT UNSIGNED NOT NULL,
    department_id             SMALLINT UNSIGNED NOT NULL,             -- current state (history in employee_history)
    job_role_id               SMALLINT UNSIGNED NOT NULL,
    job_level                 TINYINT UNSIGNED NOT NULL,
    monthly_income            INT UNSIGNED NOT NULL,
    hire_date                 DATE NOT NULL,
    termination_date          DATE NULL,
    is_active                 TINYINT(1) GENERATED ALWAYS AS (termination_date IS NULL) VIRTUAL,
    business_travel           ENUM('Non-Travel','Travel_Rarely','Travel_Frequently') NOT NULL DEFAULT 'Travel_Rarely',
    over_time                 TINYINT(1) NOT NULL DEFAULT 0,
    distance_from_home        SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    num_companies_worked      TINYINT UNSIGNED NOT NULL DEFAULT 0,
    total_working_years       TINYINT UNSIGNED NOT NULL DEFAULT 0,
    training_times_last_year  TINYINT UNSIGNED NOT NULL DEFAULT 2,
    stock_option_level        TINYINT UNSIGNED NOT NULL DEFAULT 0,
    percent_salary_hike       TINYINT UNSIGNED NOT NULL DEFAULT 12,
    years_in_current_role     TINYINT UNSIGNED NOT NULL DEFAULT 0,
    years_since_last_promotion TINYINT UNSIGNED NOT NULL DEFAULT 0,
    years_with_curr_manager   TINYINT UNSIGNED NOT NULL DEFAULT 0,
    created_at                TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_emp_number (employee_number),
    UNIQUE KEY uq_emp_email (email),
    KEY ix_emp_department (department_id),
    KEY ix_emp_role (job_role_id),
    KEY ix_emp_edu_field (education_field_id),
    KEY ix_emp_name (last_name, first_name),
    CONSTRAINT fk_emp_department FOREIGN KEY (department_id) REFERENCES wfiq_oltp.departments (department_id),
    CONSTRAINT fk_emp_role       FOREIGN KEY (job_role_id) REFERENCES wfiq_oltp.job_roles (job_role_id),
    CONSTRAINT fk_emp_edu_field  FOREIGN KEY (education_field_id) REFERENCES wfiq_oltp.education_fields (education_field_id),
    CONSTRAINT ck_emp_edu_level  CHECK (education_level BETWEEN 1 AND 5),
    CONSTRAINT ck_emp_job_level  CHECK (job_level BETWEEN 1 AND 5),
    CONSTRAINT ck_emp_income     CHECK (monthly_income > 0),
    CONSTRAINT ck_emp_term_date  CHECK (termination_date IS NULL OR termination_date >= hire_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_oltp.employee_surveys (
    survey_id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    employee_id                INT UNSIGNED NOT NULL,
    survey_date                DATE NOT NULL,
    environment_satisfaction   TINYINT UNSIGNED NOT NULL,
    job_satisfaction           TINYINT UNSIGNED NOT NULL,
    relationship_satisfaction  TINYINT UNSIGNED NOT NULL,
    work_life_balance          TINYINT UNSIGNED NOT NULL,
    job_involvement            TINYINT UNSIGNED NOT NULL,
    UNIQUE KEY uq_survey (employee_id, survey_date),
    CONSTRAINT fk_survey_emp FOREIGN KEY (employee_id) REFERENCES wfiq_oltp.employees (employee_id) ON DELETE CASCADE,
    CONSTRAINT ck_survey_scores CHECK (
        environment_satisfaction BETWEEN 1 AND 4 AND job_satisfaction BETWEEN 1 AND 4 AND
        relationship_satisfaction BETWEEN 1 AND 4 AND work_life_balance BETWEEN 1 AND 4 AND
        job_involvement BETWEEN 1 AND 4)
) ENGINE=InnoDB;

-- Effective-dated version log: one row per employee version. The DW builds SCD Type 2 rows from it.
CREATE TABLE IF NOT EXISTS wfiq_oltp.employee_history (
    history_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    employee_id     INT UNSIGNED NOT NULL,
    effective_date  DATE NOT NULL,
    department_id   SMALLINT UNSIGNED NOT NULL,
    job_role_id     SMALLINT UNSIGNED NOT NULL,
    job_level       TINYINT UNSIGNED NOT NULL,
    monthly_income  INT UNSIGNED NOT NULL,
    change_type     ENUM('Hire','Promotion','Transfer','Salary Increase','Role Change') NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_history (employee_id, effective_date),
    KEY ix_hist_department (department_id),
    KEY ix_hist_role (job_role_id),
    CONSTRAINT fk_hist_emp  FOREIGN KEY (employee_id) REFERENCES wfiq_oltp.employees (employee_id) ON DELETE CASCADE,
    CONSTRAINT fk_hist_dept FOREIGN KEY (department_id) REFERENCES wfiq_oltp.departments (department_id),
    CONSTRAINT fk_hist_role FOREIGN KEY (job_role_id) REFERENCES wfiq_oltp.job_roles (job_role_id),
    CONSTRAINT ck_hist_level  CHECK (job_level BETWEEN 1 AND 5),
    CONSTRAINT ck_hist_income CHECK (monthly_income > 0)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_oltp.projects (
    project_id        INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    project_code      VARCHAR(20)  NOT NULL,
    project_name      VARCHAR(150) NOT NULL,
    department_id     SMALLINT UNSIGNED NOT NULL,                      -- owning department
    status            ENUM('Planned','Active','On Hold','Completed','Cancelled') NOT NULL DEFAULT 'Planned',
    priority          ENUM('Low','Medium','High','Critical') NOT NULL DEFAULT 'Medium',
    start_date        DATE NOT NULL,
    planned_end_date  DATE NOT NULL,
    actual_end_date   DATE NULL,
    budget            DECIMAL(14,2) NOT NULL DEFAULT 0,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_project_code (project_code),
    KEY ix_project_dept (department_id),
    KEY ix_project_status (status),
    CONSTRAINT fk_project_dept FOREIGN KEY (department_id) REFERENCES wfiq_oltp.departments (department_id),
    CONSTRAINT ck_project_dates  CHECK (planned_end_date >= start_date),
    CONSTRAINT ck_project_actual CHECK (actual_end_date IS NULL OR actual_end_date >= start_date),
    CONSTRAINT ck_project_budget CHECK (budget >= 0)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_oltp.assignments (
    assignment_id    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    employee_id      INT UNSIGNED NOT NULL,
    project_id       INT UNSIGNED NOT NULL,
    role_on_project  VARCHAR(60) NOT NULL,
    allocation_pct   TINYINT UNSIGNED NOT NULL,
    start_date       DATE NOT NULL,
    end_date         DATE NULL,
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_assignment (employee_id, project_id, start_date),
    KEY ix_asg_project (project_id),
    CONSTRAINT fk_asg_emp  FOREIGN KEY (employee_id) REFERENCES wfiq_oltp.employees (employee_id),
    CONSTRAINT fk_asg_proj FOREIGN KEY (project_id)  REFERENCES wfiq_oltp.projects (project_id),
    CONSTRAINT ck_asg_alloc CHECK (allocation_pct BETWEEN 1 AND 100),
    CONSTRAINT ck_asg_dates CHECK (end_date IS NULL OR end_date >= start_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wfiq_oltp.reviews (
    review_id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    employee_id          INT UNSIGNED NOT NULL,
    project_id           INT UNSIGNED NULL,                            -- primary project during the review period
    review_date          DATE NOT NULL,
    review_year          SMALLINT GENERATED ALWAYS AS (YEAR(review_date)) STORED,
    performance_rating   TINYINT UNSIGNED NOT NULL,
    overall_score        DECIMAL(5,2) NOT NULL,
    goal_completion_pct  TINYINT UNSIGNED NOT NULL,
    collaboration_score  TINYINT UNSIGNED NOT NULL,
    comments             VARCHAR(500) NULL,
    created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_review_year (employee_id, review_year),             -- one review per employee per year
    KEY ix_review_project (project_id),
    KEY ix_review_date (review_date),
    CONSTRAINT fk_review_emp  FOREIGN KEY (employee_id) REFERENCES wfiq_oltp.employees (employee_id),
    CONSTRAINT fk_review_proj FOREIGN KEY (project_id)  REFERENCES wfiq_oltp.projects (project_id),
    CONSTRAINT ck_review_rating CHECK (performance_rating BETWEEN 1 AND 5),
    CONSTRAINT ck_review_score  CHECK (overall_score BETWEEN 0 AND 100),
    CONSTRAINT ck_review_goals  CHECK (goal_completion_pct BETWEEN 0 AND 100),
    CONSTRAINT ck_review_collab CHECK (collaboration_score BETWEEN 1 AND 5)
) ENGINE=InnoDB;
