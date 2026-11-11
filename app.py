"""WorkforceIQ -- Streamlit entry point (streamlit run app.py)."""
import streamlit as st

from workforceiq.ui.theme import apply_theme, bootstrap_secrets

st.set_page_config(page_title="WorkforceIQ", page_icon="📊", layout="wide")
bootstrap_secrets()
apply_theme()

from workforceiq.db_manager import DatabaseConnection  # noqa: E402

ok, msg = DatabaseConnection().healthcheck()
if not ok:
    st.title("WorkforceIQ")
    st.error(f"Database unavailable: {msg}")
    st.markdown("Set `WFIQ_DB_USER` / `WFIQ_DB_PASSWORD` in `.env` (local) or Streamlit secrets (cloud), then run "
                "`python manage.py pipeline --rows 100000`. See README.md.")
    st.stop()

nav = st.navigation([
    st.Page("views/overview.py", title="Overview", default=True),
    st.Page("views/analytics.py", title="Analytics"),
    st.Page("views/employees.py", title="Employees"),
    st.Page("views/projects.py", title="Projects"),
    st.Page("views/reviews.py", title="Reviews"),
    st.Page("views/pipeline.py", title="Data pipeline"),
])
st.sidebar.caption(f"Connected · {msg}")
nav.run()
