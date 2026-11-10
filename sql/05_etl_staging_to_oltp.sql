-- =====================================================================================
-- WorkforceIQ | 05 | ETL  STAGING -> OLTP
-- * v_*_clean views: CTEs + ROW_NUMBER() de-duplicate (newest ingest wins), TRIM/LOWER/CASE standardise
--   text, and a reject_reason column flags rows that must not be loaded.
-- * sp_etl_load_* procedures: transactional, idempotent (NOT EXISTS), log to wfiq_staging.etl_run_log.
-- Re-running never duplicates rows and never overwrites changes users made in the OLTP database.
-- =====================================================================================

-- ------------------------------------------------------------------ cleansing views
CREATE OR REPLACE VIEW wfiq_staging.v_employee_clean AS
WITH ranked AS (
    SELECT s.*,
           ROW_NUMBER() OVER (PARTITION BY s.source_employee_number
                              ORDER BY s.ingested_at DESC, s.stg_row_id DESC) AS rn
    FROM wfiq_staging.stg_employee_snapshot s
    WHERE s.source_employee_number IS NOT NULL
),
cleaned AS (
    SELECT
        r.source_employee_number                                        AS employee_number,
        TRIM(r.first_name)                                              AS first_name,
        TRIM(r.last_name)                                               AS last_name,
        LOWER(TRIM(r.email))                                            AS email,
        CASE LOWER(TRIM(r.gender)) WHEN 'male' THEN 'Male' WHEN 'm' THEN 'Male'
             WHEN 'female' THEN 'Female' WHEN 'f' THEN 'Female' ELSE 'Other' END AS gender,
        CASE LOWER(TRIM(r.marital_status)) WHEN 'single' THEN 'Single' WHEN 'married' THEN 'Married'
             WHEN 'divorced' THEN 'Divorced' END                         AS marital_status,
        TRIM(r.department)                                              AS department,
        TRIM(r.job_role)                                                AS job_role,
        TRIM(r.education_field)                                         AS education_field,
        CASE LOWER(TRIM(r.business_travel)) WHEN 'non-travel' THEN 'Non-Travel'
             WHEN 'travel_rarely' THEN 'Travel_Rarely' WHEN 'travel_frequently' THEN 'Travel_Frequently' END AS business_travel,
        CASE WHEN LOWER(TRIM(r.over_time)) IN ('yes','y','1','true') THEN 1 ELSE 0 END AS over_time,
        r.education, r.job_level, r.monthly_income, r.hire_date, r.termination_date, r.snapshot_date,
        r.distance_from_home, r.num_companies_worked, r.total_working_years, r.training_times_last_year,
        r.stock_option_level, r.percent_salary_hike, r.years_in_current_role,
        r.years_since_last_promotion, r.years_with_curr_manager,
        r.environment_satisfaction, r.job_satisfaction, r.relationship_satisfaction,
        r.work_life_balance, r.job_involvement,
        DATE_SUB(DATE_SUB(r.snapshot_date, INTERVAL r.age YEAR),
                 INTERVAL MOD(r.source_employee_number * 37, 365) DAY)  AS date_of_birth
    FROM ranked r
    WHERE r.rn = 1
),
validated AS (
    SELECT c.*,
           ROW_NUMBER() OVER (PARTITION BY c.email ORDER BY c.employee_number) AS email_rn
    FROM cleaned c
)
SELECT v.*,
       CASE
           WHEN v.email IS NULL OR v.email NOT LIKE '%@%'                          THEN 'invalid email'
           WHEN v.email_rn > 1                                                       THEN 'duplicate email'
           WHEN COALESCE(v.first_name,'') = '' OR COALESCE(v.last_name,'') = ''      THEN 'missing name'
           WHEN v.marital_status IS NULL                                             THEN 'invalid marital status'
           WHEN v.business_travel IS NULL                                            THEN 'invalid business travel'
           WHEN v.monthly_income IS NULL OR v.monthly_income <= 0                    THEN 'invalid income'
           WHEN v.job_level IS NULL OR v.job_level NOT BETWEEN 1 AND 5               THEN 'invalid job level'
           WHEN v.education IS NULL OR v.education NOT BETWEEN 1 AND 5               THEN 'invalid education'
           WHEN v.hire_date IS NULL OR v.date_of_birth IS NULL                       THEN 'missing dates'
           WHEN v.termination_date IS NOT NULL AND v.termination_date < v.hire_date  THEN 'termination before hire'
           ELSE NULL
       END AS reject_reason
