from datetime import date

import streamlit as st

from workforceiq.dal import EmployeeManager, ReviewManager
from workforceiq.entities import RATING_LABELS, Review
from workforceiq.exceptions import WorkforceIQError

st.title("Performance reviews")
rm, em = ReviewManager(), EmployeeManager()
term = st.text_input("Find employee (name, email or number)")
try:
    found = em.search(term, active_only=True, limit=25)
except WorkforceIQError as e:
    st.error(str(e)); st.stop()
if found.empty:
    st.info("No active employees match.")
else:
    idx = found.set_index("employee_id")
    pick = st.selectbox("Employee", found["employee_id"], format_func=lambda i: f"{idx.loc[i, 'name']} (#{idx.loc[i, 'employee_number']})")
    st.dataframe(rm.for_employee(int(pick)), width="stretch", hide_index=True)
    with st.form("review"):
        c1, c2 = st.columns(2)
        rd = c1.date_input("Review date", date.today())
        rating = c2.select_slider("Rating", [1, 2, 3, 4, 5], 3, format_func=lambda v: f"{v} – {RATING_LABELS[v]}")
        score, goals = c1.slider("Overall score", 0.0, 100.0, 60.0), c2.slider("Goal completion %", 0, 100, 70)
        collab = c1.slider("Collaboration", 1, 5, 3)
        code = c2.text_input("Project code (optional)")
        notes = st.text_area("Comments", max_chars=500)
        if st.form_submit_button("Submit review", type="primary"):
            try:
                proj = None
                if code.strip():
                    row = rm.fetch_one(f"SELECT project_id FROM {rm.oltp}.projects WHERE project_code=%s", (code.strip().upper(),))
                    if not row:
                        raise WorkforceIQError("Unknown project code.")
                    proj = row["project_id"]
                rid, sync = rm.submit(Review(int(pick), rd, rating, score, goals, collab, project_id=proj, comments=notes))
                st.success(f"Review {rid} saved. {sync.message}"); st.cache_data.clear()
            except WorkforceIQError as e:
                st.error(str(e))
st.subheader("Latest submissions")
st.dataframe(rm.recent(15), width="stretch", hide_index=True)
