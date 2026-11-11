import streamlit as st

from workforceiq.dal import AnalyticsManager, EtlManager
from workforceiq.exceptions import WorkforceIQError

st.title("Data pipeline")
etl, am = EtlManager(), AnalyticsManager()
try:
    st.subheader("Rows per layer")
    st.dataframe(etl.layer_counts(), width="stretch", hide_index=True)
    st.subheader("Warehouse integrity checks")
    st.dataframe(am.integrity_checks(), width="stretch", hide_index=True)
    if st.button("Run incremental warehouse refresh", type="primary"):
        with st.spinner("Running sp_dw_run_etl …"):
            etl.run_full_dw_etl()
        st.cache_data.clear(); st.success("Warehouse refreshed.")
    st.subheader("ETL run log")
    st.dataframe(etl.run_log(40), width="stretch", hide_index=True)
except WorkforceIQError as e:
    st.error(str(e))