FROM validated v;

CREATE OR REPLACE VIEW wfiq_staging.v_employee_history_clean AS
WITH ranked AS (
    SELECT h.*,
           ROW_NUMBER() OVER (PARTITION BY h.source_employee_number, h.effective_date
                              ORDER BY h.ingested_at DESC, h.stg_row_id DESC) AS rn
    FROM wfiq_staging.stg_employee_history h
    WHERE h.source_employee_number IS NOT NULL AND h.effective_date IS NOT NULL
      AND h.job_level BETWEEN 1 AND 5 AND h.monthly_income > 0
)
SELECT r.source_employee_number AS employee_number, r.effective_date,
       TRIM(r.department) AS department, TRIM(r.job_role) AS job_role, r.job_level, r.monthly_income,
       CASE LOWER(TRIM(r.change_type)) WHEN 'hire' THEN 'Hire' WHEN 'promotion' THEN 'Promotion'
            WHEN 'transfer' THEN 'Transfer' WHEN 'salary increase' THEN 'Salary Increase'
            ELSE 'Role Change' END AS change_type
FROM ranked r WHERE r.rn = 1;

CREATE OR REPLACE VIEW wfiq_staging.v_project_clean AS
WITH ranked AS (
    SELECT p.*,
           ROW_NUMBER() OVER (PARTITION BY TRIM(p.project_code)
                              ORDER BY p.ingested_at DESC, p.stg_row_id DESC) AS rn
    FROM wfiq_staging.stg_project p
    WHERE p.project_code IS NOT NULL AND TRIM(p.project_code) <> ''
)
SELECT TRIM(r.project_code) AS project_code, TRIM(r.project_name) AS project_name, TRIM(r.department) AS department,
       CASE LOWER(TRIM(r.status)) WHEN 'planned' THEN 'Planned' WHEN 'active' THEN 'Active' WHEN 'on hold' THEN 'On Hold'
            WHEN 'completed' THEN 'Completed' WHEN 'cancelled' THEN 'Cancelled' END AS status,
       CASE LOWER(TRIM(r.priority)) WHEN 'low' THEN 'Low' WHEN 'medium' THEN 'Medium'
            WHEN 'high' THEN 'High' WHEN 'critical' THEN 'Critical' END AS priority,
       r.start_date, r.planned_end_date, r.actual_end_date, COALESCE(r.budget, 0) AS budget
FROM ranked r
WHERE r.rn = 1 AND r.start_date IS NOT NULL AND r.planned_end_date >= r.start_date
  AND (r.actual_end_date IS NULL OR r.actual_end_date >= r.start_date) AND COALESCE(r.budget, 0) >= 0;

CREATE OR REPLACE VIEW wfiq_staging.v_assignment_clean AS
WITH ranked AS (
    SELECT a.*,
           ROW_NUMBER() OVER (PARTITION BY a.source_employee_number, TRIM(a.project_code), a.start_date
                              ORDER BY a.ingested_at DESC, a.stg_row_id DESC) AS rn
    FROM wfiq_staging.stg_assignment a
    WHERE a.source_employee_number IS NOT NULL AND a.project_code IS NOT NULL AND a.start_date IS NOT NULL
      AND a.allocation_pct BETWEEN 1 AND 100 AND (a.end_date IS NULL OR a.end_date >= a.start_date)
)
SELECT r.source_employee_number AS employee_number, TRIM(r.project_code) AS project_code,
       COALESCE(NULLIF(TRIM(r.role_on_project), ''), 'Contributor') AS role_on_project,
       r.allocation_pct, r.start_date, r.end_date
FROM ranked r WHERE r.rn = 1;

CREATE OR REPLACE VIEW wfiq_staging.v_review_clean AS
WITH valid_rows AS (            -- validate FIRST so the newest *valid* review wins
    SELECT s.*, YEAR(s.review_date) AS review_year
    FROM wfiq_staging.stg_review s
    WHERE s.source_employee_number IS NOT NULL AND s.review_date IS NOT NULL
      AND s.performance_rating BETWEEN 1 AND 5 AND s.overall_score BETWEEN 0 AND 100
      AND s.goal_completion_pct BETWEEN 0 AND 100 AND s.collaboration_score BETWEEN 1 AND 5
),
ranked AS (
    SELECT v.*,
           ROW_NUMBER() OVER (PARTITION BY v.source_employee_number, v.review_year
                              ORDER BY v.ingested_at DESC, v.stg_row_id DESC) AS rn
    FROM valid_rows v
)
SELECT r.source_employee_number AS employee_number, NULLIF(TRIM(r.project_code), '') AS project_code,
       r.review_date, r.review_year, r.performance_rating, r.overall_score,
       r.goal_completion_pct, r.collaboration_score
