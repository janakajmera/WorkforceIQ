from datetime import date

import plotly.graph_objects as go
import streamlit as st

from workforceiq.dal import AnalyticsManager, EmployeeManager
from workforceiq.entities import GENDERS, MARITAL, TRAVEL, Employee
from workforceiq.exceptions import WorkforceIQError
from workforceiq.ui.theme import INK, TEAL, style_fig

st.title("Employees")
em, am = EmployeeManager(), AnalyticsManager()


def report(sync):
    (st.success if sync.ok else st.warning)(sync.message)
    st.cache_data.clear()


def opts(df, id_col, name_col):
    return {r[name_col]: int(r[id_col]) for _, r in df.iterrows()}


try:
    depts, roles, fields = opts(em.departments(), "department_id", "department_name"), \
        opts(em.job_roles(), "job_role_id", "role_title"), opts(em.education_fields(), "education_field_id", "field_name")
except WorkforceIQError as e:
    st.error(str(e)); st.stop()

tab_find, tab_onboard = st.tabs(["Find & update", "Onboard employee"])

with tab_find:
    a, b, c = st.columns([3, 2, 1])
    term = a.text_input("Search name, email or employee number")
    dept_f = b.selectbox("Department", ["All"] + list(depts))
    active = c.checkbox("Active only", True)
    try:
        df = em.search(term, depts.get(dept_f), active, limit=50)
    except WorkforceIQError as e:
        st.error(str(e)); st.stop()
    st.dataframe(df, width="stretch", hide_index=True)
    if df.empty:
        st.info("No employees match. Try a different search.")
    else:
        pick = st.selectbox("Select employee", df["employee_id"],
                            format_func=lambda i: f"{df.set_index('employee_id').loc[i, 'name']} (#{df.set_index('employee_id').loc[i, 'employee_number']})")
        emp = em.get(int(pick))
        st.subheader(f"{emp.full_name} · {emp.tenure_years()} yrs tenure")

        tl = am.employee_timeline(int(pick))
        if not tl.empty:
            st.caption("Career timeline from the warehouse (SCD Type 2: one row per version)")
            fig = go.Figure(go.Scatter(x=tl["start_date"], y=tl["monthly_income"], mode="lines+markers+text",
                                       line=dict(shape="hv", color=TEAL, width=3), marker=dict(size=9, color=INK),
                                       text=tl["department_name"] + " · L" + tl["job_level"].astype(str),
                                       textposition="top center"))
            fig.update_layout(yaxis_title="Monthly income")
            st.plotly_chart(style_fig(fig, 320), width="stretch")
            st.dataframe(tl, width="stretch", hide_index=True)

        if emp.is_active:
            st.markdown("#### Change department / role / level / salary")
            with st.form("change_job"):
                cur_dept = em.fetch_one(f"SELECT department_id, job_role_id FROM {em.oltp}.employees WHERE employee_id=%s", (int(pick),))
                d_names, r_names = list(depts), list(roles)
                nd = st.selectbox("Department", d_names, index=[depts[x] for x in d_names].index(cur_dept["department_id"]))
                nr = st.selectbox("Job role", r_names, index=[roles[x] for x in r_names].index(cur_dept["job_role_id"]))
                l1, l2, l3 = st.columns(3)
                lvl = l1.number_input("Job level", 1, 5, emp.job_level)
                inc = l2.number_input("Monthly income", 1, 1_000_000, emp.monthly_income, step=100)
                eff = l3.date_input("Effective date", date.today())
                if st.form_submit_button("Save change", type="primary"):
                    try:
                        ctype, sync = em.change_job(int(pick), depts[nd], roles[nr], int(lvl), int(inc), eff)
                        st.success(f"{ctype} recorded."); report(sync)
                    except WorkforceIQError as e:
                        st.error(str(e))
            with st.expander("Record departure"):
                with st.form("terminate"):
                    td = st.date_input("Last working day", date.today())
                    if st.form_submit_button("Mark as left"):
                        try:
                            report(em.terminate(int(pick), td))
                        except WorkforceIQError as e:
                            st.error(str(e))

with tab_onboard:
    with st.form("onboard"):
        c1, c2, c3 = st.columns(3)
        fn, ln, mail = c1.text_input("First name"), c2.text_input("Last name"), c3.text_input("Email")
        g, ms, dob = c1.selectbox("Gender", GENDERS), c2.selectbox("Marital status", MARITAL), \
            c3.date_input("Date of birth", date(1995, 1, 1), min_value=date(1950, 1, 1))
        dn, rn, fld = c1.selectbox("Department ", list(depts)), c2.selectbox("Job role ", list(roles)), \
            c3.selectbox("Education field", list(fields))
        lvl, inc, hd = c1.number_input("Job level ", 1, 5, 1), c2.number_input("Monthly income ", 1, 1_000_000, 3000, step=100), \
            c3.date_input("Hire date", date.today())
        edu, trv, ot = c1.slider("Education level", 1, 5, 3), c2.selectbox("Business travel", TRAVEL, 1), \
            c3.checkbox("Works overtime")
        if st.form_submit_button("Onboard employee", type="primary"):
            try:
                e = Employee(fn, ln, mail, dob, hd, depts[dn], roles[rn], int(lvl), int(inc), fields[fld], gender=g,
                             marital_status=ms, education_level=edu, business_travel=trv, over_time=ot)
                ids, sync = em.onboard(e)
            except WorkforceIQError as ex:
                st.error(str(ex))
