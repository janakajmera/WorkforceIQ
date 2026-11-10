-- =====================================================================================
-- WorkforceIQ | 06 | ETL  OLTP -> DATA WAREHOUSE  (surrogate keys, SCD Type 2, facts)
-- SCD2 tracked attributes: department, job role, job level, monthly income (row_hash).
-- New employees: full version timeline from employee_history using LEAD()/ROW_NUMBER().
-- Existing employees: hash compare -> expire current row (end_date, is_current=0) + insert new version.
-- =====================================================================================
DELIMITER //

DROP PROCEDURE IF EXISTS wfiq_dw.sp_dw_load_dim_date //
CREATE PROCEDURE wfiq_dw.sp_dw_load_dim_date(IN p_start DATE, IN p_end DATE)
BEGIN
    DECLARE v_start DATETIME(6) DEFAULT NOW(6);
    DECLARE v_rows INT DEFAULT 0;
    SET SESSION cte_max_recursion_depth = 50000;
    INSERT INTO wfiq_dw.dim_date (full_date, day_of_month, day_name, week_of_year, month_num, month_name,
                                  quarter_num, year_num, month_label, is_weekend)
    WITH RECURSIVE d AS (
        SELECT p_start AS dt
        UNION ALL
        SELECT DATE_ADD(dt, INTERVAL 1 DAY) FROM d WHERE dt < p_end
    )
    SELECT dt, DAY(dt), DAYNAME(dt), WEEKOFYEAR(dt), MONTH(dt), MONTHNAME(dt), QUARTER(dt), YEAR(dt),
           DATE_FORMAT(dt, '%Y-%m'), WEEKDAY(dt) >= 5
    FROM d
    WHERE NOT EXISTS (SELECT 1 FROM wfiq_dw.dim_date x WHERE x.full_date = d.dt);
    SET v_rows = ROW_COUNT();
    CALL wfiq_staging.sp_log_etl('DW', 'dim_date', v_rows, 'OK', CONCAT('inserted=', v_rows), v_start);
END //

DROP PROCEDURE IF EXISTS wfiq_dw.sp_dw_load_dim_department //
CREATE PROCEDURE wfiq_dw.sp_dw_load_dim_department()
BEGIN
    DECLARE v_start DATETIME(6) DEFAULT NOW(6);
    DECLARE v_rows INT DEFAULT 0;
    UPDATE wfiq_dw.dim_department dd JOIN wfiq_oltp.departments d ON d.department_id = dd.department_id
       SET dd.department_name = d.department_name, dd.department_code = d.department_code
     WHERE dd.department_name <> d.department_name OR dd.department_code <> d.department_code;
    INSERT INTO wfiq_dw.dim_department (department_id, department_name, department_code)
    SELECT d.department_id, d.department_name, d.department_code FROM wfiq_oltp.departments d
    WHERE NOT EXISTS (SELECT 1 FROM wfiq_dw.dim_department x WHERE x.department_id = d.department_id);
    SET v_rows = ROW_COUNT();
    CALL wfiq_staging.sp_log_etl('DW', 'dim_department', v_rows, 'OK', CONCAT('inserted=', v_rows), v_start);
END //

