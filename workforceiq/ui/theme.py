"""Visual identity: deep teal ink + saffron signal colour; Bricolage Grotesque headings, Instrument Sans body."""
import os

import streamlit as st

INK, TEAL, SAFFRON, ROSE, MIST = "#16262E", "#0F766E", "#E2A400", "#B4443C", "#E6EEEC"
DEPT_COLORS = {"Research & Development": TEAL, "Sales": SAFFRON, "Human Resources": "#5B6C9D",
               "All departments": INK}
RISK_COLORS = {"Low": "#7FA99B", "Medium": SAFFRON, "High": ROSE}

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@600;700&family=Instrument+Sans:wght@400;500;600&display=swap');
html, body, [class*="css"] {{ font-family: 'Instrument Sans', sans-serif; }}
h1, h2, h3 {{ font-family: 'Bricolage Grotesque', sans-serif; letter-spacing: -0.01em; color: {INK}; }}
[data-testid="stSidebar"] {{ background: {INK}; }}
[data-testid="stSidebar"] * {{ color: #E8F0EE !important; }}
[data-testid="stMetric"] {{ border-left: 4px solid {TEAL}; padding-left: 12px; }}
[data-testid="stMetricValue"] {{ font-family: 'Bricolage Grotesque', sans-serif; }}
.stButton > button[kind="primary"] {{ background: {TEAL}; border: 0; }}
</style>
"""


def apply_theme() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def style_fig(fig, height: int = 380):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40, b=10), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Instrument Sans", color=INK),
                      legend=dict(orientation="h", y=-0.2))
    return fig


def bootstrap_secrets() -> None:
    """Streamlit Cloud: copy [mysql] secrets into the environment (local runs use .env)."""
    try:
        for k, v in dict(st.secrets.get("mysql", {})).items():
            os.environ.setdefault(k, str(v))
    except Exception:  # no secrets file locally
        pass
