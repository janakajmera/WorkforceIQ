"""Runs ETL stored procedures and DW synchronisation after OLTP edits."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..exceptions import WorkforceIQError
from .base_manager import BaseManager, guarded


@dataclass
class SyncResult:
    ok: bool
    message: str = ""


class EtlManager(BaseManager):
    @guarded
    def run_staging_to_oltp(self) -> None:
        self.call_procedure(f"{self.oltp}.sp_etl_stg_to_oltp")

    @guarded
    def run_full_dw_etl(self) -> None:
        self.call_procedure(f"{self.dw}.sp_dw_run_etl")

    def _sync(self, procs: list[str]) -> SyncResult:
        """The OLTP change is already committed; a failed sync is reported, and a later run catches up."""
        try:
            for p in procs:
                self.call_procedure(f"{self.dw}.{p}")
            return SyncResult(True, "Data warehouse updated.")
        except WorkforceIQError as exc:
            return SyncResult(False, f"Saved, but the data warehouse sync failed ({exc}). Run it from the Pipeline page.")

    def sync_employees(self) -> SyncResult:
        return self._sync(["sp_dw_load_dim_department", "sp_dw_load_dim_employee"])

    def sync_projects(self) -> SyncResult:
        return self._sync(["sp_dw_load_dim_department", "sp_dw_load_dim_project", "sp_dw_load_fact_assignments"])

    def sync_reviews(self) -> SyncResult:
        return self._sync(["sp_dw_load_dim_project", "sp_dw_load_fact_reviews"])

    @guarded
    def run_log(self, limit: int = 50) -> pd.DataFrame:
        return self.fetch_df(f"SELECT run_ts, layer, step, rows_affected, status, message, duration_ms "
                             f"FROM {self.stg}.etl_run_log ORDER BY run_id DESC LIMIT %s", (limit,))

    @guarded
    def layer_counts(self) -> pd.DataFrame:
        q = " UNION ALL ".join([
            f"SELECT 'Staging' AS layer, 'stg_employee_snapshot' AS tbl, COUNT(*) AS row_count FROM {self.stg}.stg_employee_snapshot",
            f"SELECT 'Staging', 'stg_review', COUNT(*) FROM {self.stg}.stg_review",
            f"SELECT 'OLTP', 'employees', COUNT(*) FROM {self.oltp}.employees",
            f"SELECT 'OLTP', 'employee_history', COUNT(*) FROM {self.oltp}.employee_history",
            f"SELECT 'OLTP', 'projects', COUNT(*) FROM {self.oltp}.projects",
            f"SELECT 'OLTP', 'assignments', COUNT(*) FROM {self.oltp}.assignments",
            f"SELECT 'OLTP', 'reviews', COUNT(*) FROM {self.oltp}.reviews",
            f"SELECT 'DW', 'dim_employee (versions)', COUNT(*) FROM {self.dw}.dim_employee",
            f"SELECT 'DW', 'dim_project', COUNT(*) FROM {self.dw}.dim_project",
            f"SELECT 'DW', 'fact_performance_reviews', COUNT(*) FROM {self.dw}.fact_performance_reviews",
            f"SELECT 'DW', 'fact_project_assignments', COUNT(*) FROM {self.dw}.fact_project_assignments"])
        return self.fetch_df(q)