DROP PROCEDURE IF EXISTS wfiq_dw.sp_dw_load_dim_project //
CREATE PROCEDURE wfiq_dw.sp_dw_load_dim_project()
BEGIN
    DECLARE v_start DATETIME(6) DEFAULT NOW(6);
    DECLARE v_new INT DEFAULT 0;
    DECLARE v_upd INT DEFAULT 0;
    -- Type 1: overwrite changed attributes (incl. time-dependent is_overdue)
    UPDATE wfiq_dw.dim_project dp
    JOIN wfiq_oltp.projects p ON p.project_id = dp.project_id
    JOIN wfiq_oltp.departments d ON d.department_id = p.department_id
       SET dp.project_name = p.project_name, dp.owning_department = d.department_name, dp.status = p.status,
           dp.priority = p.priority, dp.start_date = p.start_date, dp.planned_end_date = p.planned_end_date,
           dp.actual_end_date = p.actual_end_date, dp.budget = p.budget,
           dp.is_overdue = (p.status IN ('Active','On Hold') AND p.planned_end_date < CURRENT_DATE)
     WHERE NOT (dp.project_name <=> p.project_name AND dp.owning_department <=> d.department_name
                AND dp.status <=> p.status AND dp.priority <=> p.priority AND dp.start_date <=> p.start_date
                AND dp.planned_end_date <=> p.planned_end_date AND dp.actual_end_date <=> p.actual_end_date
                AND dp.budget <=> p.budget
                AND dp.is_overdue <=> (p.status IN ('Active','On Hold') AND p.planned_end_date < CURRENT_DATE));
    SET v_upd = ROW_COUNT();
    INSERT INTO wfiq_dw.dim_project (project_id, project_code, project_name, owning_department, status, priority,
                                     start_date, planned_end_date, actual_end_date, budget, is_overdue)
    SELECT p.project_id, p.project_code, p.project_name, d.department_name, p.status, p.priority, p.start_date,
           p.planned_end_date, p.actual_end_date, p.budget,
           (p.status IN ('Active','On Hold') AND p.planned_end_date < CURRENT_DATE)
    FROM wfiq_oltp.projects p JOIN wfiq_oltp.departments d ON d.department_id = p.department_id
    WHERE NOT EXISTS (SELECT 1 FROM wfiq_dw.dim_project x WHERE x.project_id = p.project_id);
    SET v_new = ROW_COUNT();
    CALL wfiq_staging.sp_log_etl('DW', 'dim_project', v_new + v_upd, 'OK',
                                 CONCAT('inserted=', v_new, ', updated=', v_upd), v_start);
END //

