from datetime import date, timedelta

import streamlit as st

from workforceiq.dal import EmployeeManager, ProjectManager
from workforceiq.entities import PRIORITIES, PROJECT_STATUSES, Project
from workforceiq.exceptions import WorkforceIQError

st.title("Projects")
pm, em = ProjectManager(), EmployeeManager()
try:
    depts = {r["department_name"]: int(r["department_id"]) for _, r in em.departments().iterrows()}
except WorkforceIQError as e:
    st.error(str(e)); st.stop()
t1, t2, t3 = st.tabs(["Browse", "Create project", "Assign employee"])

with t1:
    a, b = st.columns(2)
    sf = a.selectbox("Status", ["All"] + list(PROJECT_STATUSES))
    df_ = b.selectbox("Department", ["All"] + list(depts))
    projects = pm.list_projects(None if sf == "All" else sf, depts.get(df_))
    st.dataframe(projects, width="stretch", hide_index=True)
    if not projects.empty:
        pid = st.selectbox("Show team of", projects["project_id"],
                           format_func=lambda i: projects.set_index("project_id").loc[i, "project_code"])
        st.dataframe(pm.list_assignments(int(pid)), width="stretch", hide_index=True)

with t2:
    with st.form("new_project"):
        c1, c2 = st.columns(2)
        code, name = c1.text_input("Project code (e.g. PRJ-90001)"), c2.text_input("Project name")
        dn, pr = c1.selectbox("Owning department", list(depts)), c2.selectbox("Priority", PRIORITIES, 1)
        sd, ed = c1.date_input("Start date", date.today()), c2.date_input("Planned end", date.today() + timedelta(days=180))
        stt, bud = c1.selectbox("Status", PROJECT_STATUSES), c2.number_input("Budget", 0.0, 1e9, 100000.0, step=1000.0)
        if st.form_submit_button("Create project", type="primary"):
            try:
                pid, sync = pm.create(Project(code, name, depts[dn], sd, ed, status=stt, priority=pr, budget=bud))
                st.success(f"Project created (id {pid}). {sync.message}"); st.cache_data.clear()
            except WorkforceIQError as e:
                st.error(str(e))

with t3:
    with st.form("assign"):
        c1, c2 = st.columns(2)
        emp_no = c1.number_input("Employee ID (employee_id column)", 1, step=1)
        proj_code = c2.text_input("Project code")
        role, alloc = c1.text_input("Role on project", "Contributor"), c2.slider("Allocation %", 5, 100, 25, 5)
        sd = c1.date_input("Start date ", date.today())
        if st.form_submit_button("Assign", type="primary"):
            try:
                row = pm.fetch_one(f"SELECT project_id FROM {pm.oltp}.projects WHERE project_code=%s", (proj_code.strip().upper(),))
                if not row:
                    st.error("Unknown project code.")
                else:
                    total = pm.active_allocation(int(emp_no))
                    aid, sync = pm.assign(int(emp_no), row["project_id"], role, alloc, sd)
                    st.success(f"Assigned. {sync.message}")
                    if total + alloc > 100:
                        st.warning(f"Employee is now allocated {total + alloc}% (over 100%).")
                    st.cache_data.clear()
            except WorkforceIQError as e:
                st.error(str(e))