FROM ranked r WHERE r.rn = 1;

DELIMITER //

DROP PROCEDURE IF EXISTS wfiq_staging.sp_log_etl //
CREATE PROCEDURE wfiq_staging.sp_log_etl(IN p_layer VARCHAR(10), IN p_step VARCHAR(60), IN p_rows INT,
                                         IN p_status VARCHAR(10), IN p_msg VARCHAR(500), IN p_started DATETIME(6))
BEGIN
    INSERT INTO wfiq_staging.etl_run_log (layer, step, rows_affected, status, message, duration_ms)
    VALUES (p_layer, p_step, p_rows, p_status, LEFT(p_msg, 500),
            TIMESTAMPDIFF(MICROSECOND, p_started, NOW(6)) / 1000);
END //

-- ------------------------------------------------------------------ employees
DROP PROCEDURE IF EXISTS wfiq_oltp.sp_etl_load_employees //
CREATE PROCEDURE wfiq_oltp.sp_etl_load_employees()
BEGIN
    DECLARE v_start DATETIME(6) DEFAULT NOW(6);
    DECLARE v_inserted INT DEFAULT 0;
    DECLARE v_rejected INT DEFAULT 0;
    DECLARE v_msg TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_msg = MESSAGE_TEXT;
        ROLLBACK;
        CALL wfiq_staging.sp_log_etl('OLTP', 'load_employees', 0, 'FAILED', v_msg, v_start);
        COMMIT;
        RESIGNAL;
    END;

    START TRANSACTION;
    INSERT INTO wfiq_oltp.employees
        (employee_number, first_name, last_name, email, gender, date_of_birth, marital_status, education_level,
         education_field_id, department_id, job_role_id, job_level, monthly_income, hire_date, termination_date,
         business_travel, over_time, distance_from_home, num_companies_worked, total_working_years,
         training_times_last_year, stock_option_level, percent_salary_hike, years_in_current_role,
         years_since_last_promotion, years_with_curr_manager)
    SELECT c.employee_number, c.first_name, c.last_name, c.email, c.gender, c.date_of_birth, c.marital_status,
           c.education, ef.education_field_id, d.department_id, jr.job_role_id, c.job_level, c.monthly_income,
           c.hire_date, c.termination_date, c.business_travel, c.over_time, c.distance_from_home,
           c.num_companies_worked, c.total_working_years, c.training_times_last_year, c.stock_option_level,
           c.percent_salary_hike, c.years_in_current_role, c.years_since_last_promotion, c.years_with_curr_manager
    FROM wfiq_staging.v_employee_clean c
    JOIN wfiq_oltp.departments      d  ON d.department_name = c.department
    JOIN wfiq_oltp.job_roles        jr ON jr.role_title     = c.job_role
    JOIN wfiq_oltp.education_fields ef ON ef.field_name     = c.education_field
    WHERE c.reject_reason IS NULL
      AND NOT EXISTS (SELECT 1 FROM wfiq_oltp.employees e WHERE e.employee_number = c.employee_number)
    ORDER BY c.employee_number;
    SET v_inserted = ROW_COUNT();

    SELECT COUNT(*) INTO v_rejected
    FROM wfiq_staging.v_employee_clean c
    LEFT JOIN wfiq_oltp.departments      d  ON d.department_name = c.department
    LEFT JOIN wfiq_oltp.job_roles        jr ON jr.role_title     = c.job_role
    LEFT JOIN wfiq_oltp.education_fields ef ON ef.field_name     = c.education_field
    WHERE c.reject_reason IS NOT NULL OR d.department_id IS NULL OR jr.job_role_id IS NULL
          OR ef.education_field_id IS NULL;

    CALL wfiq_staging.sp_log_etl('OLTP', 'load_employees', v_inserted,
                                 IF(v_rejected > 0, 'WARN', 'OK'),
                                 CONCAT('inserted=', v_inserted, ', rejected=', v_rejected), v_start);
    COMMIT;
END //

