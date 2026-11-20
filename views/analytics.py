import plotly.express as px
import streamlit as st

from workforceiq.dal import AnalyticsManager
from workforceiq.exceptions import WorkforceIQError
from workforceiq.ui.theme import DEPT_COLORS, RISK_COLORS, style_fig

st.title("Analytics")


@st.cache_data(ttl=300, show_spinner="Querying the warehouse…")
def q(name, *args):
    return getattr(AnalyticsManager(), name)(*args)

t1, t2, t3, t4 = st.tabs(["Performance trends", "Top performers", "Attrition risk", "Project bottlenecks"])

try:
    with t1:
        yoy = q("yoy_performance")
        if yoy.empty:
            st.info("No review data in the warehouse yet.")
        else:
            fig = px.line(yoy, x="review_year", y="avg_score", color="department_name", markers=True,
                          color_discrete_map=DEPT_COLORS, title="Average review score by year")
            st.plotly_chart(style_fig(fig), width="stretch")
            fig = px.bar(yoy[yoy.department_name != "All departments"], x="review_year", y="yoy_change",
                         color="department_name", barmode="group", color_discrete_map=DEPT_COLORS,
                         title="Year-over-year change in score (LAG window)")
            st.plotly_chart(style_fig(fig), width="stretch")
    with t2:
        years = q("review_years")
        if years:
            a, b, c = st.columns(3)
            year = a.selectbox("Year", years)
            dept = b.selectbox("Department", ["All (company-wide rank)", "Research & Development", "Sales",
                                              "Human Resources"])
            n = c.slider("Top N (DENSE_RANK ≤ N)", 3, 25, 10)
            df = q("top_performers", year, None if dept.startswith("All") else dept, n)
            st.dataframe(df, width="stretch", hide_index=True)
    with t3:
        st.caption("Transparent additive risk score (overtime, satisfaction, pay vs peers, promotion gap …). "
                   "Age, gender and marital status are deliberately excluded.")
        band = q("attrition_by_band")
        if not band.empty:
            a, b = st.columns(2)
            fig = px.bar(band, x="risk_band", y="actual_attrition_pct", color="risk_band",
                         color_discrete_map=RISK_COLORS, category_orders={"risk_band": ["Low", "Medium", "High"]},
                         title="Actual attrition by risk band (validation)")
            a.plotly_chart(style_fig(fig), width="stretch")
            rd = q("risk_by_department")
            fig = px.bar(rd, x="department_name", y="employees", color="risk_band", color_discrete_map=RISK_COLORS,
                         title="Active employees by risk band")
            b.plotly_chart(style_fig(fig), width="stretch")
            st.subheader("Highest-risk active employees")
            st.dataframe(q("at_risk_employees", 25), width="stretch", hide_index=True)
    with t4:
        bn = q("project_bottlenecks", 20)
        if not bn.empty:
            fig = px.bar(bn.head(15), x="bottleneck_score", y="project_code", orientation="h", color="priority",color_discrete_map=RISK_COLORS,
                         hover_data=["project_name", "days_overdue", "overallocated_members"],
                         title="Top bottleneck projects (overdue + over-allocated teams)")
            st.plotly_chart(style_fig(fig, 460).update_yaxes(autorange="reversed"), width="stretch")
            st.dataframe(bn, width="stretch", hide_index=True)
            oa = q("overallocated_by_department")
            st.plotly_chart(style_fig(px.bar(oa, x="department_name", y="overallocated", color="department_name", color_discrete_map=DEPT_COLORS,
                                             title="Over-allocated employees (>100%) by department")),
                            width="stretch")
except WorkforceIQError as e:
    st.error(str(e))
