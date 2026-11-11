"""Read-only analytics against the star schema / DW views."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .base_manager import BaseManager, guarded


class AnalyticsManager(BaseManager):
    @guarded
    def kpis(self) -> dict:
        row = self.fetch_one(
            f"SELECT (SELECT COUNT(*) FROM {self.dw}.dim_employee WHERE is_current=1 AND is_active=1 AND employee_key<>-1) AS headcount, "
            f"(SELECT ROUND(100*AVG(attrition_flag='Yes'),1) FROM {self.dw}.dim_employee WHERE is_current=1 AND employee_key<>-1) AS attrition_pct, "
            f"(SELECT ROUND(AVG(monthly_income)) FROM {self.dw}.dim_employee WHERE is_current=1 AND is_active=1 AND employee_key<>-1) AS avg_income, "
            f"(SELECT COUNT(*) FROM {self.dw}.dim_project WHERE status='Active') AS active_projects, "
            f"(SELECT COUNT(*) FROM {self.dw}.vw_employee_workload WHERE is_overallocated=1) AS overallocated")
        return row or {}

    @guarded
    def headcount_by_department(self) -> pd.DataFrame:
        return self.fetch_df(f"SELECT department_name, COUNT(*) AS headcount FROM {self.dw}.dim_employee "
                             f"WHERE is_current=1 AND is_active=1 AND employee_key<>-1 GROUP BY department_name")

    @guarded
    def yoy_performance(self) -> pd.DataFrame:
        return self.fetch_df(f"SELECT * FROM {self.dw}.vw_yoy_performance ORDER BY department_name, review_year")

    @guarded
    def review_years(self) -> list[int]:
        return [int(r["review_year"]) for r in self.fetch_all(
            f"SELECT DISTINCT review_year FROM {self.dw}.vw_employee_year_performance ORDER BY 1 DESC")]

    @guarded
    def top_performers(self, year: int, department: Optional[str] = None, top_n: int = 10) -> pd.DataFrame:
        """DENSE_RANK() is computed inside the view; here we only filter on it."""
        rank_col = "dept_rank" if department else "company_rank"
        where, params = "review_year = %s", [year]
        if department:
            where += " AND department_name = %s"; params.append(department)
        return self.fetch_df(
            f"SELECT {rank_col} AS `rank`, employee_number, full_name, department_name, job_role, "
            f"ROUND(avg_score,2) AS score, ROUND(avg_rating,1) AS rating, ROUND(goal_pct) AS goal_pct "
            f"FROM {self.dw}.vw_employee_year_performance WHERE {where} AND {rank_col} <= %s "
            f"ORDER BY {rank_col}, full_name LIMIT 200", params + [top_n])

    @guarded
    def attrition_by_band(self) -> pd.DataFrame:
        return self.fetch_df(
            f"SELECT risk_band, COUNT(*) AS employees, ROUND(100*AVG(attrition_flag='Yes'),1) AS actual_attrition_pct "
            f"FROM {self.dw}.vw_attrition_risk GROUP BY risk_band")

    @guarded
    def risk_by_department(self) -> pd.DataFrame:
        return self.fetch_df(f"SELECT department_name, risk_band, COUNT(*) AS employees FROM {self.dw}.vw_attrition_risk "
                             f"WHERE is_active=1 GROUP BY department_name, risk_band")

    @guarded
    def at_risk_employees(self, limit: int = 25) -> pd.DataFrame:
        return self.fetch_df(
            f"SELECT employee_number, full_name, department_name, job_role, risk_score, risk_band, over_time "
            f"FROM {self.dw}.vw_attrition_risk WHERE is_active=1 ORDER BY risk_score DESC, employee_number LIMIT %s",
            (limit,))

    @guarded
    def attrition_trend(self) -> pd.DataFrame:
        return self.fetch_df(f"SELECT year_num, SUM(leavers) AS leavers, SUM(headcount) AS headcount, "
                             f"ROUND(100*SUM(leavers)/SUM(headcount),2) AS attrition_pct "
                             f"FROM {self.dw}.vw_attrition_by_year GROUP BY year_num ORDER BY year_num")

    @guarded
    def project_bottlenecks(self, limit: int = 20) -> pd.DataFrame:
        return self.fetch_df(
            f"SELECT bottleneck_rank, project_code, project_name, owning_department, status, priority, headcount, "
            f"overallocated_members, days_overdue, bottleneck_score FROM {self.dw}.vw_project_bottlenecks "
            f"ORDER BY bottleneck_rank, project_code LIMIT %s", (limit,))

    @guarded
    def overallocated_by_department(self) -> pd.DataFrame:
        return self.fetch_df(f"SELECT department_name, SUM(is_overallocated) AS overallocated, COUNT(*) AS staffed "
                             f"FROM {self.dw}.vw_employee_workload GROUP BY department_name")

    @guarded
    def employee_timeline(self, employee_id: int) -> pd.DataFrame:
        """SCD Type 2 versions of one employee straight from dim_employee."""
        return self.fetch_df(
            f"SELECT version_no, department_name, job_role, job_level, monthly_income, start_date, end_date, is_current "
            f"FROM {self.dw}.dim_employee WHERE employee_id=%s ORDER BY start_date", (employee_id,))

    @guarded
    def integrity_checks(self) -> pd.DataFrame:
        d = self.dw
        checks = {
            "Employees without exactly one current version":
                f"SELECT COUNT(*) FROM (SELECT employee_id FROM {d}.dim_employee WHERE employee_id>0 "
                f"GROUP BY employee_id HAVING SUM(is_current)<>1) x",
            "Overlapping or gapped SCD2 versions":
                f"SELECT COUNT(*) FROM (SELECT DATE_ADD(end_date, INTERVAL 1 DAY) AS e, "
                f"LEAD(start_date) OVER (PARTITION BY employee_id ORDER BY start_date) AS n "
                f"FROM {d}.dim_employee WHERE employee_id>0) x WHERE n IS NOT NULL AND e<>n",
            "Reviews missing from fact table":
                f"SELECT (SELECT COUNT(*) FROM {self.oltp}.reviews)-(SELECT COUNT(*) FROM {d}.fact_performance_reviews)",
            "Facts pointing at unknown employee":
                f"SELECT COUNT(*) FROM {d}.fact_performance_reviews WHERE employee_key=-1",
        }
        rows = [{"check": name, "violations": int(list(self.fetch_one(sql).values())[0])}
                for name, sql in checks.items()]
        df = pd.DataFrame(rows)
        df["status"] = df["violations"].map(lambda v: "PASS" if v == 0 else "FAIL")
        return df