-- ------------------------------------------------------------------ surveys
DROP PROCEDURE IF EXISTS wfiq_oltp.sp_etl_load_surveys //
CREATE PROCEDURE wfiq_oltp.sp_etl_load_surveys()
BEGIN
    DECLARE v_start DATETIME(6) DEFAULT NOW(6);
    DECLARE v_inserted INT DEFAULT 0;
    DECLARE v_msg TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_msg = MESSAGE_TEXT;
        ROLLBACK;
        CALL wfiq_staging.sp_log_etl('OLTP', 'load_surveys', 0, 'FAILED', v_msg, v_start);
        COMMIT;
        RESIGNAL;
    END;

    START TRANSACTION;
    INSERT INTO wfiq_oltp.employee_surveys
        (employee_id, survey_date, environment_satisfaction, job_satisfaction, relationship_satisfaction,
         work_life_balance, job_involvement)
    SELECT e.employee_id, c.snapshot_date, c.environment_satisfaction, c.job_satisfaction,
           c.relationship_satisfaction, c.work_life_balance, c.job_involvement
    FROM wfiq_staging.v_employee_clean c
    JOIN wfiq_oltp.employees e ON e.employee_number = c.employee_number
    WHERE c.reject_reason IS NULL AND c.snapshot_date IS NOT NULL
      AND c.environment_satisfaction BETWEEN 1 AND 4 AND c.job_satisfaction BETWEEN 1 AND 4
      AND c.relationship_satisfaction BETWEEN 1 AND 4 AND c.work_life_balance BETWEEN 1 AND 4
      AND c.job_involvement BETWEEN 1 AND 4
      AND NOT EXISTS (SELECT 1 FROM wfiq_oltp.employee_surveys s
                      WHERE s.employee_id = e.employee_id AND s.survey_date = c.snapshot_date);
    SET v_inserted = ROW_COUNT();
    CALL wfiq_staging.sp_log_etl('OLTP', 'load_surveys', v_inserted, 'OK', CONCAT('inserted=', v_inserted), v_start);
    COMMIT;
END //

-- ------------------------------------------------------------------ employee history (SCD2 source)
DROP PROCEDURE IF EXISTS wfiq_oltp.sp_etl_load_employee_history //
CREATE PROCEDURE wfiq_oltp.sp_etl_load_employee_history()
BEGIN
    DECLARE v_start DATETIME(6) DEFAULT NOW(6);
    DECLARE v_inserted INT DEFAULT 0;
    DECLARE v_msg TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_msg = MESSAGE_TEXT;
        ROLLBACK;
        CALL wfiq_staging.sp_log_etl('OLTP', 'load_employee_history', 0, 'FAILED', v_msg, v_start);
        COMMIT;
        RESIGNAL;
    END;

    START TRANSACTION;
    -- Employees with staged career versions get exactly those versions (first one forced to 'Hire');
    -- every other employee gets a single 'Hire' version built from the employee row itself.
    INSERT INTO wfiq_oltp.employee_history
        (employee_id, effective_date, department_id, job_role_id, job_level, monthly_income, change_type)
    WITH staged AS (
        SELECT e.employee_id, h.effective_date, d.department_id, jr.job_role_id, h.job_level,
               h.monthly_income, h.change_type,
               ROW_NUMBER() OVER (PARTITION BY e.employee_id ORDER BY h.effective_date) AS version_no
        FROM wfiq_staging.v_employee_history_clean h
        JOIN wfiq_oltp.employees   e  ON e.employee_number = h.employee_number
        JOIN wfiq_oltp.departments d  ON d.department_name = h.department
        JOIN wfiq_oltp.job_roles   jr ON jr.role_title     = h.job_role
        WHERE h.effective_date >= e.hire_date
          AND NOT EXISTS (SELECT 1 FROM wfiq_oltp.employee_history x WHERE x.employee_id = e.employee_id)
    )
    SELECT s.employee_id, s.effective_date, s.department_id, s.job_role_id, s.job_level, s.monthly_income,
           CASE WHEN s.version_no = 1 THEN 'Hire' ELSE s.change_type END
    FROM staged s
    UNION ALL
    SELECT e.employee_id, e.hire_date, e.department_id, e.job_role_id, e.job_level, e.monthly_income, 'Hire'
    FROM wfiq_oltp.employees e
    WHERE NOT EXISTS (SELECT 1 FROM wfiq_oltp.employee_history x WHERE x.employee_id = e.employee_id)
      AND NOT EXISTS (SELECT 1 FROM staged s2 WHERE s2.employee_id = e.employee_id);
    SET v_inserted = ROW_COUNT();
    CALL wfiq_staging.sp_log_etl('OLTP', 'load_employee_history', v_inserted, 'OK',
                                 CONCAT('inserted=', v_inserted), v_start);
    COMMIT;