DROP PROCEDURE IF EXISTS wfiq_dw.sp_dw_load_dim_employee //
CREATE PROCEDURE wfiq_dw.sp_dw_load_dim_employee()
BEGIN
    DECLARE v_start DATETIME(6) DEFAULT NOW(6);
    DECLARE v_new INT DEFAULT 0;
    DECLARE v_expired INT DEFAULT 0;
    DECLARE v_versions INT DEFAULT 0;
    DECLARE v_fixed INT DEFAULT 0;
    DECLARE v_t1 INT DEFAULT 0;
    DECLARE v_msg TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_msg = MESSAGE_TEXT;
        ROLLBACK;
        CALL wfiq_staging.sp_log_etl('DW', 'dim_employee', 0, 'FAILED', v_msg, v_start);
        COMMIT;
        RESIGNAL;
    END;

    -- current OLTP state per employee (+ latest survey via ROW_NUMBER, + change-detection hash)
    DROP TEMPORARY TABLE IF EXISTS tmp_emp_src;
    CREATE TEMPORARY TABLE tmp_emp_src AS
    WITH latest_survey AS (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.employee_id ORDER BY s.survey_date DESC, s.survey_id DESC) AS rn
        FROM wfiq_oltp.employee_surveys s
    )
    SELECT e.employee_id, e.employee_number, CONCAT(e.first_name, ' ', e.last_name) AS full_name, e.email, e.gender,
           CASE WHEN TIMESTAMPDIFF(YEAR, e.date_of_birth, CURRENT_DATE) < 25 THEN 'Under 25'
                WHEN TIMESTAMPDIFF(YEAR, e.date_of_birth, CURRENT_DATE) < 35 THEN '25-34'
                WHEN TIMESTAMPDIFF(YEAR, e.date_of_birth, CURRENT_DATE) < 45 THEN '35-44'
                WHEN TIMESTAMPDIFF(YEAR, e.date_of_birth, CURRENT_DATE) < 55 THEN '45-54'
                ELSE '55+' END AS age_band,
           e.marital_status, e.education_level, ef.field_name AS education_field, e.hire_date, e.termination_date,
           (e.termination_date IS NULL) AS is_active,
           CASE WHEN e.termination_date IS NULL THEN 'No' ELSE 'Yes' END AS attrition_flag,
           e.business_travel, CASE WHEN e.over_time = 1 THEN 'Yes' ELSE 'No' END AS over_time,
           e.distance_from_home, e.stock_option_level, e.years_since_last_promotion,
           ls.job_satisfaction, ls.environment_satisfaction, ls.work_life_balance,
           e.department_id, d.department_name, jr.role_title AS job_role, e.job_level, e.monthly_income,
           MD5(CONCAT_WS('|', e.department_id, e.job_role_id, e.job_level, e.monthly_income)) AS row_hash
    FROM wfiq_oltp.employees e
    JOIN wfiq_oltp.departments d ON d.department_id = e.department_id
    JOIN wfiq_oltp.job_roles jr ON jr.job_role_id = e.job_role_id
    JOIN wfiq_oltp.education_fields ef ON ef.education_field_id = e.education_field_id
    LEFT JOIN latest_survey ls ON ls.employee_id = e.employee_id AND ls.rn = 1;
    ALTER TABLE tmp_emp_src ADD PRIMARY KEY (employee_id);

    START TRANSACTION;

    -- A) brand-new employees: whole version timeline from employee_history
    INSERT INTO wfiq_dw.dim_employee
        (employee_id, employee_number, full_name, email, gender, age_band, marital_status, education_level,
         education_field, hire_date, termination_date, is_active, attrition_flag, business_travel, over_time,
         distance_from_home, stock_option_level, years_since_last_promotion, job_satisfaction,
         environment_satisfaction, work_life_balance, department_id, department_name, job_role, job_level,
         monthly_income, salary_band, row_hash, start_date, end_date, is_current, version_no)
    WITH timeline AS (
        SELECT h.employee_id, h.effective_date, h.department_id, h.job_role_id, h.job_level, h.monthly_income,
               ROW_NUMBER() OVER w AS version_no,
               LEAD(h.effective_date) OVER w AS next_effective
        FROM wfiq_oltp.employee_history h
        WHERE NOT EXISTS (SELECT 1 FROM wfiq_dw.dim_employee x WHERE x.employee_id = h.employee_id)
        WINDOW w AS (PARTITION BY h.employee_id ORDER BY h.effective_date)
    )
    SELECT s.employee_id, s.employee_number, s.full_name, s.email, s.gender, s.age_band, s.marital_status,
           s.education_level, s.education_field, s.hire_date, s.termination_date, s.is_active, s.attrition_flag,
           s.business_travel, s.over_time, s.distance_from_home, s.stock_option_level,
           s.years_since_last_promotion, s.job_satisfaction, s.environment_satisfaction, s.work_life_balance,
           t.department_id, d.department_name, jr.role_title, t.job_level, t.monthly_income,
           CASE WHEN t.monthly_income < 3000 THEN 'Under 3k' WHEN t.monthly_income < 6000 THEN '3k-6k'
                WHEN t.monthly_income < 10000 THEN '6k-10k' ELSE '10k+' END,
           MD5(CONCAT_WS('|', t.department_id, t.job_role_id, t.job_level, t.monthly_income)),
           t.effective_date, COALESCE(DATE_SUB(t.next_effective, INTERVAL 1 DAY), DATE '9999-12-31'),
           (t.next_effective IS NULL), t.version_no
    FROM timeline t
    JOIN tmp_emp_src s ON s.employee_id = t.employee_id
    JOIN wfiq_oltp.departments d ON d.department_id = t.department_id
    JOIN wfiq_oltp.job_roles jr ON jr.job_role_id = t.job_role_id
    ORDER BY t.employee_id, t.effective_date;
    SET v_new = ROW_COUNT();

    -- B) existing employees whose tracked attributes changed
    DROP TEMPORARY TABLE IF EXISTS tmp_emp_changes;
    CREATE TEMPORARY TABLE tmp_emp_changes AS
    SELECT c.employee_key, c.employee_id,
           CASE WHEN lh.effective_date > c.start_date THEN lh.effective_date
                WHEN CURRENT_DATE > c.start_date THEN CURRENT_DATE
                ELSE NULL END AS new_start               -- NULL => same-day correction, overwrite in place
    FROM wfiq_dw.dim_employee c
    JOIN tmp_emp_src s ON s.employee_id = c.employee_id
    LEFT JOIN (SELECT employee_id, MAX(effective_date) AS effective_date
               FROM wfiq_oltp.employee_history GROUP BY employee_id) lh ON lh.employee_id = c.employee_id
    WHERE c.is_current = 1 AND c.employee_id > 0 AND c.row_hash <> s.row_hash;
    ALTER TABLE tmp_emp_changes ADD PRIMARY KEY (employee_key);

    -- B1) expire the old current version
    UPDATE wfiq_dw.dim_employee d JOIN tmp_emp_changes c ON c.employee_key = d.employee_key
       SET d.end_date = DATE_SUB(c.new_start, INTERVAL 1 DAY), d.is_current = 0
     WHERE c.new_start IS NOT NULL;
    SET v_expired = ROW_COUNT();

    -- B2) insert the new current version
    INSERT INTO wfiq_dw.dim_employee
        (employee_id, employee_number, full_name, email, gender, age_band, marital_status, education_level,
         education_field, hire_date, termination_date, is_active, attrition_flag, business_travel, over_time,
         distance_from_home, stock_option_level, years_since_last_promotion, job_satisfaction,
         environment_satisfaction, work_life_balance, department_id, department_name, job_role, job_level,
         monthly_income, salary_band, row_hash, start_date, end_date, is_current, version_no)
    SELECT s.employee_id, s.employee_number, s.full_name, s.email, s.gender, s.age_band, s.marital_status,
           s.education_level, s.education_field, s.hire_date, s.termination_date, s.is_active, s.attrition_flag,
           s.business_travel, s.over_time, s.distance_from_home, s.stock_option_level,
           s.years_since_last_promotion, s.job_satisfaction, s.environment_satisfaction, s.work_life_balance,
           s.department_id, s.department_name, s.job_role, s.job_level, s.monthly_income,
           CASE WHEN s.monthly_income < 3000 THEN 'Under 3k' WHEN s.monthly_income < 6000 THEN '3k-6k'
                WHEN s.monthly_income < 10000 THEN '6k-10k' ELSE '10k+' END,
           s.row_hash, c.new_start, DATE '9999-12-31', 1, prev.version_no + 1
    FROM tmp_emp_changes c
    JOIN tmp_emp_src s ON s.employee_id = c.employee_id
    JOIN wfiq_dw.dim_employee prev ON prev.employee_key = c.employee_key
    WHERE c.new_start IS NOT NULL;
    SET v_versions = ROW_COUNT();

    -- B3) same-day corrections: overwrite the current version instead of creating a zero-length one
    UPDATE wfiq_dw.dim_employee d
    JOIN tmp_emp_changes c ON c.employee_key = d.employee_key
    JOIN tmp_emp_src s ON s.employee_id = c.employee_id
       SET d.department_id = s.department_id, d.department_name = s.department_name, d.job_role = s.job_role,
           d.job_level = s.job_level, d.monthly_income = s.monthly_income, d.row_hash = s.row_hash,
           d.salary_band = CASE WHEN s.monthly_income < 3000 THEN 'Under 3k' WHEN s.monthly_income < 6000 THEN '3k-6k'
                                WHEN s.monthly_income < 10000 THEN '6k-10k' ELSE '10k+' END
     WHERE c.new_start IS NULL;
    SET v_fixed = ROW_COUNT();

    -- C) Type 1 attributes: overwrite on every version of the employee when they differ
    UPDATE wfiq_dw.dim_employee d JOIN tmp_emp_src s ON s.employee_id = d.employee_id
       SET d.full_name = s.full_name, d.email = s.email, d.gender = s.gender, d.age_band = s.age_band,
           d.marital_status = s.marital_status, d.education_level = s.education_level,
           d.education_field = s.education_field, d.hire_date = s.hire_date,
           d.termination_date = s.termination_date, d.is_active = s.is_active, d.attrition_flag = s.attrition_flag,
           d.business_travel = s.business_travel, d.over_time = s.over_time,
           d.distance_from_home = s.distance_from_home, d.stock_option_level = s.stock_option_level,
           d.years_since_last_promotion = s.years_since_last_promotion, d.job_satisfaction = s.job_satisfaction,
           d.environment_satisfaction = s.environment_satisfaction, d.work_life_balance = s.work_life_balance
     WHERE d.employee_id > 0
       AND NOT (d.full_name <=> s.full_name AND d.email <=> s.email AND d.gender <=> s.gender
                AND d.age_band <=> s.age_band AND d.marital_status <=> s.marital_status
                AND d.education_level <=> s.education_level AND d.education_field <=> s.education_field
                AND d.hire_date <=> s.hire_date AND d.termination_date <=> s.termination_date
                AND d.is_active <=> s.is_active AND d.attrition_flag <=> s.attrition_flag
                AND d.business_travel <=> s.business_travel AND d.over_time <=> s.over_time
                AND d.distance_from_home <=> s.distance_from_home AND d.stock_option_level <=> s.stock_option_level
                AND d.years_since_last_promotion <=> s.years_since_last_promotion
                AND d.job_satisfaction <=> s.job_satisfaction
                AND d.environment_satisfaction <=> s.environment_satisfaction
                AND d.work_life_balance <=> s.work_life_balance);
    SET v_t1 = ROW_COUNT();

    CALL wfiq_staging.sp_log_etl('DW', 'dim_employee', v_new + v_versions + v_fixed + v_t1, 'OK',
        CONCAT('new_rows=', v_new, ', scd2_new_versions=', v_versions, ', expired=', v_expired,
               ', same_day_fixes=', v_fixed, ', type1_updates=', v_t1), v_start);
    COMMIT;
    DROP TEMPORARY TABLE IF EXISTS tmp_emp_changes;
    DROP TEMPORARY TABLE IF EXISTS tmp_emp_src;
