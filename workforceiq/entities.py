"""Entity classes with encapsulated, validated attributes."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from .exceptions import ValidationError

GENDERS = ("Male", "Female", "Other")
MARITAL = ("Single", "Married", "Divorced")
TRAVEL = ("Non-Travel", "Travel_Rarely", "Travel_Frequently")
PROJECT_STATUSES = ("Planned", "Active", "On Hold", "Completed", "Cancelled")
PRIORITIES = ("Low", "Medium", "High", "Critical")
RATING_LABELS = {1: "Unsatisfactory", 2: "Needs improvement", 3: "Meets expectations",
                 4: "Exceeds expectations", 5: "Outstanding"}


def _req_text(value: Any, name: str, max_len: int = 80) -> str:
    text = (value or "").strip()
    if not text:
        raise ValidationError(f"{name} is required.")
    if len(text) > max_len:
        raise ValidationError(f"{name} must be at most {max_len} characters.")
    return text


def _in_range(value: Any, name: str, lo: float, hi: float) -> Any:
    if value is None or not (lo <= value <= hi):
        raise ValidationError(f"{name} must be between {lo} and {hi}.")
    return value


class Employee:
    """An employee. Setters validate; ``to_onboard_params`` feeds sp_oltp_onboard_employee."""

    def __init__(self, first_name: str, last_name: str, email: str, date_of_birth: date, hire_date: date,
                 department_id: int, job_role_id: int, job_level: int, monthly_income: int,
                 education_field_id: int, *, gender: str = "Male", marital_status: str = "Single",
                 education_level: int = 3, business_travel: str = "Travel_Rarely", over_time: bool = False,
                 distance_from_home: int = 5, total_working_years: int = 0, num_companies_worked: int = 0,
                 employee_id: Optional[int] = None, employee_number: Optional[int] = None,
                 termination_date: Optional[date] = None) -> None:
        self.first_name, self.last_name, self.email = first_name, last_name, email
        self.gender, self.marital_status, self.business_travel = gender, marital_status, business_travel
        self.date_of_birth, self.hire_date, self.termination_date = date_of_birth, hire_date, termination_date
        self.department_id, self.job_role_id = department_id, job_role_id
        self.job_level, self.monthly_income, self.education_level = job_level, monthly_income, education_level
        self.education_field_id = education_field_id
        self.over_time = bool(over_time)
        self.distance_from_home, self.total_working_years = distance_from_home, total_working_years
        self.num_companies_worked = num_companies_worked
        self._employee_id, self._employee_number = employee_id, employee_number
        self.validate()

    # -- read-only identity
    @property
    def employee_id(self) -> Optional[int]:
        return self._employee_id

    @property
    def employee_number(self) -> Optional[int]:
        return self._employee_number

    # -- validated properties
    @property
    def email(self) -> str:
        return self._email

    @email.setter
    def email(self, v: str) -> None:
        v = _req_text(v, "Email", 160).lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValidationError("Email address is not valid.")
        self._email = v

    @property
    def first_name(self) -> str:
        return self._first

    @first_name.setter
    def first_name(self, v: str) -> None:
        self._first = _req_text(v, "First name")

    @property
    def last_name(self) -> str:
        return self._last

    @last_name.setter
    def last_name(self, v: str) -> None:
        self._last = _req_text(v, "Last name")

    @property
    def monthly_income(self) -> int:
        return self._income

    @monthly_income.setter
    def monthly_income(self, v: int) -> None:
        self._income = int(_in_range(v, "Monthly income", 1, 10_000_000))

    @property
    def job_level(self) -> int:
        return self._level

    @job_level.setter
    def job_level(self, v: int) -> None:
        self._level = int(_in_range(v, "Job level", 1, 5))

    def validate(self) -> None:
        if self.gender not in GENDERS:
            raise ValidationError(f"Gender must be one of {GENDERS}.")
        if self.marital_status not in MARITAL:
            raise ValidationError(f"Marital status must be one of {MARITAL}.")
        if self.business_travel not in TRAVEL:
            raise ValidationError(f"Business travel must be one of {TRAVEL}.")
        _in_range(self.education_level, "Education level", 1, 5)
        if self.date_of_birth.year > self.hire_date.year - 16:
            raise ValidationError("Employee must be at least 16 years old on the hire date.")
        if self.termination_date and self.termination_date < self.hire_date:
            raise ValidationError("Termination date cannot be before the hire date.")

    # -- behaviour
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def is_active(self) -> bool:
        return self.termination_date is None

    def tenure_years(self, as_of: Optional[date] = None) -> float:
        end = self.termination_date or as_of or date.today()
        return round((end - self.hire_date).days / 365.25, 1)

    def to_onboard_params(self) -> tuple:
        return (self.first_name, self.last_name, self.email, self.gender, self.date_of_birth, self.marital_status,
                self.education_level, self.education_field_id, self.department_id, self.job_role_id,
                self.job_level, self.monthly_income, self.hire_date, self.business_travel, int(self.over_time),
                self.distance_from_home, self.total_working_years, self.num_companies_worked)

    @classmethod
    def from_row(cls, r: dict) -> "Employee":
        return cls(r["first_name"], r["last_name"], r["email"], r["date_of_birth"], r["hire_date"],
                   r["department_id"], r["job_role_id"], r["job_level"], r["monthly_income"],
                   r["education_field_id"], gender=r["gender"], marital_status=r["marital_status"],
                   education_level=r["education_level"], business_travel=r["business_travel"],
                   over_time=bool(r["over_time"]), distance_from_home=r["distance_from_home"],
                   total_working_years=r["total_working_years"], num_companies_worked=r["num_companies_worked"],
                   employee_id=r["employee_id"], employee_number=r["employee_number"],
                   termination_date=r.get("termination_date"))


class Project:
    def __init__(self, project_code: str, project_name: str, department_id: int, start_date: date,
                 planned_end_date: date, *, status: str = "Planned", priority: str = "Medium",
                 budget: float = 0.0, actual_end_date: Optional[date] = None,
                 project_id: Optional[int] = None) -> None:
        self.project_code = _req_text(project_code, "Project code", 20).upper()
        self.project_name = _req_text(project_name, "Project name", 150)
        if status not in PROJECT_STATUSES:
            raise ValidationError(f"Status must be one of {PROJECT_STATUSES}.")
        if priority not in PRIORITIES:
            raise ValidationError(f"Priority must be one of {PRIORITIES}.")
        if planned_end_date < start_date:
            raise ValidationError("Planned end date cannot be before the start date.")
        if actual_end_date and actual_end_date < start_date:
            raise ValidationError("Actual end date cannot be before the start date.")
        if budget < 0:
            raise ValidationError("Budget cannot be negative.")
        self.department_id, self.start_date, self.planned_end_date = department_id, start_date, planned_end_date
        self.status, self.priority, self.budget = status, priority, float(budget)
        self.actual_end_date, self.project_id = actual_end_date, project_id

    @property
    def planned_duration_days(self) -> int:
        return (self.planned_end_date - self.start_date).days

    def is_overdue(self, as_of: Optional[date] = None) -> bool:
        return self.status in ("Active", "On Hold") and self.planned_end_date < (as_of or date.today())


class Review:
    def __init__(self, employee_id: int, review_date: date, performance_rating: int, overall_score: float,
                 goal_completion_pct: int, collaboration_score: int, *, project_id: Optional[int] = None,
                 comments: Optional[str] = None) -> None:
        self.employee_id, self.review_date, self.project_id = employee_id, review_date, project_id
        self.performance_rating = int(_in_range(performance_rating, "Performance rating", 1, 5))
        self.overall_score = float(_in_range(overall_score, "Overall score", 0, 100))
        self.goal_completion_pct = int(_in_range(goal_completion_pct, "Goal completion", 0, 100))
        self.collaboration_score = int(_in_range(collaboration_score, "Collaboration score", 1, 5))
        self.comments = (comments or "").strip()[:500] or None
        if review_date > date.today():
            raise ValidationError("Review date cannot be in the future.")

    @property
    def rating_label(self) -> str:
        return RATING_LABELS[self.performance_rating]
