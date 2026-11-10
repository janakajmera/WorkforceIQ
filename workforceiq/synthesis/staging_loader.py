"""Bulk-loads a SynthesisResult into the MySQL staging schema (chunked multi-row INSERTs)."""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import numpy as np
import pandas as pd

from ..db_manager import DatabaseConnection
from ..exceptions import DatabaseError
from .synthesizer import SynthesisResult

log = logging.getLogger(__name__)

_COMMON = ["batch_id", "ingested_at"]
TABLE_SPECS: dict[str, tuple[str, list[str]]] = {
    # staging table -> (SynthesisResult attribute, columns)
    "stg_employee_snapshot": ("employees", [
        "source_employee_number", "first_name", "last_name", "email", "snapshot_date", "hire_date",
        "termination_date", "age", "attrition", "business_travel", "daily_rate", "department",
        "distance_from_home", "education", "education_field", "environment_satisfaction", "gender",
        "hourly_rate", "job_involvement", "job_level", "job_role", "job_satisfaction", "marital_status",
        "monthly_income", "monthly_rate", "num_companies_worked", "over_time", "percent_salary_hike",
        "performance_rating", "relationship_satisfaction", "stock_option_level", "total_working_years",
        "training_times_last_year", "work_life_balance", "years_at_company", "years_in_current_role",
        "years_since_last_promotion", "years_with_curr_manager", "record_source"] + _COMMON),
    "stg_employee_history": ("history", [
        "source_employee_number", "effective_date", "department", "job_role", "job_level", "monthly_income",
        "change_type"] + _COMMON),
    "stg_project": ("projects", [
        "project_code", "project_name", "department", "status", "priority", "start_date", "planned_end_date",
        "actual_end_date", "budget"] + _COMMON),
    "stg_assignment": ("assignments", [
        "source_employee_number", "project_code", "role_on_project", "allocation_pct", "start_date",
        "end_date"] + _COMMON),
    "stg_review": ("reviews", [
        "source_employee_number", "project_code", "review_date", "performance_rating", "overall_score",
        "goal_completion_pct", "collaboration_score"] + _COMMON),
}


def _column_values(series: pd.Series, name: str) -> list:
    """Convert a pandas column to plain Python objects PyMySQL can escape (NaN/NaT -> None)."""
    if pd.api.types.is_datetime64_any_dtype(series):
        if name == "ingested_at":
            vals = [None if pd.isna(v) else v.to_pydatetime() for v in series]
        else:
            vals = [None if pd.isna(v) else v.date() for v in series]
        return vals
    obj = series.astype(object).where(series.notna(), None)
    return [v.item() if isinstance(v, np.generic) else v for v in obj]


def dataframe_to_rows(df: pd.DataFrame, columns: list[str]) -> list[tuple]:
    cols = [_column_values(df[c], c) for c in columns]
    return list(zip(*cols))


class StagingLoader(DatabaseConnection):
    """Loads synthesized frames into ``<staging_schema>.stg_*`` tables."""

    def load(self, result: SynthesisResult, truncate: bool = True, chunk_size: int = 10_000,
             progress: Optional[Callable[[str], None]] = None) -> dict[str, int]:
        say = progress or (lambda m: log.info(m))
        schema = self.settings.staging_schema
        counts: dict[str, int] = {}
        try:
            for table, (attr, columns) in TABLE_SPECS.items():
                df: pd.DataFrame = getattr(result, attr)
                if truncate:
                    self.execute(f"TRUNCATE TABLE {schema}.{table}")
                sql = (f"INSERT INTO {schema}.{table} ({', '.join(columns)}) "
                       f"VALUES ({', '.join(['%s'] * len(columns))})")
                started, loaded = time.time(), 0
                for lo in range(0, len(df), chunk_size):
                    rows = dataframe_to_rows(df.iloc[lo:lo + chunk_size], columns)
                    self.execute_many(sql, rows)
                    loaded += len(rows)
                counts[table] = loaded
                say(f"  staged {loaded:>9,} rows -> {table}  ({time.time() - started:.1f}s)")
        except DatabaseError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DatabaseError(f"Staging load failed: {exc}") from exc
        return counts
