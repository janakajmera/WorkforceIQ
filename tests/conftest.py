import dataclasses
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workforceiq.config import SQL_DIR, Settings  # noqa: E402
from workforceiq.dal import AnalyticsManager, EmployeeManager, EtlManager, ProjectManager, ReviewManager  # noqa: E402
from workforceiq.db_manager import DatabaseConnection  # noqa: E402
from workforceiq.sql_runner import SqlScriptRunner  # noqa: E402
from workforceiq.synthesis import DataSynthesizer, StagingLoader, SynthesisConfig  # noqa: E402

SCRIPTS = ["01_create_staging.sql", "02_create_oltp.sql", "03_create_dw.sql", "04_oltp_reference_data.sql",
           "05_etl_staging_to_oltp.sql", "06_etl_oltp_to_dw.sql", "07_oltp_operations.sql", "08_analytics_views.sql"]


@pytest.fixture(scope="session")
def warehouse():
    """Builds an isolated test warehouse (schemas wfiq_t_*) from 1,500 synthetic employees."""
    base = Settings.from_env()
    if not base.user:
        pytest.skip("MySQL credentials not configured")
    cfg = dataclasses.replace(base, staging_schema="wfiq_t_staging", oltp_schema="wfiq_t_oltp", dw_schema="wfiq_t_dw")
    DatabaseConnection.reset()
    db = DatabaseConnection(cfg)
    ok, msg = db.healthcheck()
    if not ok:
        pytest.skip(f"MySQL unavailable: {msg}")
    for s in (cfg.staging_schema, cfg.oltp_schema, cfg.dw_schema):
        db.execute(f"DROP DATABASE IF EXISTS {s}")           # test schemas only
    runner = SqlScriptRunner(db)
    for f in SCRIPTS:
        runner.run_file(SQL_DIR / f)
    res = DataSynthesizer(SynthesisConfig(target_employees=1500, n_projects=150, as_of=date(2026, 11, 7))).run()
    StagingLoader().load(res)
    etl = EtlManager()
    etl.run_staging_to_oltp()
    etl.run_full_dw_etl()
    yield {"result": res, "db": db}
    for s in (cfg.staging_schema, cfg.oltp_schema, cfg.dw_schema):
        db.execute(f"DROP DATABASE IF EXISTS {s}")
    DatabaseConnection.reset()
