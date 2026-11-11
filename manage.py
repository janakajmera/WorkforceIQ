"""WorkforceIQ command line.

  python manage.py init-db                  create schemas, tables, procedures, views (idempotent)
  python manage.py reset-db --yes           DROP and recreate all three schemas (destructive)
  python manage.py synthesize --rows 100000 build synthetic data and load staging (truncates staging tables)
  python manage.py etl                      staging -> OLTP -> DW
  python manage.py pipeline --rows 100000   init-db + synthesize + etl
  python manage.py verify                   row counts + DW integrity checks
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date

from workforceiq.config import SQL_DIR
from workforceiq.dal import AnalyticsManager, EtlManager
from workforceiq.db_manager import DatabaseConnection
from workforceiq.sql_runner import SqlScriptRunner
from workforceiq.synthesis import DataSynthesizer, StagingLoader, SynthesisConfig

SCRIPTS = ["01_create_staging.sql", "02_create_oltp.sql", "03_create_dw.sql", "04_oltp_reference_data.sql",
           "05_etl_staging_to_oltp.sql", "06_etl_oltp_to_dw.sql", "07_oltp_operations.sql", "08_analytics_views.sql"]


def init_db() -> None:
    runner = SqlScriptRunner()
    for name in SCRIPTS:
        n = runner.run_file(SQL_DIR / name)
        print(f"  {name}: {n} statements")


def synthesize(rows: int, seed: int, as_of: date) -> None:
    t = time.time()
    res = DataSynthesizer(SynthesisConfig(target_employees=rows, seed=seed, as_of=as_of)).run()
    print(f"  generated {res.total_rows():,} rows in {time.time() - t:.1f}s: {res.counts()}")
    print(f"  injected noise: {res.noise_report}")
    print("  loading staging (existing staging rows are truncated; staging is a transient layer)")
    StagingLoader().load(res, truncate=True, progress=print)


def etl() -> None:
    m = EtlManager()
    for label, fn in (("staging -> OLTP", m.run_staging_to_oltp), ("OLTP -> DW", m.run_full_dw_etl)):
        t = time.time()
        fn()
        print(f"  {label}: {time.time() - t:.1f}s")
    print(m.run_log(20).to_string(index=False))


def verify() -> None:
    print(EtlManager().layer_counts().to_string(index=False))
    print(AnalyticsManager().integrity_checks().to_string(index=False))


def main() -> None:
    p = argparse.ArgumentParser(description="WorkforceIQ management commands")
    p.add_argument("command", choices=["init-db", "reset-db", "synthesize", "etl", "pipeline", "verify"])
    p.add_argument("--rows", type=int, default=100_000)
    p.add_argument("--yes", action="store_true", help="confirm destructive reset-db")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    ok, msg = DatabaseConnection().healthcheck()
    if not ok:
        raise SystemExit(f"Cannot connect: {msg}")
    print(f"Connected: {msg}")
    if a.command == "reset-db":
        st = DatabaseConnection().settings
        names = [st.staging_schema, st.oltp_schema, st.dw_schema]
        if not a.yes:
            raise SystemExit(f"This DROPS schemas {names}. Re-run with --yes to confirm.")
        for n in names:
            DatabaseConnection().execute(f"DROP DATABASE IF EXISTS {n}")
        print(f"  dropped {names}")
    if a.command in ("init-db", "reset-db", "pipeline"):
        init_db()
    if a.command in ("synthesize", "pipeline"):
        synthesize(a.rows, a.seed, a.as_of)
    if a.command in ("etl", "pipeline"):
        etl()
    if a.command in ("verify", "pipeline"):
        verify()


if __name__ == "__main__":
    main()
