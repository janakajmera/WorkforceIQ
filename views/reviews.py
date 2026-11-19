from datetime import date

import streamlit as st

from workforceiq.dal import EmployeeManager, ReviewManager
from workforceiq.entities import RATING_LABELS, Review
from workforceiq.exceptions import WorkforceIQError

st.title("Performance reviews")
rm, em = ReviewManager(), EmployeeManager()

search_tab, add_tab = st.tabs(["Search reviews", "Add review"])

with search_tab:
    c1, c2, c3 = st.columns([3, 1, 1])
    query = c1.text_input(
        "Search reviews (employee name, email, number, project code, or comments)",
        key="review_search_query",
    )
    rating_filter = c2.selectbox(
        "Rating",
        [None, 1, 2, 3, 4, 5],
        format_func=lambda v: "All ratings" if v is None else f"{v} – {RATING_LABELS[v]}",
        key="review_search_rating",
    )
    year_input = c3.text_input("Year", placeholder="e.g. 2026", key="review_search_year")

    year_filter: int | None = None
    if year_input.strip():
        if year_input.strip().isdigit() and len(year_input.strip()) == 4:
            year_filter = int(year_input.strip())
        else:
            st.warning("Enter a valid 4-digit year.")

    try:
        results = rm.search(term=query, rating=rating_filter, year=year_filter, limit=50)
    except WorkforceIQError as e:
        st.error(str(e))
    else:
        if results.empty:
            st.info("No reviews match your search criteria.")
        else:
            st.caption(f"Showing {len(results)} review(s)")
            st.dataframe(results, width="stretch", hide_index=True)

with add_tab:
    term = st.text_input("Find employee (name, email or number)", key="review_add_emp_term")
    try:
        found = em.search(term, active_only=True, limit=25)
    except WorkforceIQError as e:
        st.error(str(e))
        st.stop()

    if found.empty:
        st.info("No active employees match.")
    else:
        idx = found.set_index("employee_id")
        pick = st.selectbox(
            "Employee",
            found["employee_id"],
            format_func=lambda i: f"{idx.loc[i, 'name']} (#{idx.loc[i, 'employee_number']})",
            key="review_add_emp_pick",
        )
        st.dataframe(rm.for_employee(int(pick)), width="stretch", hide_index=True)

        with st.form("review"):
            c1, c2 = st.columns(2)
            rd = c1.date_input("Review date", date.today())
            rating = c2.select_slider(
                "Rating",
                [1, 2, 3, 4, 5],
                3,
                format_func=lambda v: f"{v} – {RATING_LABELS[v]}",
            )
            score = c1.slider("Overall score", 0.0, 100.0, 60.0)
            goals = c2.slider("Goal completion %", 0, 100, 70)
            collab = c1.slider("Collaboration", 1, 5, 3)
            code = c2.text_input("Project code (optional)")
            notes = st.text_area("Comments", max_chars=500)

            if st.form_submit_button("Submit review", type="primary"):
                try:
                    proj = None
                    if code.strip():
                        row = rm.fetch_one(
                            f"SELECT project_id FROM {rm.oltp}.projects WHERE project_code=%s",
                            (code.strip().upper(),),
                        )
                        if not row:
                            raise WorkforceIQError("Unknown project code.")
                        proj = row["project_id"]
                    rid, sync = rm.submit(
                        Review(int(pick), rd, rating, score, goals, collab, project_id=proj, comments=notes)
                    )
                    st.success(f"Review {rid} saved. {sync.message}")
                    st.cache_data.clear()
                except WorkforceIQError as e:
                    st.error(str(e))