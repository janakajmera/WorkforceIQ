"""Data Synthesizer: scales the 1,470-row IBM HR Analytics snapshot to 100,000+ employees and engineers
history (SCD Type 2 source), projects, assignments and yearly performance reviews.

Approach
--------
* **Scale** -- stratified bootstrap of the real rows with bounded jitter, then a consistency pass so
  tenure/age/promotion fields stay logically valid. The original 1,470 rows are kept untouched.
* **History** -- for a configurable subset of employees, career versions (promotion / transfer / salary
  increase) are simulated *backwards* from the snapshot, so the newest version always equals today's state.
* **Reviews** -- one annual review per employee per year, driven by a latent quality score correlated with
  the IBM performance rating, satisfaction, overtime and department, plus a mild upward trend.
* **Noise** -- optional realistic dirt (duplicates, casing, orphans, out-of-range values) that the ETL's
  CTEs / window functions must clean. The injected counts are returned so tests can reconcile exactly.
Everything is deterministic for a given seed and ``as_of`` date.
"""
from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from faker import Faker

from ..config import RAW_DATA_PATH

log = logging.getLogger(__name__)

SYNTHETIC_EMPLOYEE_NUMBER_START = 10_001
DEPARTMENTS = ["Research & Development", "Sales", "Human Resources"]
ROLES_BY_DEPT = {
    "Research & Development": ["Research Scientist", "Laboratory Technician", "Manufacturing Director",
                               "Healthcare Representative", "Research Director", "Manager"],
    "Sales": ["Sales Executive", "Sales Representative", "Manager"],
    "Human Resources": ["Human Resources", "Manager"],
}
PROJECT_CODENAMES = ["Atlas", "Orion", "Helios", "Nimbus", "Vertex", "Aurora", "Cobalt", "Meridian", "Zenith",
                     "Quasar", "Falcon", "Lumen", "Harbor", "Summit", "Pioneer", "Beacon", "Cascade", "Ember",
                     "Granite", "Horizon", "Ion", "Juniper", "Keystone", "Lattice", "Mosaic", "Nova", "Oasis",
                     "Prism", "Quartz", "Radiant", "Sierra", "Tundra", "Umbra", "Vector", "Willow", "Xenon"]
PROJECT_TOPICS = ["Payroll Migration", "Talent Pipeline", "Clinical Data Platform", "Lab Automation",
                  "Customer 360", "Sales Forecasting", "Onboarding Revamp", "Compliance Audit", "Pricing Engine",
                  "Market Expansion", "Supplier Portal", "Quality Analytics", "Learning Hub", "Benefits Redesign",
                  "Territory Planning", "Process Digitisation", "Safety Programme", "Retention Study",
                  "Partner Enablement", "Data Lake Rollout"]
PROJECT_ROLES = ["Project Lead", "Analyst", "Contributor", "Reviewer", "Subject Matter Expert", "Coordinator"]
IBM_CONSTANT_COLUMNS = ["employee_count", "over18", "standard_hours"]


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


@dataclass(frozen=True)
class SynthesisConfig:
    target_employees: int = 100_000
    seed: int = 42
    as_of: date = field(default_factory=date.today)
    history_rate: float = 0.35          # share of (sufficiently tenured) employees with career changes
    n_projects: int = 2_000
    review_years: int = 5
    inject_noise: bool = True
    base_csv: Path = RAW_DATA_PATH


@dataclass
class SynthesisResult:
    employees: pd.DataFrame
    history: pd.DataFrame
    projects: pd.DataFrame
    assignments: pd.DataFrame
    reviews: pd.DataFrame
    noise_report: dict = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {"employees": len(self.employees), "history": len(self.history), "projects": len(self.projects),
                "assignments": len(self.assignments), "reviews": len(self.reviews)}

    def total_rows(self) -> int:
        return sum(self.counts().values())

    def to_csv(self, directory: Path) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("employees", "history", "projects", "assignments", "reviews"):
            getattr(self, name).to_csv(directory / f"{name}.csv", index=False)


