from __future__ import annotations

import pandas as pd

from ..entities import Review
from ..exceptions import NotFoundError, ValidationError
from .base_manager import BaseManager, guarded
from .etl_manager import EtlManager, SyncResult


class ReviewManager(BaseManager):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._etl = EtlManager()

    @guarded
    def submit(self, r: Review) -> tuple[int, SyncResult]:
        emp = self.fetch_one(
            f"SELECT hire_date, termination_date FROM {self.oltp}.employees WHERE employee_id=%s",
            (r.employee_id,),
        )
        if not emp:
            raise NotFoundError("Employee not found.")
        if r.review_date < emp["hire_date"]:
            raise ValidationError("Review date cannot be before the hire date.")
        if emp["termination_date"] and r.review_date > emp["termination_date"]:
            raise ValidationError("Review date cannot be after the termination date.")
        with self.transaction() as cur:  # UNIQUE(employee_id, review_year) -> DuplicateError if one exists
            cur.execute(
                f"INSERT INTO {self.oltp}.reviews (employee_id, project_id, review_date, performance_rating, "
                f"overall_score, goal_completion_pct, collaboration_score, comments) "
                f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    r.employee_id,
                    r.project_id,
                    r.review_date,
                    r.performance_rating,
                    r.overall_score,
                    r.goal_completion_pct,
                    r.collaboration_score,
                    r.comments,
                ),
            )
            rid = cur.lastrowid
        return rid, self._etl.sync_reviews()

    @guarded
    def search(
        self,
        term: str = "",
        rating: int | None = None,
        year: int | None = None,
        limit: int = 50,
    ) -> pd.DataFrame:
        clauses: list[str] = []
        params: list[object] = []

        q = term.strip()
        if q:
            like = f"%{q}%"
            clauses.append(
                "(CONCAT(e.first_name, ' ', e.last_name) LIKE %s "
                "OR e.employee_number LIKE %s "
                "OR e.email LIKE %s "
                "OR p.project_code LIKE %s "
                "OR r.comments LIKE %s)"
            )
            params.extend([like, like, like, like, like])

        if rating is not None:
            clauses.append("r.performance_rating = %s")
            params.append(rating)

        if year is not None:
            clauses.append("YEAR(r.review_date) = %s")
            params.append(year)

        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        params.append(limit)

        return self.fetch_df(
            f"SELECT r.review_id, e.employee_number, CONCAT(e.first_name, ' ', e.last_name) AS name, "
            f"r.review_date, p.project_code, r.performance_rating, r.overall_score, "
            f"r.goal_completion_pct, r.collaboration_score, r.comments "
            f"FROM {self.oltp}.reviews r "
            f"JOIN {self.oltp}.employees e ON e.employee_id = r.employee_id "
            f"LEFT JOIN {self.oltp}.projects p ON p.project_id = r.project_id "
            f"{where}"
            f"ORDER BY r.review_date DESC, r.review_id DESC LIMIT %s",
            tuple(params),
        )

    @guarded
    def for_employee(self, employee_id: int) -> pd.DataFrame:
        return self.fetch_df(
            f"SELECT review_date, performance_rating, overall_score, goal_completion_pct, "
            f"collaboration_score, comments FROM {self.oltp}.reviews WHERE employee_id=%s "
            f"ORDER BY review_date DESC",
            (employee_id,),
        )

    @guarded
    def recent(self, limit: int = 20) -> pd.DataFrame:
        return self.fetch_df(
            f"SELECT r.review_id, e.employee_number, CONCAT(e.first_name,' ',e.last_name) AS name, r.review_date, "
            f"r.performance_rating, r.overall_score FROM {self.oltp}.reviews r "
            f"JOIN {self.oltp}.employees e ON e.employee_id = r.employee_id ORDER BY r.review_id DESC LIMIT %s",
            (limit,),
        )