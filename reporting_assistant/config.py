"""Runtime settings, read from environment variables with safe defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ENV_FILE = Path(".env")


def load_env_file(path: Path = ENV_FILE) -> None:
    """Read KEY=VALUE lines from .env (written by `cli setup`) into the environment.
    Settings already exported in the shell take precedence."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()


@dataclass(frozen=True)
class Settings:
    # Where the sample ERP database, the reporting copy and the audit log live.
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("RA_DATA_DIR", "var")).resolve())
    semantic_layer_path: Path = PACKAGE_DIR / "semantic_layer.yaml"

    # LLM orchestrator (section 7 of the proposal): claude, gemini, groq,
    # openrouter, grok or demo. RA_LLM_MODEL names the model for non-Claude providers.
    provider: str = os.getenv("RA_PROVIDER", "claude").lower()
    llm_model: str = os.getenv("RA_LLM_MODEL", "") or os.getenv("RA_GROK_MODEL", "")
    model: str = os.getenv("RA_MODEL", "claude-opus-5-5")
    plan_effort: str = os.getenv("RA_PLAN_EFFORT", "high")
    narrative_effort: str = os.getenv("RA_NARRATIVE_EFFORT", "low")

    # Answer only the standard templates, without calling Claude.
    demo_mode: bool = os.getenv("RA_DEMO_MODE", "") not in ("", "0", "false")

    # Query service guardrails (NFR "Source system load").
    query_timeout_seconds: float = float(os.getenv("RA_QUERY_TIMEOUT", "30"))
    max_rows: int = int(os.getenv("RA_MAX_ROWS", "5000"))

    # SQL dialect the validator parses: "sqlite" (prototype) or "tsql" (SQL Server).
    sql_dialect: str = os.getenv("RA_SQL_DIALECT", "sqlite").lower()

    # Optional second model for summaries (e.g. Arctic writes SQL, Qwen writes summaries).
    summary_model: str = os.getenv("RA_SUMMARY_MODEL", "")

    # Confirm before running: new requests (not in the verified library) are
    # restated in plain words and run only after the user confirms.
    confirm_new_requests: bool = os.getenv("RA_CONFIRM_NEW", "1") not in ("", "0", "false")
    # Show the table and chart first; the summary is fetched separately.
    defer_summary: bool = os.getenv("RA_DEFER_SUMMARY", "1") not in ("", "0", "false")

    # Token quota per client per month (0 = no limit). Warn at 80%, block at 100%.
    monthly_token_quota: int = int(os.getenv("RA_MONTHLY_TOKEN_QUOTA", "0"))
    quota_warning_ratio: float = float(os.getenv("RA_QUOTA_WARNING", "0.8"))

    # Document capture: vision-language model and the portal's approval-workflow API.
    document_model: str = os.getenv("RA_DOCUMENT_MODEL", "")
    portal_api_url: str = os.getenv("RA_PORTAL_API_URL", "")  # empty: local stand-in workflow

    # Data freshness targets in hours (NFR "Data freshness").
    inventory_refresh_hours: float = float(os.getenv("RA_INVENTORY_REFRESH_HOURS", "1"))
    finance_refresh_hours: float = float(os.getenv("RA_FINANCE_REFRESH_HOURS", "24"))

    @property
    def erp_db(self) -> Path:
        return self.data_dir / "erp.db"

    @property
    def reporting_db(self) -> Path:
        return self.data_dir / "reporting.db"

    @property
    def app_db(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    def client_dir(self, client_id: str, default_client: str) -> Path:
        """Each client's own database folder. The default client uses data_dir."""
        if client_id == default_client:
            return self.data_dir
        if not client_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"Invalid client id {client_id!r}")
        return self.data_dir / "clients" / client_id


def get_settings() -> Settings:
    return Settings()
