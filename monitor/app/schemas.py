from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class SettingsUpdate(BaseModel):
    settings_version: int = 3
    provider_mode: str = "auto"
    refresh_seconds: int = Field(default=15, ge=5, le=300)
    total_capital: float = Field(default=1000.0, gt=0)
    perp_allocation: float = Field(default=0.5, ge=0.1, le=0.9)
    leverage: float = Field(default=1.0, ge=0.1, le=50)
    holding_days: int = Field(default=30, ge=1, le=365)
    min_annualized: float = Field(default=0.0, ge=-10, le=20)
    execution_mode: str = "maker"
    spot_fee_rate: float = Field(default=0.001, ge=0, le=0.1)
    spot_min_fee: float = Field(default=0.35, ge=0, le=100)
    perp_maker_fee: float = Field(default=0.0, ge=-0.01, le=0.1)
    perp_taker_fee: float = Field(default=0.0004, ge=0, le=0.1)
    binance_maker_fee: float = Field(default=0.0002, ge=-0.01, le=0.1)
    binance_taker_fee: float = Field(default=0.0005, ge=0, le=0.1)
    okx_maker_fee: float = Field(default=0.0002, ge=-0.01, le=0.1)
    okx_taker_fee: float = Field(default=0.0005, ge=0, le=0.1)
    slippage_bps: float = Field(default=2.0, ge=0, le=1000)
    extra_cost_bps: float = Field(default=0.0, ge=0, le=1000)
    extra_fixed_fee: float = Field(default=0.0, ge=0, le=1000)
    alert_annualized: float = Field(default=0.5, ge=-10, le=20)
    watch_symbols: list[str] = Field(default_factory=list, min_length=1, max_length=100)

    @field_validator("provider_mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in {"auto", "live", "demo"}:
            raise ValueError("provider_mode must be auto, live, or demo")
        return value

    @field_validator("execution_mode")
    @classmethod
    def validate_execution(cls, value: str) -> str:
        if value not in {"maker", "taker"}:
            raise ValueError("execution_mode must be maker or taker")
        return value

    @field_validator("watch_symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            symbol = value.strip().upper().replace("-", "")
            if symbol.endswith("SWAP"):
                symbol = symbol[:-4]
            if symbol and symbol not in result:
                result.append(symbol if symbol.endswith("USDT") else f"{symbol}USDT")
        if not result:
            raise ValueError("at least one watch symbol is required")
        return result


class PositionInput(BaseModel):
    symbol: str = Field(min_length=2, max_length=30)
    quantity: float = Field(gt=0)
    spot_entry: float = Field(gt=0)
    perp_entry: float = Field(gt=0)
    opened_at: str
    funding_received: float = 0.0
    opening_fees: float = Field(default=0.0, ge=0)
    spot_price_override: float | None = Field(default=None, gt=0)
    note: str = Field(default="", max_length=300)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        return symbol if symbol.endswith("USDT") else f"{symbol}USDT"

    @field_validator("opened_at")
    @classmethod
    def validate_opened_at(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
