from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from ..entities import PROJECT_STATUSES, Project
from ..exceptions import NotFoundError, ValidationError
from .base_manager import BaseManager, guarded
from .etl_manager import EtlManager, SyncResult


class ProjectManager(BaseManager):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._etl = EtlManager()

    @guarded
    def create(self, p: Project) -> tuple[int, SyncResult]:
        with self.transaction() as cur:
            cur.execute(f"INSERT INTO {self.oltp}.projects (project_code, project_name, department_id, status, "
                        f"priority, start_date, planned_end_date, actual_end_date, budget) "
                        f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (p.project_code, p.project_name, p.department_id, p.status, p.priority, p.start_date,
                         p.planned_end_date, p.actual_end_date, p.budget))
            pid = cur.lastrowid
        return pid, self._etl.sync_projects()

    @guarded
    def list_projects(self, status: Optional[str] = None, department_id: Optional[int] = None,
                      limit: int = 200) -> pd.DataFrame:
        where, params = ["1=1"], []
        if status:
            where.append("p.status = %s"); params.append(status)
        if department_id:
            where.append("p.department_id = %s"); params.append(department_id)
        return self.fetch_df(
            f"SELECT p.project_id, p.project_code, p.project_name, d.department_name, p.status, p.priority, "
            f"p.start_date, p.planned_end_date, p.actual_end_date, p.budget FROM {self.oltp}.projects p "
            f"JOIN {self.oltp}.departments d ON d.department_id = p.department_id "
            f"WHERE {' AND '.join(where)} ORDER BY p.project_id DESC LIMIT %s", params + [limit])

    @guarded
    def update_status(self, project_id: int, status: str, actual_end_date: Optional[date] = None) -> SyncResult:
        if status not in PROJECT_STATUSES:
            raise ValidationError("Invalid status.")
        if self.execute(f"UPDATE {self.oltp}.projects SET status=%s, actual_end_date=%s WHERE project_id=%s",
                        (status, actual_end_date, project_id)) == 0:
            raise NotFoundError("Project not found (or nothing changed).")
        return self._etl.sync_projects()

    @guarded
    def assign(self, employee_id: int, project_id: int, role: str, allocation_pct: int, start_date: date,
               end_date: Optional[date] = None) -> tuple[int, SyncResult]:
        if not 1 <= allocation_pct <= 100:
            raise ValidationError("Allocation must be between 1 and 100%.")
        emp = self.fetch_one(f"SELECT hire_date, termination_date FROM {self.oltp}.employees WHERE employee_id=%s",
                             (employee_id,))
        if not emp:
            raise NotFoundError("Employee not found.")
        if emp["termination_date"] is not None:
            raise ValidationError("Cannot assign a former employee.")
        if start_date < emp["hire_date"]:
            raise ValidationError("Assignment cannot start before the hire date.")
        with self.transaction() as cur:
            cur.execute(f"INSERT INTO {self.oltp}.assignments (employee_id, project_id, role_on_project, "
                        f"allocation_pct, start_date, end_date) VALUES (%s,%s,%s,%s,%s,%s)",
                        (employee_id, project_id, role.strip() or "Contributor", allocation_pct, start_date, end_date))
            aid = cur.lastrowid
        return aid, self._etl.sync_projects()

    @guarded
    def active_allocation(self, employee_id: int) -> int:
        row = self.fetch_one(f"SELECT COALESCE(SUM(allocation_pct),0) AS total FROM {self.oltp}.assignments "
                             f"WHERE employee_id=%s AND (end_date IS NULL OR end_date >= CURRENT_DATE)",
                             (employee_id,))
        return int(row["total"])

    @guarded
    def list_assignments(self, project_id: int) -> pd.DataFrame:
        return self.fetch_df(
            f"SELECT a.assignment_id, e.employee_number, CONCAT(e.first_name,' ',e.last_name) AS name, "
            f"a.role_on_project, a.allocation_pct, a.start_date, a.end_date FROM {self.oltp}.assignments a "
            f"JOIN {self.oltp}.employees e ON e.employee_id = a.employee_id WHERE a.project_id=%s "
            f"ORDER BY a.start_date DESC LIMIT 200", (project_id,))
