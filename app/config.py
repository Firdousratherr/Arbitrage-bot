from functools import lru_cache
import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str
    admin_ids: str = ""
    admin_telegram_ids: str = ""
    admin_secret_key: str = "8767"
    ai_api_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_max_log_entries: int = Field(default=40, ge=1, le=500)
    database_path: str = "data/arbitrage.sqlite3"
    log_level: str = "INFO"
    scan_interval_seconds: int = 30
    max_exchange_concurrency: int = 4
    default_min_profit: float = 0.5
    default_min_volume: float = 10_000
    default_alert_cooldown: int = 300
    enabled_exchanges: str = "xt,kucoin,gateio,mexc,okx,htx,kraken,bitget,bitrue,lbank,coinbase,bitfinex,phemex,cryptocom,poloniex"

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def admin_id_set(self) -> set[int]:
        raw = self.admin_telegram_ids or self.admin_ids
        return {int(value.strip()) for value in raw.split(",") if value.strip()}

    @property
    def exchange_names(self) -> list[str]:
        return [
            value.strip().lower()
            for value in self.enabled_exchanges.split(",")
            if value.strip() and value.strip().lower() != "bitmart"
        ]

    def exchange_credentials(self, name: str) -> dict[str, str]:
        prefix = name.upper()
        credentials = {
            "apiKey": os.getenv(f"{prefix}_API_KEY", ""),
            "secret": os.getenv(f"{prefix}_SECRET", ""),
            "password": os.getenv(f"{prefix}_PASSWORD", ""),
        }
        return {key: value for key, value in credentials.items() if value}


@lru_cache
def get_settings() -> Settings:
    return Settings()