END //

-- ------------------------------------------------------------------ projects
DROP PROCEDURE IF EXISTS wfiq_oltp.sp_etl_load_projects //
CREATE PROCEDURE wfiq_oltp.sp_etl_load_projects()
BEGIN
    DECLARE v_start DATETIME(6) DEFAULT NOW(6);
    DECLARE v_inserted INT DEFAULT 0;
    DECLARE v_msg TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_msg = MESSAGE_TEXT;
        ROLLBACK;
        CALL wfiq_staging.sp_log_etl('OLTP', 'load_projects', 0, 'FAILED', v_msg, v_start);
        COMMIT;
        RESIGNAL;
    END;

    START TRANSACTION;
    INSERT INTO wfiq_oltp.projects
        (project_code, project_name, department_id, status, priority, start_date, planned_end_date,
         actual_end_date, budget)
    SELECT p.project_code, p.project_name, d.department_id, p.status, p.priority, p.start_date,
           p.planned_end_date, p.actual_end_date, p.budget
    FROM wfiq_staging.v_project_clean p
    JOIN wfiq_oltp.departments d ON d.department_name = p.department
    WHERE p.status IS NOT NULL AND p.priority IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM wfiq_oltp.projects x WHERE x.project_code = p.project_code)
    ORDER BY p.project_code;
    SET v_inserted = ROW_COUNT();
    CALL wfiq_staging.sp_log_etl('OLTP', 'load_projects', v_inserted, 'OK', CONCAT('inserted=', v_inserted), v_start);
    COMMIT;
END //

-- ------------------------------------------------------------------ assignments
DROP PROCEDURE IF EXISTS wfiq_oltp.sp_etl_load_assignments //
CREATE PROCEDURE wfiq_oltp.sp_etl_load_assignments()
BEGIN
    DECLARE v_start DATETIME(6) DEFAULT NOW(6);
    DECLARE v_inserted INT DEFAULT 0;
    DECLARE v_staged INT DEFAULT 0;
    DECLARE v_msg TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_msg = MESSAGE_TEXT;
        ROLLBACK;
        CALL wfiq_staging.sp_log_etl('OLTP', 'load_assignments', 0, 'FAILED', v_msg, v_start);
        COMMIT;
        RESIGNAL;
    END;

    START TRANSACTION;
    SELECT COUNT(*) INTO v_staged FROM wfiq_staging.stg_assignment;
    INSERT INTO wfiq_oltp.assignments
        (employee_id, project_id, role_on_project, allocation_pct, start_date, end_date)
    SELECT e.employee_id, p.project_id, a.role_on_project, a.allocation_pct, a.start_date, a.end_date
    FROM wfiq_staging.v_assignment_clean a
    JOIN wfiq_oltp.employees e ON e.employee_number = a.employee_number     -- orphans fall out here
    JOIN wfiq_oltp.projects  p ON p.project_code    = a.project_code
    WHERE NOT EXISTS (SELECT 1 FROM wfiq_oltp.assignments x
                      WHERE x.employee_id = e.employee_id AND x.project_id = p.project_id
                        AND x.start_date = a.start_date)
    ORDER BY e.employee_id, a.start_date;
    SET v_inserted = ROW_COUNT();
    CALL wfiq_staging.sp_log_etl('OLTP', 'load_assignments', v_inserted,
                                 IF(v_inserted < v_staged, 'WARN', 'OK'),
                                 CONCAT('staged=', v_staged, ', inserted=', v_inserted,
                                        ' (difference = duplicates/orphans/already loaded)'), v_start);
    COMMIT;
END //

