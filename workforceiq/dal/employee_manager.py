"""CRUD for employees (OLTP) + DW synchronisation (SCD2 happens in the DW ETL)."""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from ..entities import Employee
from ..exceptions import NotFoundError
from .base_manager import BaseManager, guarded
from .etl_manager import EtlManager, SyncResult


class EmployeeManager(BaseManager):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._etl = EtlManager()

    # -- lookups
    @guarded
    def departments(self) -> pd.DataFrame:
        return self.fetch_df(f"SELECT department_id, department_name FROM {self.oltp}.departments ORDER BY 2")

    @guarded
    def job_roles(self) -> pd.DataFrame:
        return self.fetch_df(f"SELECT job_role_id, role_title FROM {self.oltp}.job_roles ORDER BY 2")

    @guarded
    def education_fields(self) -> pd.DataFrame:
        return self.fetch_df(f"SELECT education_field_id, field_name FROM {self.oltp}.education_fields ORDER BY 2")

    # -- read
    @guarded
    def search(
        self,
        term: str = "",
        department_id: Optional[int] = None,
        active_only: bool = True,
        limit: int = 50,
        offset: int = 0
    ) -> pd.DataFrame:
        where, params = ["1=1"], []

        term = term.strip()

        if term:
            if term.isdigit():
                where.append("e.employee_number = %s")
                params.append(int(term))
            else:
                like = f"%{term}%"
                where.append(
                    """
                    (
                        LOWER(e.first_name) LIKE LOWER(%s)
                        OR LOWER(e.last_name) LIKE LOWER(%s)
                        OR LOWER(CONCAT(TRIM(COALESCE(e.first_name, '')), ' ', TRIM(COALESCE(e.last_name, '')))) LIKE LOWER(%s)
                        OR LOWER(e.email) LIKE LOWER(%s)
                    )
                    """
                )
                params.extend([like, like, like, like])

        if department_id is not None:
            where.append("e.department_id = %s")
            params.append(department_id)

        if active_only:
            where.append("e.termination_date IS NULL")

        sql = (
            f"SELECT e.employee_id, e.employee_number, "
            f"CONCAT(TRIM(COALESCE(e.first_name, '')), ' ', TRIM(COALESCE(e.last_name, ''))) AS name, "
            f"e.email, d.department_name, jr.role_title AS job_role, "
            f"e.job_level, e.monthly_income, e.hire_date, "
            f"e.termination_date "
            f"FROM {self.oltp}.employees e "
            f"JOIN {self.oltp}.departments d "
            f"ON d.department_id = e.department_id "
            f"JOIN {self.oltp}.job_roles jr "
            f"ON jr.job_role_id = e.job_role_id "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY e.employee_number "
            f"LIMIT %s OFFSET %s"
        )

        return self.fetch_df(sql, params + [limit, offset])

    @guarded
    def get(self, employee_id: int) -> Employee:
        row = self.fetch_one(f"SELECT * FROM {self.oltp}.employees WHERE employee_id = %s", (employee_id,))
        if not row:
            raise NotFoundError(f"Employee {employee_id} not found.")
        return Employee.from_row(row)

    @guarded
    def oltp_history(self, employee_id: int) -> pd.DataFrame:
        return self.fetch_df(
            f"SELECT h.effective_date, d.department_name, jr.role_title AS job_role, h.job_level, "
            f"h.monthly_income, h.change_type FROM {self.oltp}.employee_history h "
            f"JOIN {self.oltp}.departments d ON d.department_id = h.department_id "
            f"JOIN {self.oltp}.job_roles jr ON jr.job_role_id = h.job_role_id "
            f"WHERE h.employee_id = %s ORDER BY h.effective_date", (employee_id,))

    # -- write
    @guarded
    def onboard(self, emp: Employee) -> tuple[dict, SyncResult]:
        rows = self.call_procedure(f"{self.oltp}.sp_oltp_onboard_employee", emp.to_onboard_params())
        return rows[0], self._etl.sync_employees()

    @guarded
    def change_job(self, employee_id: int, department_id: int, job_role_id: int, job_level: int,
                   monthly_income: int, effective_date: date) -> tuple[str, SyncResult]:
        """Department/role/level/salary change -> OLTP history row -> SCD Type 2 version in the DW."""
        rows = self.call_procedure(f"{self.oltp}.sp_oltp_change_employee_job",
                                   (employee_id, department_id, job_role_id, job_level, monthly_income,
                                    effective_date, None))
        return rows[0]["change_type"], self._etl.sync_employees()

    @guarded
    def terminate(self, employee_id: int, termination_date: date) -> SyncResult:
        self.call_procedure(f"{self.oltp}.sp_oltp_terminate_employee", (employee_id, termination_date))
        return self._etl.sync_employees()
