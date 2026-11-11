import plotly.express as px
import streamlit as st

from workforceiq.dal import AnalyticsManager
from workforceiq.exceptions import WorkforceIQError
from workforceiq.ui.theme import DEPT_COLORS, style_fig

st.title("Workforce overview")
am = AnalyticsManager()


@st.cache_data(ttl=300)
def load():
    return am.kpis(), am.headcount_by_department(), am.attrition_trend()


try:
    k, hc, trend = load()
except WorkforceIQError as e:
    st.error(str(e)); st.stop()
if not k or not k.get("headcount"):
    st.info("The warehouse is empty. Run `python manage.py pipeline --rows 100000` first."); st.stop()

c = st.columns(5)
c[0].metric("Active employees", f"{k['headcount']:,}")
c[1].metric("Historical attrition", f"{k['attrition_pct']}%")
c[2].metric("Avg monthly income", f"{int(k['avg_income']):,}")
c[3].metric("Active projects", f"{k['active_projects']:,}")
c[4].metric("Over-allocated staff", f"{k['overallocated']:,}")

l, r = st.columns(2)
fig = px.bar(hc, x="department_name", y="headcount", color="department_name", color_discrete_map=DEPT_COLORS,
             title="Headcount by department")
l.plotly_chart(style_fig(fig).update_layout(showlegend=False), width="stretch")
fig = px.line(trend, x="year_num", y="attrition_pct", markers=True, title="Attrition rate by year (%)")
r.plotly_chart(style_fig(fig), width="stretch")