END //

DROP PROCEDURE IF EXISTS wfiq_dw.sp_dw_load_fact_reviews //
CREATE PROCEDURE wfiq_dw.sp_dw_load_fact_reviews()
BEGIN
    DECLARE v_start DATETIME(6) DEFAULT NOW(6);
    DECLARE v_rows INT DEFAULT 0;
    DECLARE v_msg TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_msg = MESSAGE_TEXT;
        ROLLBACK;
        CALL wfiq_staging.sp_log_etl('DW', 'fact_performance_reviews', 0, 'FAILED', v_msg, v_start);
        COMMIT;
        RESIGNAL;
    END;
    START TRANSACTION;
    -- point-in-time join: the employee VERSION whose [start_date, end_date] contains the review date
    INSERT INTO wfiq_dw.fact_performance_reviews
        (review_id, employee_key, department_key, project_key, date_key, performance_rating, overall_score,
         goal_completion_pct, collaboration_score)
    SELECT r.review_id, COALESCE(pit.employee_key, cur.employee_key), COALESCE(dd.department_key, -1),
           COALESCE(dp.project_key, -1), COALESCE(dt.date_key, -1), r.performance_rating, r.overall_score,
           r.goal_completion_pct, r.collaboration_score
    FROM wfiq_oltp.reviews r
    LEFT JOIN wfiq_dw.fact_performance_reviews f ON f.review_id = r.review_id
    JOIN wfiq_dw.dim_employee cur ON cur.employee_id = r.employee_id AND cur.is_current = 1
    LEFT JOIN wfiq_dw.dim_employee pit ON pit.employee_id = r.employee_id
         AND r.review_date BETWEEN pit.start_date AND pit.end_date
    LEFT JOIN wfiq_dw.dim_department dd ON dd.department_id = COALESCE(pit.department_id, cur.department_id)
    LEFT JOIN wfiq_dw.dim_project dp ON dp.project_id = r.project_id
    LEFT JOIN wfiq_dw.dim_date dt ON dt.full_date = r.review_date
    WHERE f.review_id IS NULL;
    SET v_rows = ROW_COUNT();
    CALL wfiq_staging.sp_log_etl('DW', 'fact_performance_reviews', v_rows, 'OK', CONCAT('inserted=', v_rows), v_start);
    COMMIT;
