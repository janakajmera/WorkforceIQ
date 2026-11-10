-- =====================================================================================
-- WorkforceIQ | 08 | DW analytics views (CTEs + window functions). The Streamlit app only filters these.
-- =====================================================================================

-- Per employee per year + DENSE_RANK inside department / company  (Top performers)
CREATE OR REPLACE VIEW wfiq_dw.vw_employee_year_performance AS
WITH yearly AS (
    SELECT e.employee_id, e.employee_number, e.full_name, dep.department_name, dd.year_num AS review_year,
           MAX(e.job_role) AS job_role, AVG(f.overall_score) AS avg_score, AVG(f.performance_rating) AS avg_rating,
           AVG(f.goal_completion_pct) AS goal_pct, COUNT(*) AS review_count
    FROM wfiq_dw.fact_performance_reviews f
    JOIN wfiq_dw.dim_employee   e   ON e.employee_key = f.employee_key
    JOIN wfiq_dw.dim_department dep ON dep.department_key = f.department_key
    JOIN wfiq_dw.dim_date       dd  ON dd.date_key = f.date_key
    WHERE f.employee_key <> -1
    GROUP BY e.employee_id, e.employee_number, e.full_name, dep.department_name, dd.year_num
)
SELECT y.*,
       DENSE_RANK() OVER (PARTITION BY y.department_name, y.review_year ORDER BY y.avg_score DESC) AS dept_rank,
       DENSE_RANK() OVER (PARTITION BY y.review_year ORDER BY y.avg_score DESC)                    AS company_rank
FROM yearly y;

-- Year-over-year trend per department (+ company total) using LAG
CREATE OR REPLACE VIEW wfiq_dw.vw_yoy_performance AS
WITH dept_year AS (
    SELECT dep.department_name, dd.year_num AS review_year, AVG(f.overall_score) AS avg_score,
           AVG(f.performance_rating) AS avg_rating, COUNT(*) AS reviews,
           AVG(f.performance_rating >= 4) AS high_performer_share
    FROM wfiq_dw.fact_performance_reviews f
    JOIN wfiq_dw.dim_department dep ON dep.department_key = f.department_key
    JOIN wfiq_dw.dim_date dd ON dd.date_key = f.date_key
    WHERE f.employee_key <> -1
    GROUP BY dep.department_name, dd.year_num
    UNION ALL
    SELECT 'All departments', dd.year_num, AVG(f.overall_score), AVG(f.performance_rating), COUNT(*),
           AVG(f.performance_rating >= 4)
    FROM wfiq_dw.fact_performance_reviews f
    JOIN wfiq_dw.dim_date dd ON dd.date_key = f.date_key
    WHERE f.employee_key <> -1
    GROUP BY dd.year_num
)
SELECT dy.*, LAG(dy.avg_score) OVER (PARTITION BY dy.department_name ORDER BY dy.review_year) AS prev_score,
       dy.avg_score - LAG(dy.avg_score) OVER (PARTITION BY dy.department_name ORDER BY dy.review_year) AS yoy_change
FROM dept_year dy;

-- Attrition risk: transparent additive score on current employee versions. Protected attributes
-- (age, gender, marital status) are deliberately NOT used.
CREATE OR REPLACE VIEW wfiq_dw.vw_attrition_risk AS
WITH latest_review AS (
    SELECT e.employee_id, f.performance_rating,
           ROW_NUMBER() OVER (PARTITION BY e.employee_id ORDER BY dd.full_date DESC) AS rn
    FROM wfiq_dw.fact_performance_reviews f
    JOIN wfiq_dw.dim_employee e ON e.employee_key = f.employee_key
    JOIN wfiq_dw.dim_date dd ON dd.date_key = f.date_key
),
cur AS (
    SELECT e.*, AVG(e.monthly_income) OVER (PARTITION BY e.department_name, e.job_role, e.job_level) AS peer_avg_income
    FROM wfiq_dw.dim_employee e
    WHERE e.is_current = 1 AND e.employee_key <> -1
),
scored AS (
    SELECT c.employee_id, c.employee_number, c.full_name, c.department_name, c.job_role, c.job_level,
           c.monthly_income, c.peer_avg_income, c.attrition_flag, c.is_active, c.over_time, lr.performance_rating AS last_rating,
           LEAST(100,
               (CASE WHEN c.over_time = 'Yes' THEN 25 ELSE 0 END)
             + (CASE WHEN c.business_travel = 'Travel_Frequently' THEN 10 ELSE 0 END)
             + (CASE WHEN c.job_satisfaction <= 2 THEN 15 ELSE 0 END)
             + (CASE WHEN c.environment_satisfaction <= 2 THEN 8 ELSE 0 END)
             + (CASE WHEN c.work_life_balance <= 2 THEN 8 ELSE 0 END)
             + (CASE WHEN c.monthly_income < 0.85 * c.peer_avg_income THEN 12 ELSE 0 END)
             + (CASE WHEN c.years_since_last_promotion >= 5 THEN 8 ELSE 0 END)
             + (CASE WHEN c.distance_from_home > 20 THEN 4 ELSE 0 END)
             + (CASE WHEN c.stock_option_level = 0 THEN 10 ELSE 0 END)
             + (CASE WHEN lr.performance_rating <= 2 THEN 10 ELSE 0 END)) AS risk_score
    FROM cur c LEFT JOIN latest_review lr ON lr.employee_id = c.employee_id AND lr.rn = 1
)
SELECT s.*, CASE WHEN s.risk_score >= 50 THEN 'High' WHEN s.risk_score >= 30 THEN 'Medium' ELSE 'Low' END AS risk_band,
       NTILE(10) OVER (ORDER BY s.risk_score) AS risk_decile
