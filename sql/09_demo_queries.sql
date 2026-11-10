-- WorkforceIQ | 09 | Demo / verification queries (run in MySQL Workbench)
-- 1. SCD2 timeline of one employee
SELECT employee_key, employee_id, department_name, job_role, job_level, monthly_income,
       start_date, end_date, is_current, version_no
FROM wfiq_dw.dim_employee WHERE employee_id = 1 ORDER BY start_date;

-- 2. Integrity: every employee must have exactly one current version
SELECT employee_id, SUM(is_current) AS current_rows FROM wfiq_dw.dim_employee
WHERE employee_id > 0 GROUP BY employee_id HAVING SUM(is_current) <> 1;

-- 3. Integrity: no overlapping / gapped versions (LEAD)
WITH v AS (SELECT employee_id, start_date, end_date,
                  LEAD(start_date) OVER (PARTITION BY employee_id ORDER BY start_date) AS next_start
           FROM wfiq_dw.dim_employee WHERE employee_id > 0)
SELECT * FROM v WHERE next_start IS NOT NULL AND DATE_ADD(end_date, INTERVAL 1 DAY) <> next_start;

-- 4. Top 5 performers per department for the latest year (DENSE_RANK)
SELECT department_name, review_year, full_name, ROUND(avg_score, 2) AS score, dept_rank
FROM wfiq_dw.vw_employee_year_performance
WHERE review_year = (SELECT MAX(review_year) FROM wfiq_dw.vw_employee_year_performance) AND dept_rank <= 5
ORDER BY department_name, dept_rank;

-- 5. YoY trend (LAG)
SELECT department_name, review_year, ROUND(avg_score, 2) AS avg_score, ROUND(yoy_change, 2) AS yoy_change
FROM wfiq_dw.vw_yoy_performance ORDER BY department_name, review_year;

-- 6. Attrition by risk band (sanity check of the risk score)
SELECT risk_band, COUNT(*) AS employees, ROUND(100 * AVG(attrition_flag = 'Yes'), 1) AS actual_attrition_pct
FROM wfiq_dw.vw_attrition_risk GROUP BY risk_band;

-- 7. Top bottleneck projects
SELECT project_code, project_name, status, headcount, overallocated_members, days_overdue, bottleneck_score
FROM wfiq_dw.vw_project_bottlenecks WHERE bottleneck_rank <= 10 ORDER BY bottleneck_rank;
