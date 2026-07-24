from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .calculator import (
    CrossExchangeInput,
    calculate_position_pnl,
    project_cross_exchange_profit,
    utc_now_iso,
)
from .config import config
from .db import Database
from .providers import BinanceOkxFundingProvider, DemoProvider, ProviderError


class MonitorService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.provider = BinanceOkxFundingProvider(config.binance_futures_base_url, config.okx_base_url)
        self.demo = DemoProvider()
        self.snapshot: dict[str, Any] = {
            "status": "starting",
            "source": "none",
            "updated_at": None,
            "last_attempt_at": None,
            "error": None,
            "opportunities": [],
            "positions": [],
            "summary": {},
        }
        self.version = 0
        self.condition = asyncio.Condition()
        self._refresh_lock = asyncio.Lock()

    async def refresh(self) -> dict[str, Any]:
        if self._refresh_lock.locked():
            return self.snapshot
        async with self._refresh_lock:
            settings = self.db.get_settings()
            mode = settings.get("provider_mode") or config.monitor_provider_mode
            symbols = settings["watch_symbols"]
            try:
                if mode == "demo":
                    raw = await self.demo.fetch(symbols)
                else:
                    raw = await self.provider.fetch(symbols)
            except ProviderError as exc:
                error_text = str(exc) or exc.__class__.__name__
                if mode == "live":
                    raw = {"source": "error", "records": [], "error": error_text}
                elif self.snapshot.get("source") in {"live", "stale"} and self.snapshot.get("opportunities"):
                    self.snapshot = {
                        **self.snapshot,
                        "status": "stale",
                        "source": "stale",
                        "error": error_text,
                        "last_attempt_at": utc_now_iso(),
                    }
                    self.version += 1
                    async with self.condition:
                        self.condition.notify_all()
                    return self.snapshot
                else:
                    raw = await self.demo.fetch(symbols, error_text)

            opportunities = self._build_opportunities(raw["records"], settings)
            positions = self._build_positions(opportunities, settings)
            profitable = [item for item in opportunities if item["projection"]["net_profit"] > 0]
            best = opportunities[0] if opportunities else None
            total_pnl = sum(item["pnl"]["net_pnl"] for item in positions)
            self.snapshot = {
                "status": "ok" if raw["source"] in {"live", "demo"} else "error",
                "source": raw["source"],
                "updated_at": utc_now_iso(),
                "last_attempt_at": utc_now_iso(),
                "error": raw.get("error"),
                "opportunities": opportunities,
                "positions": positions,
                "settings": settings,
                "summary": {
                    "market_count": len(opportunities),
                    "profitable_count": len(profitable),
                    "best_symbol": best["symbol"] if best else None,
                    "best_annualized": best["annualized_current"] if best else 0,
                    "best_projected_profit": best["projection"]["net_profit"] if best else 0,
                    "position_count": len(positions),
                    "position_net_pnl": total_pnl,
                    "next_funding_time": min(
                        (item["next_funding_time"] for item in opportunities if item["next_funding_time"]),
                        default=0,
                    ),
                },
            }
            self.version += 1
            async with self.condition:
                self.condition.notify_all()
            return self.snapshot

    def _build_opportunities(self, records: list[dict[str, Any]], settings: dict[str, Any]) -> list[dict[str, Any]]:
        result = []
        binance_fee_rate = (
            settings["binance_maker_fee"]
            if settings["execution_mode"] == "maker"
            else settings["binance_taker_fee"]
        )
        okx_fee_rate = (
            settings["okx_maker_fee"]
            if settings["execution_mode"] == "maker"
            else settings["okx_taker_fee"]
        )
        for row in records:
            if row["long_ask"] <= 0 or row["short_bid"] <= 0:
                continue
            current_annualized = row["funding_spread_annualized"]
            projection = project_cross_exchange_profit(
                CrossExchangeInput(
                    total_capital=settings["total_capital"],
                    leverage=settings.get("leverage", 1.0),
                    holding_days=settings["holding_days"],
                    funding_spread_annualized=current_annualized,
                    binance_fee_rate=binance_fee_rate,
                    okx_fee_rate=okx_fee_rate,
                    slippage_bps=settings["slippage_bps"],
                    extra_cost_bps=settings["extra_cost_bps"],
                    extra_fixed_fee=settings["extra_fixed_fee"],
                    entry_basis_bps=row["entry_basis_bps"],
                )
            )
            risk_flags = []
            if current_annualized <= 0:
                risk_flags.append("无费差")
            elif current_annualized < settings["min_annualized"]:
                risk_flags.append("费差偏低")
            if current_annualized > 2:
                risk_flags.append("费率过热")
            if abs(row["entry_basis_bps"]) > 30:
                risk_flags.append("价差偏大")
            if not risk_flags:
                risk_flags.append("常规")
            result.append(
                {
                    **row,
                    "spot_bid": row["long_bid"],
                    "spot_ask": row["long_ask"],
                    "perp_bid": row["short_bid"],
                    "perp_ask": row["short_ask"],
                    "spot_price_kind": "cross_exchange",
                    "exit_basis_bps": row["entry_basis_bps"],
                    "annualized_current": current_annualized,
                    "projection": projection.__dict__,
                    "risk_flags": risk_flags,
                }
            )
        return sorted(
            result,
            key=lambda item: (
                item["projection"]["net_profit"],
                item["annualized_current"],
                item["annualized_7d"],
            ),
            reverse=True,
        )

    def _build_positions(self, opportunities: list[dict[str, Any]], settings: dict[str, Any]) -> list[dict[str, Any]]:
        market = {item["symbol"]: item for item in opportunities}
        fee_rate = (
            settings["binance_maker_fee"] + settings["okx_maker_fee"]
            if settings["execution_mode"] == "maker"
            else settings["binance_taker_fee"] + settings["okx_taker_fee"]
        )
        result = []
        for position in self.db.list_positions():
            quote = market.get(position["symbol"])
            if not quote:
                result.append({**position, "quote_available": False, "pnl": None})
                continue
            spot_bid = position.get("spot_price_override") or quote["spot_bid"]
            pnl = calculate_position_pnl(
                quantity=position["quantity"],
                spot_entry=position["spot_entry"],
                perp_entry=position["perp_entry"],
                spot_bid=spot_bid,
                perp_ask=quote["perp_ask"],
                funding_received=position.get("funding_received", 0),
                opening_fees=position.get("opening_fees", 0),
                spot_fee_rate=fee_rate,
                spot_min_fee=0,
                perp_fee_rate=0,
            )
            result.append(
                {
                    **position,
                    "quote_available": True,
                    "current_spot": spot_bid,
                    "current_perp": quote["perp_ask"],
                    "spot_price_kind": "manual" if position.get("spot_price_override") else "cross_exchange",
                    "pnl": pnl.__dict__,
                }
            )
        return result

    async def wait_for_update(self, after_version: int, timeout: float = 30) -> int:
        if self.version > after_version:
            return self.version
        try:
            async with self.condition:
                await asyncio.wait_for(self.condition.wait(), timeout=timeout)
        except TimeoutError:
            pass
        return self.version
