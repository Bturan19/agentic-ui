from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    agent_model: str = "google-vertex:gemini-2.5-flash"

    toolbox_mcp_url: str = ""
    bigquery_project: str = ""

    marketplace_api_base_url: str = ""
    marketplace_api_token: str = ""
    marketplace_dry_run: bool = True

    reporting_api_base_url: str = ""
    reporting_api_token: str = ""

    postgres_dsn: str = ""
    pg_campaigns_table: str = "campaigns"
    pg_campaign_daily_table: str = "campaign_daily"
    pg_category_daily_table: str = "category_product_daily"

    integration_token: str = ""
    event_webhook_url: str = ""

    database_path: str = "data/agentic_ui.db"
    journal_dir: str = "data/journal"
    memory_dir: str = "data/memory"
    skills_dir: str = "skills"
    agent_md_path: str = "config/agent.md"
    policy_path: str = "config/policy.yaml"

    scheduler_enabled: bool = False
    daily_review_cron: str = "0 9 * * *"
    weekly_learning_cron: str = "30 8 * * 1"

    timezone: str = "Europe/Istanbul"

    @property
    def db_path(self) -> Path:
        return resolve_path(self.database_path)

    @property
    def journal_path(self) -> Path:
        return resolve_path(self.journal_dir)

    @property
    def memory_path(self) -> Path:
        return resolve_path(self.memory_dir)

    @property
    def skills_path(self) -> Path:
        return resolve_path(self.skills_dir)

    @property
    def agent_md_file(self) -> Path:
        return resolve_path(self.agent_md_path)

    @property
    def policy_file(self) -> Path:
        return resolve_path(self.policy_path)

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_path.mkdir(parents=True, exist_ok=True)
        self.memory_path.mkdir(parents=True, exist_ok=True)


settings = Settings()