END //

DROP PROCEDURE IF EXISTS wfiq_dw.sp_dw_load_fact_assignments //
CREATE PROCEDURE wfiq_dw.sp_dw_load_fact_assignments()
BEGIN
    DECLARE v_start DATETIME(6) DEFAULT NOW(6);
    DECLARE v_new INT DEFAULT 0;
    DECLARE v_upd INT DEFAULT 0;
    DECLARE v_msg TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_msg = MESSAGE_TEXT;
        ROLLBACK;
        CALL wfiq_staging.sp_log_etl('DW', 'fact_project_assignments', 0, 'FAILED', v_msg, v_start);
        COMMIT;
        RESIGNAL;
    END;
    START TRANSACTION;
    INSERT INTO wfiq_dw.fact_project_assignments
        (assignment_id, employee_key, department_key, project_key, start_date_key, end_date_key, allocation_pct,
         assigned_days, is_active)
    SELECT a.assignment_id, COALESCE(pit.employee_key, cur.employee_key), COALESCE(dd.department_key, -1),
           dp.project_key, COALESCE(sd.date_key, -1), COALESCE(ed.date_key, -1), a.allocation_pct,
           DATEDIFF(COALESCE(a.end_date, CURRENT_DATE), a.start_date),
           (a.end_date IS NULL OR a.end_date >= CURRENT_DATE)
    FROM wfiq_oltp.assignments a
    LEFT JOIN wfiq_dw.fact_project_assignments fa ON fa.assignment_id = a.assignment_id
    JOIN wfiq_dw.dim_project dp ON dp.project_id = a.project_id
    JOIN wfiq_dw.dim_employee cur ON cur.employee_id = a.employee_id AND cur.is_current = 1
    LEFT JOIN wfiq_dw.dim_employee pit ON pit.employee_id = a.employee_id
         AND a.start_date BETWEEN pit.start_date AND pit.end_date
    LEFT JOIN wfiq_dw.dim_department dd ON dd.department_id = COALESCE(pit.department_id, cur.department_id)
    LEFT JOIN wfiq_dw.dim_date sd ON sd.full_date = a.start_date
    LEFT JOIN wfiq_dw.dim_date ed ON ed.full_date = a.end_date
    WHERE fa.assignment_id IS NULL;
    SET v_new = ROW_COUNT();

    UPDATE wfiq_dw.fact_project_assignments fa
    JOIN wfiq_oltp.assignments a ON a.assignment_id = fa.assignment_id
    LEFT JOIN wfiq_dw.dim_date ed ON ed.full_date = a.end_date
       SET fa.end_date_key = COALESCE(ed.date_key, -1), fa.allocation_pct = a.allocation_pct,
           fa.assigned_days = DATEDIFF(COALESCE(a.end_date, CURRENT_DATE), a.start_date),
           fa.is_active = (a.end_date IS NULL OR a.end_date >= CURRENT_DATE)
     WHERE fa.end_date_key <> COALESCE(ed.date_key, -1) OR fa.allocation_pct <> a.allocation_pct
        OR fa.assigned_days <> DATEDIFF(COALESCE(a.end_date, CURRENT_DATE), a.start_date)
        OR fa.is_active <> (a.end_date IS NULL OR a.end_date >= CURRENT_DATE);
    SET v_upd = ROW_COUNT();
    CALL wfiq_staging.sp_log_etl('DW', 'fact_project_assignments', v_new + v_upd, 'OK',
                                 CONCAT('inserted=', v_new, ', updated=', v_upd), v_start);
    COMMIT;
END //

-- Full incremental DW refresh (idempotent). The app calls the smaller procedures below after single edits.
DROP PROCEDURE IF EXISTS wfiq_dw.sp_dw_run_etl //
CREATE PROCEDURE wfiq_dw.sp_dw_run_etl()
BEGIN
    CALL wfiq_dw.sp_dw_load_dim_date('2000-01-01', '2035-12-31');
    CALL wfiq_dw.sp_dw_load_dim_department();
    CALL wfiq_dw.sp_dw_load_dim_project();
    CALL wfiq_dw.sp_dw_load_dim_employee();
    CALL wfiq_dw.sp_dw_load_fact_reviews();
    CALL wfiq_dw.sp_dw_load_fact_assignments();
END //

DELIMITER ;
