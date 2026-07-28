from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[1]


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    monitor_provider_mode: str = "auto"
    monitor_refresh_seconds: int = 15
    binance_futures_base_url: str = "https://fapi.binance.com"
    okx_base_url: str = "https://www.okx.com"
    aster_futures_base_url: str = "https://fapi.asterdex.com"
    hyperliquid_info_url: str = "https://api.hyperliquid.xyz/info"
    database_path: Path = ROOT / "data" / "monitor.db"


config = AppConfig()
