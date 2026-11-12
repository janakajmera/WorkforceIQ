from datetime import date

import pytest

from workforceiq.entities import Employee, Project, Review
from workforceiq.exceptions import ValidationError
from workforceiq.sql_runner import split_statements
from workforceiq.synthesis import DataSynthesizer, SynthesisConfig


def test_splitter_handles_delimiter_and_strings():
    sql = "SELECT ';';\nDELIMITER //\nCREATE PROCEDURE p() BEGIN SELECT 1; END //\nDELIMITER ;\nSELECT 2;"
    assert len(split_statements(sql)) == 3


def test_employee_validation():
    with pytest.raises(ValidationError):
        Employee("A", "B", "bad-email", date(1990, 1, 1), date(2020, 1, 1), 1, 1, 1, 3000, 1)
    e = Employee("A", "B", "A@B.com", date(1990, 1, 1), date(2020, 1, 1), 1, 1, 1, 3000, 1)
    assert e.email == "a@b.com" and e.full_name == "A B"


def test_project_and_review_rules():
    with pytest.raises(ValidationError):
        Project("P1", "x", 1, date(2024, 5, 1), date(2024, 4, 1))
    with pytest.raises(ValidationError):
        Review(1, date(2020, 1, 1), 7, 50, 50, 3)


def test_synthesizer_scale_and_consistency():
    res = DataSynthesizer(SynthesisConfig(target_employees=3000, n_projects=100, as_of=date(2026, 11, 7))).run()
    e = res.employees
    assert e["source_employee_number"].nunique() == 3000
    assert (e["years_at_company"] <= e["total_working_years"]).all()
    assert (res.history.groupby("source_employee_number")["effective_date"].nunique()
            == res.history.groupby("source_employee_number").size()).all()