FROM scored s;

-- Workload per active employee (SUM OVER) and over-allocation flag
CREATE OR REPLACE VIEW wfiq_dw.vw_employee_workload AS
WITH active AS (
    SELECT e.employee_id, SUM(fa.allocation_pct) AS total_allocation, COUNT(*) AS active_projects
    FROM wfiq_dw.fact_project_assignments fa
    JOIN wfiq_dw.dim_employee e ON e.employee_key = fa.employee_key
    WHERE fa.is_active = 1
    GROUP BY e.employee_id
)
SELECT c.employee_id, c.full_name, c.department_name, c.job_role, a.total_allocation, a.active_projects,
       (a.total_allocation > 100) AS is_overallocated,
       RANK() OVER (PARTITION BY c.department_name ORDER BY a.total_allocation DESC) AS dept_workload_rank
FROM active a JOIN wfiq_dw.dim_employee c ON c.employee_id = a.employee_id AND c.is_current = 1
WHERE c.is_active = 1;

-- Project bottlenecks: overdue + over-allocated teams, ranked with DENSE_RANK
CREATE OR REPLACE VIEW wfiq_dw.vw_project_bottlenecks AS
WITH team AS (
    SELECT fa.project_key, COUNT(DISTINCT e.employee_id) AS headcount, SUM(fa.allocation_pct) / 100 AS total_fte,
           COUNT(DISTINCT CASE WHEN w.is_overallocated = 1 THEN e.employee_id END) AS overallocated_members
    FROM wfiq_dw.fact_project_assignments fa
    JOIN wfiq_dw.dim_employee e ON e.employee_key = fa.employee_key
    LEFT JOIN wfiq_dw.vw_employee_workload w ON w.employee_id = e.employee_id
    WHERE fa.is_active = 1
    GROUP BY fa.project_key
),
scored AS (
    SELECT p.project_key, p.project_code, p.project_name, p.owning_department, p.status, p.priority,
           p.planned_end_date, p.budget, t.headcount, t.total_fte, t.overallocated_members,
           GREATEST(0, DATEDIFF(CURRENT_DATE, p.planned_end_date)) AS days_overdue,
           t.overallocated_members / NULLIF(t.headcount, 0) AS overallocated_share
    FROM wfiq_dw.dim_project p JOIN team t ON t.project_key = p.project_key
    WHERE p.status IN ('Active', 'On Hold') AND t.headcount >= 3   -- ignore tiny teams
)
SELECT s.*, ROUND(LEAST(100, (CASE WHEN s.days_overdue > 0 THEN 40 ELSE 0 END)
                           + 60 * COALESCE(s.overallocated_share, 0)), 1) AS bottleneck_score,
       DENSE_RANK() OVER (ORDER BY (CASE WHEN s.days_overdue > 0 THEN 40 ELSE 0 END)
                                   + 60 * COALESCE(s.overallocated_share, 0) DESC) AS bottleneck_rank
FROM scored s;

-- Leavers vs headcount by year (uses dim_date for the year list)
CREATE OR REPLACE VIEW wfiq_dw.vw_attrition_by_year AS
WITH years AS (
    SELECT DISTINCT year_num FROM wfiq_dw.dim_date WHERE year_num BETWEEN 2020 AND YEAR(CURRENT_DATE)
)
SELECT y.year_num AS year_num, e.department_name,
       SUM(YEAR(e.termination_date) = y.year_num) AS leavers,
       COUNT(*) AS headcount
FROM years y
JOIN wfiq_dw.dim_employee e ON e.is_current = 1 AND e.employee_key <> -1
     AND e.hire_date <= MAKEDATE(y.year_num, 365)
     AND (e.termination_date IS NULL OR YEAR(e.termination_date) >= y.year_num)
GROUP BY y.year_num, e.department_name;
