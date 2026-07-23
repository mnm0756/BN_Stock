from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .calculator import (
    ProjectionInput,
    annualize_average,
    annualize_funding,
    calculate_position_pnl,
    project_profit,
    utc_now_iso,
)
from .config import config
from .db import Database
from .providers import BinanceFuturesProvider, DemoProvider, ProviderError


class MonitorService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.binance = BinanceFuturesProvider(config.binance_futures_base_url)
        self.demo = DemoProvider()
        self.snapshot: dict[str, Any] = {
            "status": "starting",
            "source": "none",
            "updated_at": None,
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
                    raw = await self.binance.fetch(symbols)
            except ProviderError as exc:
                if mode == "live":
                    raw = {"source": "error", "records": [], "error": str(exc)}
                else:
                    raw = await self.demo.fetch(symbols, str(exc))

            opportunities = self._build_opportunities(raw["records"], settings)
            positions = self._build_positions(opportunities, settings)
            profitable = [item for item in opportunities if item["projection"]["net_profit"] > 0]
            best = opportunities[0] if opportunities else None
            total_pnl = sum(item["pnl"]["net_pnl"] for item in positions)
            self.snapshot = {
                "status": "ok" if raw["source"] in {"live", "demo"} else "error",
                "source": raw["source"],
                "updated_at": utc_now_iso(),
                "error": raw.get("error"),
                "opportunities": opportunities,
                "positions": positions,
                "settings": settings,
                "summary": {
                    "market_count": len(opportunities),
                    "profitable_count": len(profitable),
                    "best_symbol": best["symbol"] if best else None,
                    "best_annualized": best["annualized_7d"] if best else 0,
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
        fee_rate = (
            settings["perp_maker_fee"]
            if settings["execution_mode"] == "maker"
            else settings["perp_taker_fee"]
        )
        for row in records:
            spot_ask = row["index_price"]
            spot_bid = row["index_price"]
            if spot_ask <= 0 or row["perp_bid"] <= 0:
                continue
            entry_basis_bps = (row["perp_bid"] / spot_ask - 1) * 10_000
            exit_basis_bps = (row["perp_ask"] / spot_bid - 1) * 10_000
            current_annualized = annualize_funding(
                row["funding_rate"], row["funding_interval_hours"]
            )
            historical = annualize_average(
                row["history_rates"], row["funding_interval_hours"]
            )
            projection = project_profit(
                ProjectionInput(
                    total_capital=settings["total_capital"],
                    perp_allocation=settings["perp_allocation"],
                    holding_days=settings["holding_days"],
                    funding_rate=(sum(row["history_rates"]) / len(row["history_rates"]))
                    if row["history_rates"]
                    else row["funding_rate"],
                    interval_hours=row["funding_interval_hours"],
                    spot_fee_rate=settings["spot_fee_rate"],
                    spot_min_fee=settings["spot_min_fee"],
                    perp_fee_rate=fee_rate,
                    slippage_bps=settings["slippage_bps"],
                    extra_cost_bps=settings["extra_cost_bps"],
                    extra_fixed_fee=settings["extra_fixed_fee"],
                    entry_basis_bps=entry_basis_bps,
                )
            )
            risk_flags = []
            if row["funding_rate"] <= 0:
                risk_flags.append("负资金费")
            if current_annualized > 2:
                risk_flags.append("费率过热")
            if abs(entry_basis_bps) > 100:
                risk_flags.append("价差偏大")
            if not risk_flags:
                risk_flags.append("常规")
            result.append(
                {
                    **row,
                    "spot_bid": spot_bid,
                    "spot_ask": spot_ask,
                    "spot_price_kind": "index_reference",
                    "entry_basis_bps": entry_basis_bps,
                    "exit_basis_bps": exit_basis_bps,
                    "annualized_current": current_annualized,
                    "annualized_7d": historical,
                    "projection": projection.__dict__,
                    "risk_flags": risk_flags,
                }
            )
        return sorted(
            result,
            key=lambda item: (item["projection"]["net_profit"], item["annualized_7d"]),
            reverse=True,
        )

    def _build_positions(self, opportunities: list[dict[str, Any]], settings: dict[str, Any]) -> list[dict[str, Any]]:
        market = {item["symbol"]: item for item in opportunities}
        fee_rate = (
            settings["perp_maker_fee"]
            if settings["execution_mode"] == "maker"
            else settings["perp_taker_fee"]
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
                spot_fee_rate=settings["spot_fee_rate"],
                spot_min_fee=settings["spot_min_fee"],
                perp_fee_rate=fee_rate,
            )
            result.append(
                {
                    **position,
                    "quote_available": True,
                    "current_spot": spot_bid,
                    "current_perp": quote["perp_ask"],
                    "spot_price_kind": "manual" if position.get("spot_price_override") else "index_reference",
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