class DataSynthesizer:
    """Builds all staging DataFrames. Usage: ``DataSynthesizer(SynthesisConfig(...)).run()``."""

    def __init__(self, config: Optional[SynthesisConfig] = None) -> None:
        self.cfg = config or SynthesisConfig()
        self._rng = np.random.default_rng(self.cfg.seed)
        self._py = random.Random(self.cfg.seed)
        self._faker = {"en_IN": Faker("en_IN"), "en_US": Faker("en_US")}
        for i, f in enumerate(self._faker.values()):
            f.seed_instance(self.cfg.seed + i)
        self._batch_ts = datetime(self.cfg.as_of.year, self.cfg.as_of.month, self.cfg.as_of.day, 6, 0, 0)
        self.batch_id = f"synth-{self.cfg.seed}-{self.cfg.as_of:%Y%m%d}"

    # ------------------------------------------------------------------ public API
    def run(self) -> SynthesisResult:
        base = self.load_base()
        employees = self.scale_employees(base)
        history, employees = self.generate_history(employees)
        projects = self.generate_projects()
        assignments = self.generate_assignments(employees, projects)
        reviews = self.generate_reviews(employees, assignments)
        result = SynthesisResult(employees, history, projects, assignments, reviews)
        if self.cfg.inject_noise:
            self._inject_noise(result)
        self._finalise(result)
        log.info("Synthesized %s", result.counts())
        return result

    # ------------------------------------------------------------------ 1. base data
    def load_base(self) -> pd.DataFrame:
        df = pd.read_csv(self.cfg.base_csv)
        df.columns = [_snake(c) for c in df.columns]
        df = df.drop(columns=IBM_CONSTANT_COLUMNS).rename(columns={"employee_number": "source_employee_number"})
        if len(df) < 100:
            raise ValueError(f"Base dataset looks wrong ({len(df)} rows): {self.cfg.base_csv}")
        return df.reset_index(drop=True)

    # ------------------------------------------------------------------ 2. scaling
    def scale_employees(self, base: pd.DataFrame) -> pd.DataFrame:
        n_extra = max(0, self.cfg.target_employees - len(base))
        orig = base.copy()
        orig["record_source"] = "IBM_ORIGINAL"
        if n_extra:
            idx = self._rng.integers(0, len(base), n_extra)
            syn = base.iloc[idx].reset_index(drop=True).copy()
            syn = self._perturb(syn)
            syn["source_employee_number"] = np.arange(SYNTHETIC_EMPLOYEE_NUMBER_START,
                                                      SYNTHETIC_EMPLOYEE_NUMBER_START + n_extra)
            syn["record_source"] = "SYNTHETIC"
            emp = pd.concat([orig, syn], ignore_index=True)
        else:
            emp = orig
        emp = self._enforce_consistency(emp)
        emp = self._add_dates(emp)
        emp = self._add_identity(emp)
        emp["snapshot_date"] = pd.Timestamp(self.cfg.as_of)
        return emp

    def _perturb(self, df: pd.DataFrame) -> pd.DataFrame:
        r, n = self._rng, len(df)

        def jitter(col: str, lo: int, hi: int, floor: int, ceil: Optional[int] = None) -> None:
            vals = df[col].to_numpy() + r.integers(lo, hi + 1, n)
            df[col] = np.clip(vals, floor, ceil if ceil is not None else None)

        jitter("age", -3, 3, 18, 60)
        df["monthly_income"] = np.clip((df["monthly_income"] * r.lognormal(0, 0.07, n)).round(), 1009, None).astype(int)
        df["daily_rate"] = r.integers(102, 1500, n)
        df["hourly_rate"] = r.integers(30, 101, n)
        df["monthly_rate"] = r.integers(2094, 27000, n)
        jitter("distance_from_home", -2, 2, 1, 29)
        jitter("total_working_years", -1, 1, 0)
        for col in ("years_at_company", "years_in_current_role", "years_since_last_promotion", "years_with_curr_manager"):
            jitter(col, -1, 1, 0)
        jitter("percent_salary_hike", -1, 1, 11, 25)
        jitter("training_times_last_year", -1, 1, 0, 6)
        jitter("num_companies_worked", 0, 1, 0, 9)
        for col in ("environment_satisfaction", "job_satisfaction", "relationship_satisfaction",
                    "work_life_balance", "job_involvement"):
            move = (r.random(n) < 0.15) * r.integers(-1, 2, n)
            df[col] = np.clip(df[col].to_numpy() + move, 1, 4)
        return df

    @staticmethod
    def _enforce_consistency(df: pd.DataFrame) -> pd.DataFrame:
        """Make tenure / age / promotion fields mutually consistent (also valid for the raw rows)."""
        df["age"] = df["age"].clip(18, 65)
        df["total_working_years"] = np.minimum(df["total_working_years"], df["age"] - 18).clip(lower=0)
        df["years_at_company"] = np.minimum(df["years_at_company"], df["total_working_years"])
        for col in ("years_in_current_role", "years_since_last_promotion", "years_with_curr_manager"):
            df[col] = np.minimum(df[col], df["years_at_company"])
        df["monthly_income"] = df["monthly_income"].clip(lower=1009)
        return df

    def _add_dates(self, df: pd.DataFrame) -> pd.DataFrame:
        """hire_date / termination_date consistent with YearsAtCompany and Attrition."""
        r, n, as_of = self._rng, len(df), np.datetime64(self.cfg.as_of)
        leaver = (df["attrition"] == "Yes").to_numpy()
        term_offset = r.integers(15, 1826, n)                                  # leavers left in the last 5 years
        term = as_of - term_offset.astype("timedelta64[D]")
        anchor = np.where(leaver, term, np.broadcast_to(as_of, n))
        tenure_days = np.maximum((df["years_at_company"].to_numpy() * 365.25).astype(int) + r.integers(0, 365, n), 45)
        hire = anchor - tenure_days.astype("timedelta64[D]")
        df["hire_date"] = pd.to_datetime(hire)
        df["termination_date"] = pd.to_datetime(np.where(leaver, term, np.datetime64("NaT")))
        return df

    def _add_identity(self, df: pd.DataFrame) -> pd.DataFrame:
        n = len(df)
        locales = self._rng.choice(["en_IN", "en_US"], n)
        firsts, lasts, emails = [], [], []
        for loc, num in zip(locales, df["source_employee_number"].to_numpy()):
            f = self._faker[loc]
            fn, ln = f.first_name(), f.last_name()
            firsts.append(fn)
            lasts.append(ln)
            emails.append(f"{re.sub('[^a-z]', '', fn.lower())}.{re.sub('[^a-z]', '', ln.lower())}.{num}@workforceiq.example")
        df["first_name"], df["last_name"], df["email"] = firsts, lasts, emails
        return df

    # ------------------------------------------------------------------ 3. SCD2 history
    def generate_history(self, emp: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        rows: list[tuple] = []
        promo_update: dict[int, int] = {}
        end_default = self.cfg.as_of
        records = emp[["source_employee_number", "hire_date", "termination_date", "department", "job_role",
                       "job_level", "monthly_income", "years_at_company"]].itertuples(index=False)
        for num, hire, term, dept, role, level, income, yac in records:
            hire_d = hire.date()
            end_d = term.date() if pd.notna(term) else end_default
            span = (end_d - hire_d).days
            if span < 240 or self._py.random() > self.cfg.history_rate:
                continue
            versions = self._build_versions(hire_d, end_d, span, dept, role, int(level), int(income))
            for eff, d, ro, lv, inc, ctype in versions:
                rows.append((int(num), eff, d, ro, lv, inc, ctype))
            promos = [v[0] for v in versions if v[5] == "Promotion"]
            if promos:
                promo_update[int(num)] = min(int(yac), (end_d - max(promos)).days // 365)
        hist = pd.DataFrame(rows, columns=["source_employee_number", "effective_date", "department", "job_role",
                                           "job_level", "monthly_income", "change_type"])
        if promo_update:  # keep YearsSinceLastPromotion consistent with the engineered promotions
            upd = emp["source_employee_number"].map(promo_update)
            mask = upd.notna()
            emp.loc[mask, "years_since_last_promotion"] = upd[mask].astype(int)
            emp["years_in_current_role"] = np.minimum(emp["years_in_current_role"], emp["years_at_company"])
        return hist, emp

    def _build_versions(self, hire_d: date, end_d: date, span: int, dept: str, role: str, level: int, income: int):
        py = self._py
        k = py.choices([1, 2, 3], weights=[55, 30, 15])[0]
        k = min(k, (span - 90) // 150)
        offsets: list[int] = []
        for _ in range(20):                                                     # distinct, >=60 days apart
            cand = sorted(py.sample(range(90, span - 30), k))
            if all(b - a >= 60 for a, b in zip(cand, cand[1:])):
                offsets = cand
                break
        if not offsets:
            offsets = [py.randint(90, span - 30)]
        kinds = py.choices(["Promotion", "Salary Increase", "Transfer"], weights=[40, 40, 20], k=len(offsets))
        state = (dept, role, level, income)
        rev: list[tuple] = []
        for off, kind in zip(reversed(offsets), reversed(kinds)):               # walk backwards from today's state
            d, ro, lv, inc = state
            eff = hire_d + timedelta(days=off)
            if kind == "Promotion" and lv < 2:
                kind = "Salary Increase"
            rev.append((eff, d, ro, lv, inc, kind))                              # the version created by this event
            if kind == "Promotion":
                state = (d, ro, lv - 1, max(1009, round(inc / py.uniform(1.10, 1.28))))
            elif kind == "Salary Increase":
                state = (d, ro, lv, max(1009, round(inc / py.uniform(1.04, 1.12))))
            else:
                prev_dept = py.choice([x for x in DEPARTMENTS if x != d])
                prev_level = max(1, min(5, lv + py.choice([-1, 0, 0, 1])))
                state = (prev_dept, py.choice(ROLES_BY_DEPT[prev_dept]), prev_level,
                         max(1009, round(inc / py.uniform(0.95, 1.08))))
        d, ro, lv, inc = state
        versions = [(hire_d, d, ro, lv, inc, "Hire")] + list(reversed(rev))
        return versions

    # ------------------------------------------------------------------ 4. projects
    def generate_projects(self) -> pd.DataFrame:
        r, n, as_of = self._rng, self.cfg.n_projects, self.cfg.as_of
        status = r.choice(["Completed", "Active", "On Hold", "Planned", "Cancelled"], n, p=[.38, .36, .08, .08, .10])
        dept = r.choice(DEPARTMENTS, n, p=[.50, .35, .15])
        priority = r.choice(["Low", "Medium", "High", "Critical"], n, p=[.20, .45, .25, .10])
        start_back = np.where(status == "Completed", r.integers(200, 1801, n),
                              np.where(status == "Cancelled", r.integers(100, 1501, n), r.integers(30, 1001, n)))
        start = np.array([as_of - timedelta(days=int(d)) for d in start_back], dtype="datetime64[D]")
        future = status == "Planned"
        start[future] = np.array([as_of + timedelta(days=int(d)) for d in r.integers(10, 181, future.sum())],
                                 dtype="datetime64[D]")
        planned_end = start + r.integers(120, 721, n).astype("timedelta64[D]")
        actual = np.full(n, np.datetime64("NaT"), dtype="datetime64[D]")
        as_of64 = np.datetime64(as_of)
        done = status == "Completed"
        a = planned_end + r.integers(-45, 151, n).astype("timedelta64[D]")
        a = np.minimum(a, as_of64 - np.timedelta64(7, "D"))
        a = np.maximum(a, start + np.timedelta64(30, "D"))
        actual[done] = a[done]
        canc = status == "Cancelled"
        span = np.maximum((as_of64 - start).astype(int), 31)
        c = start + (r.random(n) * (span - 30) + 30).astype(int).astype("timedelta64[D]")
        actual[canc] = c[canc]
        names = [f"{self._py.choice(PROJECT_CODENAMES)} - {self._py.choice(PROJECT_TOPICS)}" for _ in range(n)]
        return pd.DataFrame({
            "project_code": [f"PRJ-{i + 1:05d}" for i in range(n)], "project_name": names, "department": dept,
            "status": status, "priority": priority, "start_date": pd.to_datetime(start),
            "planned_end_date": pd.to_datetime(planned_end), "actual_end_date": pd.to_datetime(actual),
            "budget": np.round(r.lognormal(13.0, 0.8, n), 2),
        })

    # ------------------------------------------------------------------ 5. assignments
    def generate_assignments(self, emp: pd.DataFrame, proj: pd.DataFrame) -> pd.DataFrame:
        py, as_of_o = self._py, self.cfg.as_of.toordinal()
        p_start = proj["start_date"].dt.date.map(date.toordinal).to_numpy()
        p_end = proj["actual_end_date"].map(lambda d: d.toordinal() if pd.notna(d) else as_of_o).to_numpy()
        codes = proj["project_code"].to_numpy()
        status = proj["status"].to_numpy()
        pdept = proj["department"].to_numpy()
        active_pool = np.where(np.isin(status, ["Active", "On Hold"]) & (p_start <= as_of_o))[0]
        past_pool = np.where(np.isin(status, ["Completed", "Cancelled"]))[0]
        by_dept = {d: (active_pool[pdept[active_pool] == d], past_pool[pdept[past_pool] == d]) for d in DEPARTMENTS}

        def pick(pool_all: np.ndarray, pool_dept: np.ndarray, k: int, taken: set) -> list[int]:
            out: list[int] = []
            for _ in range(k):
                for _try in range(6):
                    pool = pool_dept if (len(pool_dept) and py.random() < 0.65) else pool_all
                    j = int(pool[py.randrange(len(pool))])
                    if j not in taken:
                        taken.add(j)
                        out.append(j)
                        break
            return out

        rows: list[tuple] = []
        cols = emp[["source_employee_number", "hire_date", "termination_date", "department"]].itertuples(index=False)
        for num, hire, term, dept in cols:
            h = hire.toordinal()
            t = term.toordinal() if pd.notna(term) else None
            emp_end = t if t is not None else as_of_o
            taken: set[int] = set()
            n_act = py.choices([0, 1, 2, 3], weights=[40, 32, 18, 10])[0]
            n_past = py.choices([0, 1, 2, 3], weights=[50, 28, 14, 8])[0]
            for j in pick(active_pool, by_dept[dept][0], n_act, taken):
                start = max(h, int(p_start[j])) + py.randint(0, 45)
                if start >= emp_end - 5:
                    continue
                end = t                                                          # open-ended unless the employee left
                alloc = py.choices([20, 25, 30, 40, 50, 60], weights=[15, 15, 25, 20, 15, 10])[0]
                rows.append((int(num), codes[j], py.choices(PROJECT_ROLES)[0], alloc, start, end))
            for j in pick(past_pool, by_dept[dept][1], n_past, taken):
                w_start, w_end = max(h, int(p_start[j])), min(int(p_end[j]), emp_end)
                if w_end - w_start < 21:
                    continue
                start = w_start + py.randint(0, (w_end - w_start) // 3)
                end = w_end - py.randint(0, (w_end - w_start) // 5)
                alloc = py.choices([20, 25, 30, 40, 50, 60, 80, 100], weights=[10, 10, 20, 20, 15, 10, 10, 5])[0]
                rows.append((int(num), codes[j], py.choices(PROJECT_ROLES)[0], alloc, start, max(start, end)))
        df = pd.DataFrame(rows, columns=["source_employee_number", "project_code", "role_on_project",
                                         "allocation_pct", "start_date", "end_date"])
        for c in ("start_date", "end_date"):
            df[c] = pd.to_datetime(df[c].astype("Int64") - 719163, unit="D")
        return df

    # ------------------------------------------------------------------ 6. reviews
    def generate_reviews(self, emp: pd.DataFrame, asg: pd.DataFrame) -> pd.DataFrame:
        r, as_of = self._rng, self.cfg.as_of
        last_year = as_of.year if as_of >= date(as_of.year, 12, 15) else as_of.year - 1
        years = list(range(last_year - self.cfg.review_years + 1, last_year + 1))
        n = len(emp)
        quality = (r.normal(0, 0.55, n) + 0.40 * (emp["performance_rating"].to_numpy() == 4)
                   + 0.10 * (emp["job_satisfaction"].to_numpy() - 2.5)
                   - 0.12 * (emp["over_time"].to_numpy() == "Yes")
                   - 0.15 * (emp["attrition"].to_numpy() == "Yes")
                   + np.select([emp["department"].to_numpy() == "Research & Development",
                                emp["department"].to_numpy() == "Sales"], [0.05, -0.05], 0.0))
        base = pd.DataFrame({"source_employee_number": emp["source_employee_number"].to_numpy(),
                             "hire_date": emp["hire_date"].to_numpy(), "termination_date": emp["termination_date"].to_numpy(),
                             "quality": quality})
        frames = [base.assign(review_year=y) for y in years]
        rev = pd.concat(frames, ignore_index=True)
        offs = r.integers(0, 76, len(rev)).astype("timedelta64[D]")
        rev["review_date"] = pd.to_datetime(
            (rev["review_year"].astype(str) + "-10-01").to_numpy().astype("datetime64[D]") + offs)
        eligible = (rev["hire_date"] <= rev["review_date"] - pd.Timedelta(days=60)) & (
            rev["termination_date"].isna() | (rev["termination_date"] >= rev["review_date"]))
        rev = rev[eligible].reset_index(drop=True)
        m = len(rev)
        rating_f = 3.1 + rev["quality"].to_numpy() + 0.06 * (rev["review_year"].to_numpy() - years[0]) + r.normal(0, 0.45, m)
        rev["performance_rating"] = np.clip(np.rint(rating_f), 1, 5).astype(int)
        score = np.clip(100 / (1 + np.exp(-0.9 * (rating_f - 3.2))) + r.normal(0, 3, m), 0.5, 99.5)  # smooth: no pile-up at 100
        rev["overall_score"] = np.round(score, 2)
        rev["goal_completion_pct"] = np.clip(np.rint(score + r.normal(0, 9, m)), 0, 100).astype(int)
        rev["collaboration_score"] = np.clip(np.rint(rating_f + r.normal(0, 0.8, m)), 1, 5).astype(int)
        rev["project_code"] = self._primary_project(rev, asg)
        return rev[["source_employee_number", "project_code", "review_date", "performance_rating", "overall_score",
                    "goal_completion_pct", "collaboration_score"]]

    @staticmethod
    def _primary_project(rev: pd.DataFrame, asg: pd.DataFrame) -> pd.Series:
        """Pick one assignment overlapping the review date (if any) as the review's project."""
        a = asg.assign(end_filled=asg["end_date"].fillna(pd.Timestamp.max.floor("D")))
        key = rev[["source_employee_number", "review_date"]].reset_index().rename(columns={"index": "rid"})
        merged = key.merge(a[["source_employee_number", "project_code", "start_date", "end_filled"]],
                           on="source_employee_number", how="inner")
        merged = merged[(merged["start_date"] <= merged["review_date"]) & (merged["end_filled"] >= merged["review_date"])]
        first = merged.sort_values(["rid", "start_date"]).drop_duplicates("rid").set_index("rid")["project_code"]
        return rev.index.to_series().map(first)

    # ------------------------------------------------------------------ 7. controlled dirt
    def _inject_noise(self, res: SynthesisResult) -> None:
        r, py = self._rng, self._py
        emp = res.employees
        report: dict[str, int] = {}
        n = len(emp)
        # (a) stale duplicate snapshot rows: older ingest, outdated salary -> dedup must keep the newest
        n_dup = max(1, int(n * 0.004))
        dup = emp.iloc[r.choice(n, n_dup, replace=False)].copy()
        dup["monthly_income"] = (dup["monthly_income"] * 0.9).round().astype(int)
        dup["_ingested_at"] = self._batch_ts - timedelta(days=1)
        report["duplicate_employee_rows"] = n_dup
        # (b) casing / whitespace / alias noise that cleansing must normalise (rows stay valid)
        def pick(frac: float) -> np.ndarray:
            return r.random(n) < frac
        m = pick(0.01); emp.loc[m, "department"] = emp.loc[m, "department"].str.upper() + " "
        m = pick(0.01); emp.loc[m, "job_role"] = emp.loc[m, "job_role"].str.lower()
        m = pick(0.01); emp.loc[m, "email"] = "  " + emp.loc[m, "email"].str.upper()
        m = pick(0.005); emp.loc[m, "gender"] = emp.loc[m, "gender"].map({"Male": "M", "Female": "F"}).fillna("Other")
        report["dirty_text_rows"] = int(n * 0.03)  # approximate; informational only
        emp["_ingested_at"] = self._batch_ts
        res.employees = pd.concat([emp, dup], ignore_index=True)
        # (c) reviews: older stale duplicates, orphans and out-of-range ratings
        rev = res.reviews.copy()
        rev["_ingested_at"] = self._batch_ts
        n_rev = len(rev)
        n_rdup = max(1, int(n_rev * 0.002))
        rdup = rev.iloc[r.choice(n_rev, n_rdup, replace=False)].copy()
        rdup["performance_rating"] = np.clip(rdup["performance_rating"] - 1, 1, 5)
        rdup["_ingested_at"] = self._batch_ts - timedelta(days=1)
        first_year = int(rev["review_date"].dt.year.min())
        valid_nums = emp["source_employee_number"].to_numpy()
        n_bad = 40
        bad = rev.iloc[:n_bad].copy()
        bad["source_employee_number"] = r.choice(valid_nums, n_bad)
        bad["review_date"] = pd.Timestamp(date(first_year - 3, 11, 1))           # a year with no real review
        bad["performance_rating"] = r.choice([0, 6, 7], n_bad)
        n_orph = 30
        orph = rev.iloc[n_bad:n_bad + n_orph].copy()
        orph["source_employee_number"] = 9_000_000 + np.arange(n_orph)
        res.reviews = pd.concat([rev, rdup, bad, orph], ignore_index=True)
        report.update(duplicate_review_rows=n_rdup, invalid_rating_reviews=n_bad, orphan_reviews=n_orph)
        # (d) orphan assignments
        asg = res.assignments.copy()
        asg["_ingested_at"] = self._batch_ts
        n_oa = 30
        oa = asg.iloc[:n_oa].copy()
        oa["source_employee_number"] = 9_100_000 + np.arange(n_oa)
        res.assignments = pd.concat([asg, oa], ignore_index=True)
        report["orphan_assignments"] = n_oa
        res.noise_report = report

    # ------------------------------------------------------------------ finishing touches
    def _finalise(self, res: SynthesisResult) -> None:
        ts = self._batch_ts
        for name in ("employees", "history", "projects", "assignments", "reviews"):
            df = getattr(res, name)
            if "_ingested_at" in df.columns:
                df["ingested_at"] = df.pop("_ingested_at")
            else:
                df["ingested_at"] = ts
            df["batch_id"] = self.batch_id
        res.history["effective_date"] = pd.to_datetime(res.history["effective_date"])
