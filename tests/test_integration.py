from datetime import date

import pytest

from workforceiq.dal import AnalyticsManager, EmployeeManager, EtlManager, ProjectManager, ReviewManager
from workforceiq.entities import Employee, Project, Review
from workforceiq.exceptions import DuplicateError, ValidationError

pytestmark = pytest.mark.integration


def count(wh, sql):
    return list(wh["db"].fetch_one(sql).values())[0]


def test_row_counts_reconcile_with_injected_noise(warehouse):
    res, em = warehouse["result"], EmployeeManager()
    assert count(warehouse, f"SELECT COUNT(*) FROM {em.oltp}.employees") == 1500            # duplicates collapsed
    n = res.noise_report
    expected_reviews = len(res.reviews) - n["duplicate_review_rows"] - n["invalid_rating_reviews"] - n["orphan_reviews"]
    assert count(warehouse, f"SELECT COUNT(*) FROM {em.oltp}.reviews") == expected_reviews
    assert count(warehouse, f"SELECT COUNT(*) FROM {em.oltp}.assignments") == len(res.assignments) - n["orphan_assignments"]


def test_dedup_keeps_newest_snapshot(warehouse):
    """Stale duplicate rows carry a 10% lower salary; the loaded salary must be the newest (original) one."""
    res, em = warehouse["result"], EmployeeManager()
    e = res.employees
    dup_nums = e[e.duplicated("source_employee_number", keep=False)]["source_employee_number"].unique()
    newest = e[e["ingested_at"] == e["ingested_at"].max()].set_index("source_employee_number")["monthly_income"]
    for num in dup_nums[:20]:
        got = em.fetch_one(f"SELECT monthly_income m FROM {em.oltp}.employees WHERE employee_number=%s", (int(num),))["m"]
        assert got == newest[num]


def test_dirty_text_is_normalised(warehouse):
    em = EmployeeManager()
    assert count(warehouse, f"SELECT COUNT(*) FROM {em.oltp}.employees WHERE email <> LOWER(TRIM(email))") == 0
    assert count(warehouse, f"SELECT COUNT(*) FROM {em.oltp}.departments") == 3


def test_scd2_invariants(warehouse):
    checks = AnalyticsManager().integrity_checks()
    assert (checks["status"] == "PASS").all(), checks
    dw = AnalyticsManager().dw
    assert count(warehouse, f"SELECT COUNT(*) FROM {dw}.dim_employee WHERE employee_id>0 AND end_date<start_date") == 0
    assert count(warehouse, f"SELECT COUNT(*) FROM {dw}.dim_employee WHERE is_current=1 AND end_date<>'9999-12-31'") == 0
    assert count(warehouse, f"SELECT COUNT(*) FROM {dw}.dim_employee WHERE employee_id>0 AND version_no>1") > 0


def test_surrogate_keys_are_unique_and_distinct_from_natural_keys(warehouse):
    dw = AnalyticsManager().dw
    total = count(warehouse, f"SELECT COUNT(*) FROM {dw}.dim_employee")
    assert total == count(warehouse, f"SELECT COUNT(DISTINCT employee_key) FROM {dw}.dim_employee")
    assert total > count(warehouse, f"SELECT COUNT(DISTINCT employee_id) FROM {dw}.dim_employee")  # versions > employees


def test_fact_uses_point_in_time_version(warehouse):
    dw = AnalyticsManager().dw
    bad = count(warehouse, f"""SELECT COUNT(*) FROM {dw}.fact_performance_reviews f
        JOIN {dw}.dim_employee e ON e.employee_key=f.employee_key JOIN {dw}.dim_date d ON d.date_key=f.date_key
        WHERE d.full_date NOT BETWEEN e.start_date AND e.end_date""")
    assert bad == 0


def test_etl_is_idempotent(warehouse):
    etl = EtlManager()
    before = etl.layer_counts()["row_count"].tolist()
    etl.run_staging_to_oltp()
    etl.run_full_dw_etl()
    assert etl.layer_counts()["row_count"].tolist() == before


def test_department_change_creates_scd2_version(warehouse):
    em, am = EmployeeManager(), AnalyticsManager()
    row = em.fetch_one(f"SELECT employee_id, department_id FROM {em.oltp}.employees WHERE termination_date IS NULL LIMIT 1")
    eid, old = row["employee_id"], row["department_id"]
    versions_before = len(am.employee_timeline(eid))
    new = 1 if old != 1 else 2
    ctype, sync = em.change_job(eid, new, 1, 2, 7777, date.today())
    assert ctype == "Transfer" and sync.ok
    tl = am.employee_timeline(eid)
    assert len(tl) == versions_before + 1
    assert tl.iloc[-1]["is_current"] == 1 and tl.iloc[-2]["is_current"] == 0
    assert tl.iloc[-2]["end_date"] < tl.iloc[-1]["start_date"]
    with pytest.raises(ValidationError):
        em.change_job(eid, new, 1, 2, 7777, date.today())          # nothing changed
    assert (am.integrity_checks()["status"] == "PASS").all()


def test_onboard_review_and_project_flow(warehouse):
    em, pm, rm, am = EmployeeManager(), ProjectManager(), ReviewManager(), AnalyticsManager()
    e = Employee("Test", "Person", "test.person@example.com", date(1995, 5, 5), date(2024, 1, 15), 1, 1, 1, 3500, 1)
    ids, sync = em.onboard(e)
    assert sync.ok and ids["employee_number"] > 0
    with pytest.raises(ValidationError):
        em.onboard(e)                                                # duplicate email
    pid, _ = pm.create(Project("TST-1", "Test project", 1, date(2024, 1, 1), date(2024, 12, 31), status="Active"))
    pm.assign(ids["employee_id"], pid, "Lead", 60, date(2024, 2, 1))
    assert pm.active_allocation(ids["employee_id"]) == 60
    rid, sync = rm.submit(Review(ids["employee_id"], date(2025, 11, 1), 4, 80, 90, 4, project_id=pid))
    assert sync.ok
    with pytest.raises(DuplicateError):
        rm.submit(Review(ids["employee_id"], date(2025, 12, 1), 3, 60, 60, 3))   # one review per year
    assert len(am.employee_timeline(ids["employee_id"])) == 1


def test_analytics_views_return_expected_shapes(warehouse):
    am = AnalyticsManager()
    year = am.review_years()[0]
    top = am.top_performers(year, "Sales", 5)
    assert not top.empty and top["rank"].max() <= 5 and (top["department_name"] == "Sales").all()
    yoy = am.yoy_performance()
    assert yoy["yoy_change"].notna().any()
    bands = am.attrition_by_band().set_index("risk_band")["actual_attrition_pct"]
    assert bands["High"] > bands["Low"]                               # risk score separates leavers from stayers
    assert set(am.kpis()) >= {"headcount", "attrition_pct"}
