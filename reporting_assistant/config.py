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


def get_settings() -> Settings:
    return Settings()
