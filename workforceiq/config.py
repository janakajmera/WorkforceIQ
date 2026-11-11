"""Configuration. Credentials come ONLY from environment variables / .env / Streamlit secrets."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from dotenv import load_dotenv

from .exceptions import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = PROJECT_ROOT / "sql"
RAW_DATA_PATH = PROJECT_ROOT / "data" / "raw" / "WA_Fn-UseC_-HR-Employee-Attrition.csv"
SYNTHETIC_DIR = PROJECT_ROOT / "data" / "synthetic"

DEFAULT_STAGING = "wfiq_staging"
DEFAULT_OLTP = "wfiq_oltp"
DEFAULT_DW = "wfiq_dw"

_IDENT = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = ""
    password: str = field(default="", repr=False)  # never printed
    staging_schema: str = DEFAULT_STAGING
    oltp_schema: str = DEFAULT_OLTP
    dw_schema: str = DEFAULT_DW
    pool_size: int = 5
    connect_timeout: int = 10
    use_ssl: bool = False
    ssl_ca: Optional[str] = None

    def __post_init__(self) -> None:
        for name in (self.staging_schema, self.oltp_schema, self.dw_schema):
            if not _IDENT.match(name):
                raise ConfigurationError(f"Invalid schema name: {name!r}")

    @property
    def schema_map(self) -> dict[str, str]:
        """Maps the schema names used inside the .sql files to the configured ones."""
        return {
            DEFAULT_STAGING: self.staging_schema,
            DEFAULT_OLTP: self.oltp_schema,
            DEFAULT_DW: self.dw_schema,
        }

    def require_credentials(self) -> None:
        if not self.user or not self.password:
            raise ConfigurationError(
                "Database credentials are not configured. Copy .env.example to .env and set "
                "WFIQ_DB_USER and WFIQ_DB_PASSWORD (or add them to Streamlit secrets)."
            )

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> "Settings":
        def get(key: str, default: str = "") -> str:
            return str(env.get(key, default) or default)

        try:
            port = int(get("WFIQ_DB_PORT", "3306"))
        except ValueError as exc:
            raise ConfigurationError("WFIQ_DB_PORT must be an integer") from exc
        ca = get("WFIQ_DB_SSL_CA") or None
        return cls(
            host=get("WFIQ_DB_HOST", "127.0.0.1"),
            port=port,
            user=get("WFIQ_DB_USER"),
            password=get("WFIQ_DB_PASSWORD"),
            staging_schema=get("WFIQ_STAGING_SCHEMA", DEFAULT_STAGING),
            oltp_schema=get("WFIQ_OLTP_SCHEMA", DEFAULT_OLTP),
            dw_schema=get("WFIQ_DW_SCHEMA", DEFAULT_DW),
            pool_size=int(get("WFIQ_DB_POOL_SIZE", "5")),
            use_ssl=get("WFIQ_DB_SSL").lower() in _TRUE or ca is not None,
            ssl_ca=ca,
        )

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        return cls.from_mapping(os.environ)
