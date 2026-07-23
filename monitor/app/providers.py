from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any

import httpx


SYMBOL_NAMES = {
    "MUUSDT": "Micron",
    "NOKUSDT": "Nokia",
    "ASTSUSDT": "AST SpaceMobile",
    "NVOUSDT": "Novo Nordisk",
    "MSTRUSDT": "Strategy",
    "COINUSDT": "Coinbase",
    "NVDAUSDT": "NVIDIA",
    "TSLAUSDT": "Tesla",
    "AAPLUSDT": "Apple",
    "AMDUSDT": "AMD",
    "PLTRUSDT": "Palantir",
}


class ProviderError(RuntimeError):
    pass


def _describe_exception(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    name = exc.__class__.__name__
    if isinstance(exc, httpx.HTTPError) and getattr(exc, "request", None):
        return f"{name} while requesting {exc.request.url}"
    return name


class BinanceFuturesProvider:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._history_cache: tuple[float, dict[str, list[dict[str, Any]]]] = (0.0, {})

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and "code" in payload and payload.get("code") not in (0, 200):
            raise ProviderError(str(payload.get("msg", payload)))
        return payload

    async def fetch(self, symbols: list[str]) -> dict[str, Any]:
        timeout = httpx.Timeout(12.0, connect=8.0)
        headers = {"User-Agent": "bn-stock-monitor/1.0"}
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
                premium, books, funding_info = await asyncio.gather(
                    self._get(client, "/fapi/v1/premiumIndex"),
                    self._get(client, "/fapi/v1/ticker/bookTicker"),
                    self._get(client, "/fapi/v1/fundingInfo"),
                )
                histories = await self._funding_histories(client, symbols)
        except Exception as exc:
            raise ProviderError(f"Binance public API unavailable: {_describe_exception(exc)}") from exc

        premium_map = {item.get("symbol"): item for item in premium if isinstance(item, dict)}
        book_map = {item.get("symbol"): item for item in books if isinstance(item, dict)}
        interval_map = {
            item.get("symbol"): float(item.get("fundingIntervalHours", 8))
            for item in funding_info
            if isinstance(item, dict)
        }
        records = []
        for symbol in symbols:
            price = premium_map.get(symbol)
            book = book_map.get(symbol)
            if not price or not book:
                continue
            interval = interval_map.get(symbol, 8.0)
            history = histories.get(symbol, [])
            rates = [float(item.get("fundingRate", 0)) for item in history]
            records.append(
                {
                    "symbol": symbol,
                    "ticker": symbol.removesuffix("USDT"),
                    "name": SYMBOL_NAMES.get(symbol, symbol.removesuffix("USDT")),
                    "mark_price": float(price.get("markPrice", 0)),
                    "index_price": float(price.get("indexPrice", 0)),
                    "funding_rate": float(price.get("lastFundingRate", 0)),
                    "funding_interval_hours": interval,
                    "next_funding_time": int(price.get("nextFundingTime", 0)),
                    "perp_bid": float(book.get("bidPrice", 0)),
                    "perp_ask": float(book.get("askPrice", 0)),
                    "history_rates": rates,
                    "history": history,
                }
            )
        if not records:
            raise ProviderError("No configured stock perpetual symbols were returned by Binance")
        return {"source": "live", "records": records, "error": None}

    async def _funding_histories(
        self, client: httpx.AsyncClient, symbols: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        cached_at, cached = self._history_cache
        if time.time() - cached_at < 300 and all(symbol in cached for symbol in symbols):
            return cached
        semaphore = asyncio.Semaphore(4)

        async def one(symbol: str) -> tuple[str, list[dict[str, Any]]]:
            async with semaphore:
                payload = await self._get(
                    client,
                    "/fapi/v1/fundingRate",
                    {"symbol": symbol, "limit": 21},
                )
                return symbol, payload if isinstance(payload, list) else []

        results = await asyncio.gather(*(one(symbol) for symbol in symbols), return_exceptions=True)
        merged = dict(cached)
        for result in results:
            if not isinstance(result, Exception):
                symbol, rows = result
                merged[symbol] = rows
        self._history_cache = (time.time(), merged)
        return merged


class DemoProvider:
    BASE = [
        ("NOKUSDT", 6.45, 0.001994, 2.1),
        ("ASTSUSDT", 42.18, 0.001508, 4.8),
        ("NVOUSDT", 71.32, 0.001309, 1.6),
        ("MUUSDT", 128.44, 0.000274, 3.4),
        ("MSTRUSDT", 176.22, 0.000198, 8.2),
        ("COINUSDT", 328.60, 0.000152, 5.3),
        ("NVDAUSDT", 185.31, 0.000083, 1.2),
        ("TSLAUSDT", 412.70, -0.000021, 3.1),
        ("AAPLUSDT", 269.50, 0.000046, 0.8),
        ("AMDUSDT", 221.60, 0.000061, 1.4),
        ("PLTRUSDT", 158.20, 0.000115, 2.6),
    ]

    async def fetch(self, symbols: list[str], reason: str | None = None) -> dict[str, Any]:
        now = time.time()
        next_funding = int((math.floor(now / 28800) * 28800 + 28800) * 1000)
        records = []
        wanted = set(symbols)
        for index, (symbol, base_price, rate, spread_bps) in enumerate(self.BASE):
            if symbol not in wanted:
                continue
            wave = math.sin(now / 17 + index) * 0.0018
            price = base_price * (1 + wave)
            funding_wave = math.sin(now / 61 + index * 0.8) * abs(rate) * 0.06
            live_rate = rate + funding_wave
            index_price = price
            perp_mid = price * (1 + spread_bps / 10_000)
            spread = max(perp_mid * 0.00012, 0.001)
            history_rates = [rate * (0.82 + 0.18 * math.sin(i * 0.9 + index)) for i in range(21)]
            records.append(
                {
                    "symbol": symbol,
                    "ticker": symbol.removesuffix("USDT"),
                    "name": SYMBOL_NAMES.get(symbol, symbol.removesuffix("USDT")),
                    "mark_price": perp_mid,
                    "index_price": index_price,
                    "funding_rate": live_rate,
                    "funding_interval_hours": 8.0,
                    "next_funding_time": next_funding,
                    "perp_bid": perp_mid - spread / 2,
                    "perp_ask": perp_mid + spread / 2,
                    "history_rates": history_rates,
                    "history": [
                        {
                            "fundingRate": str(value),
                            "fundingTime": int((now - (20 - i) * 28800) * 1000),
                            "markPrice": str(price),
                        }
                        for i, value in enumerate(history_rates)
                    ],
                }
            )
        await asyncio.sleep(0.03)
        return {
            "source": "demo",
            "records": records,
            "error": reason or "演示模式已启用",
        }
