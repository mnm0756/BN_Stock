from __future__ import annotations

import asyncio
import math
import time
from typing import Any

import httpx

from .calculator import annualize_average, annualize_funding


SYMBOL_NAMES = {
    "BTCUSDT": "Bitcoin",
    "ETHUSDT": "Ethereum",
    "SOLUSDT": "Solana",
    "XRPUSDT": "XRP",
    "DOGEUSDT": "Dogecoin",
    "BNBUSDT": "BNB",
    "ADAUSDT": "Cardano",
    "LINKUSDT": "Chainlink",
    "AVAXUSDT": "Avalanche",
    "SUIUSDT": "Sui",
    "LTCUSDT": "Litecoin",
    "BCHUSDT": "Bitcoin Cash",
    "TRXUSDT": "TRON",
    "TONUSDT": "Toncoin",
    "DOTUSDT": "Polkadot",
    "NEARUSDT": "NEAR",
    "AAVEUSDT": "Aave",
    "UNIUSDT": "Uniswap",
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


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper().replace("-", "")
    if symbol.endswith("SWAP"):
        symbol = symbol[:-4]
    return symbol if symbol.endswith("USDT") else f"{symbol}USDT"


def okx_inst_id(symbol: str) -> str:
    base = normalize_symbol(symbol).removesuffix("USDT")
    return f"{base}-USDT-SWAP"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class BinanceOkxFundingProvider:
    def __init__(self, binance_base_url: str, okx_base_url: str) -> None:
        self.binance_base_url = binance_base_url.rstrip("/")
        self.okx_base_url = okx_base_url.rstrip("/")
        self._history_cache: tuple[float, dict[str, dict[str, list[dict[str, Any]]]]] = (
            0.0,
            {"binance": {}, "okx": {}},
        )

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = await client.get(f"{base_url}{path}", params=params)
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0", 200):
                    raise ProviderError(str(payload.get("msg") or payload))
                return payload
            except Exception as exc:  # retry transient TLS/proxy hiccups once
                last_error = exc
                await asyncio.sleep(0.25)
        assert last_error is not None
        raise last_error

    async def fetch(self, symbols: list[str]) -> dict[str, Any]:
        wanted = [normalize_symbol(symbol) for symbol in symbols]
        timeout = httpx.Timeout(18.0, connect=10.0)
        headers = {"User-Agent": "bn-okx-funding-monitor/1.0"}
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
                (
                    binance_premium,
                    binance_books,
                    binance_funding_info,
                    okx_tickers,
                    okx_funding,
                    histories,
                ) = await asyncio.gather(
                    self._get_json(client, self.binance_base_url, "/fapi/v1/premiumIndex"),
                    self._get_json(client, self.binance_base_url, "/fapi/v1/ticker/bookTicker"),
                    self._get_json(client, self.binance_base_url, "/fapi/v1/fundingInfo"),
                    self._get_json(client, self.okx_base_url, "/api/v5/market/tickers", {"instType": "SWAP"}),
                    self._okx_funding_rates(client, wanted),
                    self._funding_histories(client, wanted),
                )
        except Exception as exc:
            raise ProviderError(f"Binance/OKX public API unavailable: {_describe_exception(exc)}") from exc

        premium_map = {item.get("symbol"): item for item in binance_premium if isinstance(item, dict)}
        book_map = {item.get("symbol"): item for item in binance_books if isinstance(item, dict)}
        interval_map = {
            item.get("symbol"): _float(item.get("fundingIntervalHours"), 8.0)
            for item in binance_funding_info
            if isinstance(item, dict)
        }
        okx_ticker_map = {
            item.get("instId"): item
            for item in okx_tickers.get("data", [])
            if isinstance(item, dict)
        }
        okx_funding_map = {
            item.get("instId"): item
            for item in okx_funding
            if isinstance(item, dict)
        }

        records = []
        for symbol in wanted:
            inst_id = okx_inst_id(symbol)
            binance_price = premium_map.get(symbol)
            binance_book = book_map.get(symbol)
            okx_rate = okx_funding_map.get(inst_id)
            okx_book = okx_ticker_map.get(inst_id)
            if not binance_price or not binance_book or not okx_rate or not okx_book:
                continue
            record = self._record(symbol, inst_id, binance_price, binance_book, okx_rate, okx_book, histories)
            if record:
                records.append(record)
        if not records:
            raise ProviderError("No configured Binance/OKX USDT swap symbols were returned by both exchanges")
        return {"source": "live", "records": records, "error": None}

    async def _okx_funding_rates(
        self,
        client: httpx.AsyncClient,
        symbols: list[str],
    ) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(6)

        async def one(symbol: str) -> list[dict[str, Any]]:
            async with semaphore:
                payload = await self._get_json(
                    client,
                    self.okx_base_url,
                    "/api/v5/public/funding-rate",
                    {"instId": okx_inst_id(symbol)},
                )
                return payload.get("data", []) if isinstance(payload, dict) else []

        results = await asyncio.gather(*(one(symbol) for symbol in symbols), return_exceptions=True)
        merged: list[dict[str, Any]] = []
        for result in results:
            if not isinstance(result, Exception):
                merged.extend(result)
        return merged

    async def _funding_histories(
        self,
        client: httpx.AsyncClient,
        symbols: list[str],
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        cached_at, cached = self._history_cache
        if time.time() - cached_at < 300 and all(
            symbol in cached["binance"] and symbol in cached["okx"] for symbol in symbols
        ):
            return cached
        semaphore = asyncio.Semaphore(5)

        async def binance_one(symbol: str) -> tuple[str, list[dict[str, Any]]]:
            async with semaphore:
                payload = await self._get_json(
                    client,
                    self.binance_base_url,
                    "/fapi/v1/fundingRate",
                    {"symbol": symbol, "limit": 21},
                )
                return symbol, payload if isinstance(payload, list) else []

        async def okx_one(symbol: str) -> tuple[str, list[dict[str, Any]]]:
            async with semaphore:
                payload = await self._get_json(
                    client,
                    self.okx_base_url,
                    "/api/v5/public/funding-rate-history",
                    {"instId": okx_inst_id(symbol), "limit": 21},
                )
                rows = payload.get("data", []) if isinstance(payload, dict) else []
                return symbol, rows

        binance_results = await asyncio.gather(*(binance_one(symbol) for symbol in symbols), return_exceptions=True)
        okx_results = await asyncio.gather(*(okx_one(symbol) for symbol in symbols), return_exceptions=True)
        merged = {"binance": dict(cached["binance"]), "okx": dict(cached["okx"])}
        for result in binance_results:
            if not isinstance(result, Exception):
                symbol, rows = result
                merged["binance"][symbol] = rows
        for result in okx_results:
            if not isinstance(result, Exception):
                symbol, rows = result
                merged["okx"][symbol] = rows
        self._history_cache = (time.time(), merged)
        return merged

    def _record(
        self,
        symbol: str,
        inst_id: str,
        binance_price: dict[str, Any],
        binance_book: dict[str, Any],
        okx_rate: dict[str, Any],
        okx_book: dict[str, Any],
        histories: dict[str, dict[str, list[dict[str, Any]]]],
    ) -> dict[str, Any] | None:
        binance_rate = _float(binance_price.get("lastFundingRate"))
        okx_current = _float(okx_rate.get("fundingRate"))
        binance_interval = 8.0
        okx_interval = (
            (_int(okx_rate.get("nextFundingTime")) - _int(okx_rate.get("fundingTime"))) / 3_600_000
            if _int(okx_rate.get("nextFundingTime")) and _int(okx_rate.get("fundingTime"))
            else 8.0
        )
        okx_interval = okx_interval if okx_interval > 0 else 8.0

        binance_annualized = annualize_funding(binance_rate, binance_interval)
        okx_annualized = annualize_funding(okx_current, okx_interval)
        if binance_annualized >= okx_annualized:
            short_exchange = "Binance"
            long_exchange = "OKX"
            short_bid = _float(binance_book.get("bidPrice"))
            short_ask = _float(binance_book.get("askPrice"))
            long_bid = _float(okx_book.get("bidPx"))
            long_ask = _float(okx_book.get("askPx"))
            short_rate = binance_rate
            long_rate = okx_current
        else:
            short_exchange = "OKX"
            long_exchange = "Binance"
            short_bid = _float(okx_book.get("bidPx"))
            short_ask = _float(okx_book.get("askPx"))
            long_bid = _float(binance_book.get("bidPrice"))
            long_ask = _float(binance_book.get("askPrice"))
            short_rate = okx_current
            long_rate = binance_rate
        if short_bid <= 0 or short_ask <= 0 or long_bid <= 0 or long_ask <= 0:
            return None

        spread_annualized = abs(binance_annualized - okx_annualized)
        spread_rate_8h = spread_annualized / (365 * 3)
        history_rates = self._spread_history_rates(symbol)
        historical_annualized = annualize_average(history_rates, 8.0)
        entry_basis_bps = (short_bid / long_ask - 1) * 10_000
        funding_times = [
            value
            for value in [_int(binance_price.get("nextFundingTime")), _int(okx_rate.get("fundingTime"))]
            if value
        ]
        return {
            "symbol": symbol,
            "ticker": symbol.removesuffix("USDT"),
            "name": SYMBOL_NAMES.get(symbol, symbol.removesuffix("USDT")),
            "okx_inst_id": inst_id,
            "funding_rate": spread_rate_8h,
            "funding_interval_hours": 8.0,
            "next_funding_time": min(funding_times, default=0),
            "binance_funding_rate": binance_rate,
            "okx_funding_rate": okx_current,
            "binance_annualized": binance_annualized,
            "okx_annualized": okx_annualized,
            "funding_spread_annualized": spread_annualized,
            "annualized_7d": historical_annualized,
            "short_exchange": short_exchange,
            "long_exchange": long_exchange,
            "short_rate": short_rate,
            "long_rate": long_rate,
            "binance_mark_price": _float(binance_price.get("markPrice")),
            "binance_index_price": _float(binance_price.get("indexPrice")),
            "binance_bid": _float(binance_book.get("bidPrice")),
            "binance_ask": _float(binance_book.get("askPrice")),
            "okx_last": _float(okx_book.get("last")),
            "okx_bid": _float(okx_book.get("bidPx")),
            "okx_ask": _float(okx_book.get("askPx")),
            "short_bid": short_bid,
            "short_ask": short_ask,
            "long_bid": long_bid,
            "long_ask": long_ask,
            "entry_basis_bps": entry_basis_bps,
            "history_rates": history_rates,
            "history": histories["binance"].get(symbol, []),
        }

    def _spread_history_rates(self, symbol: str) -> list[float]:
        _, cached = self._history_cache
        binance_rows = cached["binance"].get(symbol, [])
        okx_rows = cached["okx"].get(symbol, [])
        okx_by_time = {
            _int(row.get("fundingTime")): _float(row.get("realizedRate", row.get("fundingRate")))
            for row in okx_rows
        }
        rates = []
        for row in binance_rows:
            timestamp = _int(row.get("fundingTime"))
            okx_rate = okx_by_time.get(timestamp)
            if okx_rate is None:
                continue
            rates.append(abs(_float(row.get("fundingRate")) - okx_rate))
        return rates[-21:]


class DemoProvider:
    BASE = [
        ("BTCUSDT", 65100, 0.000031, -0.000008, 1.4),
        ("ETHUSDT", 1928, 0.000015, 0.000042, -0.8),
        ("SOLUSDT", 126.8, 0.000090, 0.000018, 2.0),
        ("XRPUSDT", 1.13, -0.000020, 0.000034, -1.2),
        ("DOGEUSDT", 0.167, 0.000055, 0.000006, 3.6),
        ("BNBUSDT", 544, 0.000008, 0.000026, 0.7),
        ("ADAUSDT", 0.482, 0.000012, -0.000018, -0.4),
        ("LINKUSDT", 14.2, 0.000033, 0.000011, 1.1),
        ("AVAXUSDT", 19.7, -0.000010, 0.000049, -2.2),
        ("SUIUSDT", 2.82, 0.000063, 0.000020, 1.8),
        ("LTCUSDT", 83.5, 0.000018, 0.000004, 0.5),
        ("BCHUSDT", 216.3, 0.000022, -0.000014, 1.0),
    ]

    async def fetch(self, symbols: list[str], reason: str | None = None) -> dict[str, Any]:
        now = time.time()
        next_funding = int((math.floor(now / 28800) * 28800 + 28800) * 1000)
        wanted = {normalize_symbol(symbol) for symbol in symbols}
        records = []
        for index, (symbol, price, binance_rate, okx_rate, basis_bps) in enumerate(self.BASE):
            if symbol not in wanted:
                continue
            wave = math.sin(now / 19 + index) * 0.0015
            b_price = price * (1 + wave)
            o_price = price * (1 + wave + basis_bps / 10_000)
            b_spread = max(b_price * 0.00008, 0.00001)
            o_spread = max(o_price * 0.0001, 0.00001)
            b_annualized = annualize_funding(binance_rate, 8)
            o_annualized = annualize_funding(okx_rate, 8)
            if b_annualized >= o_annualized:
                short_exchange = "Binance"
                long_exchange = "OKX"
                short_bid = b_price - b_spread / 2
                short_ask = b_price + b_spread / 2
                long_bid = o_price - o_spread / 2
                long_ask = o_price + o_spread / 2
                short_rate = binance_rate
                long_rate = okx_rate
            else:
                short_exchange = "OKX"
                long_exchange = "Binance"
                short_bid = o_price - o_spread / 2
                short_ask = o_price + o_spread / 2
                long_bid = b_price - b_spread / 2
                long_ask = b_price + b_spread / 2
                short_rate = okx_rate
                long_rate = binance_rate
            spread_annualized = abs(b_annualized - o_annualized)
            history_rates = [
                abs((binance_rate - okx_rate) * (0.72 + 0.28 * math.sin(i * 0.7 + index)))
                for i in range(21)
            ]
            records.append(
                {
                    "symbol": symbol,
                    "ticker": symbol.removesuffix("USDT"),
                    "name": SYMBOL_NAMES.get(symbol, symbol.removesuffix("USDT")),
                    "okx_inst_id": okx_inst_id(symbol),
                    "funding_rate": spread_annualized / (365 * 3),
                    "funding_interval_hours": 8.0,
                    "next_funding_time": next_funding,
                    "binance_funding_rate": binance_rate,
                    "okx_funding_rate": okx_rate,
                    "binance_annualized": b_annualized,
                    "okx_annualized": o_annualized,
                    "funding_spread_annualized": spread_annualized,
                    "annualized_7d": annualize_average(history_rates, 8),
                    "short_exchange": short_exchange,
                    "long_exchange": long_exchange,
                    "short_rate": short_rate,
                    "long_rate": long_rate,
                    "binance_mark_price": b_price,
                    "binance_index_price": b_price,
                    "binance_bid": b_price - b_spread / 2,
                    "binance_ask": b_price + b_spread / 2,
                    "okx_last": o_price,
                    "okx_bid": o_price - o_spread / 2,
                    "okx_ask": o_price + o_spread / 2,
                    "short_bid": short_bid,
                    "short_ask": short_ask,
                    "long_bid": long_bid,
                    "long_ask": long_ask,
                    "entry_basis_bps": (short_bid / long_ask - 1) * 10_000,
                    "history_rates": history_rates,
                    "history": [],
                }
            )
        await asyncio.sleep(0.03)
        return {
            "source": "demo",
            "records": records,
            "error": reason or "演示模式已启用",
        }