-- ------------------------------------------------------------------ reviews
DROP PROCEDURE IF EXISTS wfiq_oltp.sp_etl_load_reviews //
CREATE PROCEDURE wfiq_oltp.sp_etl_load_reviews()
BEGIN
    DECLARE v_start DATETIME(6) DEFAULT NOW(6);
    DECLARE v_inserted INT DEFAULT 0;
    DECLARE v_staged INT DEFAULT 0;
    DECLARE v_rejected INT DEFAULT 0;
    DECLARE v_msg TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_msg = MESSAGE_TEXT;
        ROLLBACK;
        CALL wfiq_staging.sp_log_etl('OLTP', 'load_reviews', 0, 'FAILED', v_msg, v_start);
        COMMIT;
        RESIGNAL;
    END;

    START TRANSACTION;
    SELECT COUNT(*) INTO v_staged FROM wfiq_staging.stg_review;
    SELECT COUNT(*) INTO v_rejected
    FROM wfiq_staging.stg_review s
    LEFT JOIN wfiq_oltp.employees e ON e.employee_number = s.source_employee_number
    WHERE e.employee_id IS NULL OR s.review_date IS NULL OR s.performance_rating NOT BETWEEN 1 AND 5
          OR s.overall_score NOT BETWEEN 0 AND 100 OR s.goal_completion_pct NOT BETWEEN 0 AND 100
          OR s.collaboration_score NOT BETWEEN 1 AND 5;

    INSERT INTO wfiq_oltp.reviews
        (employee_id, project_id, review_date, performance_rating, overall_score, goal_completion_pct,
         collaboration_score)
    SELECT e.employee_id, p.project_id, r.review_date, r.performance_rating, r.overall_score,
           r.goal_completion_pct, r.collaboration_score
    FROM wfiq_staging.v_review_clean r
    JOIN wfiq_oltp.employees e ON e.employee_number = r.employee_number
    LEFT JOIN wfiq_oltp.projects p ON p.project_code = r.project_code
    WHERE NOT EXISTS (SELECT 1 FROM wfiq_oltp.reviews x
                      WHERE x.employee_id = e.employee_id AND x.review_year = r.review_year)
    ORDER BY e.employee_id, r.review_date;
    SET v_inserted = ROW_COUNT();
    CALL wfiq_staging.sp_log_etl('OLTP', 'load_reviews', v_inserted, IF(v_rejected > 0, 'WARN', 'OK'),
                                 CONCAT('staged=', v_staged, ', inserted=', v_inserted,
                                        ', rejected(invalid/orphan)=', v_rejected), v_start);
    COMMIT;
END //

-- ------------------------------------------------------------------ post-load consistency check
DROP PROCEDURE IF EXISTS wfiq_oltp.sp_etl_validate_oltp //
CREATE PROCEDURE wfiq_oltp.sp_etl_validate_oltp()
BEGIN
    DECLARE v_start DATETIME(6) DEFAULT NOW(6);
    DECLARE v_mismatch INT DEFAULT 0;
    DECLARE v_no_hist INT DEFAULT 0;
    -- the newest history version must equal the employee's current state
    SELECT COUNT(*) INTO v_mismatch
    FROM wfiq_oltp.employees e
    JOIN (SELECT employee_id, MAX(effective_date) AS last_eff FROM wfiq_oltp.employee_history GROUP BY employee_id) m
         ON m.employee_id = e.employee_id
    JOIN wfiq_oltp.employee_history h ON h.employee_id = m.employee_id AND h.effective_date = m.last_eff
    WHERE h.department_id <> e.department_id OR h.job_role_id <> e.job_role_id
          OR h.job_level <> e.job_level OR h.monthly_income <> e.monthly_income;
    SELECT COUNT(*) INTO v_no_hist
    FROM wfiq_oltp.employees e
    WHERE NOT EXISTS (SELECT 1 FROM wfiq_oltp.employee_history h WHERE h.employee_id = e.employee_id);
    CALL wfiq_staging.sp_log_etl('OLTP', 'validate_oltp', v_mismatch + v_no_hist,
                                 IF(v_mismatch + v_no_hist > 0, 'WARN', 'OK'),
                                 CONCAT('history/employee mismatches=', v_mismatch, ', employees without history=', v_no_hist),
                                 v_start);
END //

-- ------------------------------------------------------------------ orchestrator
DROP PROCEDURE IF EXISTS wfiq_oltp.sp_etl_stg_to_oltp //
CREATE PROCEDURE wfiq_oltp.sp_etl_stg_to_oltp()
BEGIN
    CALL wfiq_oltp.sp_etl_load_employees();
    CALL wfiq_oltp.sp_etl_load_surveys();
    CALL wfiq_oltp.sp_etl_load_employee_history();
    CALL wfiq_oltp.sp_etl_load_projects();
    CALL wfiq_oltp.sp_etl_load_assignments();
    CALL wfiq_oltp.sp_etl_load_reviews();
    CALL wfiq_oltp.sp_etl_validate_oltp();
END //

DELIMITER ;
