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
    "CXMTUSDT": "ChangXin Memory",
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


def hyper_coin(symbol: str) -> str:
    base = normalize_symbol(symbol).removesuffix("USDT")
    return f"xyz:{base}"


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


def _next_hour_millis() -> int:
    now_ms = int(time.time() * 1000)
    hour_ms = 3_600_000
    return (now_ms // hour_ms + 1) * hour_ms


class AsterHyperliquidFundingProvider:
    def __init__(self, aster_base_url: str, hyperliquid_info_url: str) -> None:
        self.aster_base_url = aster_base_url.rstrip("/")
        self.hyperliquid_info_url = hyperliquid_info_url
        self._history_cache: tuple[float, dict[str, dict[str, list[dict[str, Any]]]]] = (
            0.0,
            {"aster": {}, "hyper": {}},
        )

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = await client.get(f"{self.aster_base_url}{path}", params=params)
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0", 200):
                    raise ProviderError(str(payload.get("msg") or payload))
                return payload
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.25)
        assert last_error is not None
        raise last_error

    async def _post_hyper(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = await client.post(self.hyperliquid_info_url, json=payload)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.25)
        assert last_error is not None
        raise last_error

    async def fetch(self, symbols: list[str]) -> dict[str, Any]:
        wanted = [normalize_symbol(symbol) for symbol in symbols]
        timeout = httpx.Timeout(18.0, connect=10.0)
        headers = {"User-Agent": "bn-cxmt-basis-monitor/1.0"}
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
                (
                    aster_premium,
                    aster_books,
                    aster_funding_info,
                    hyper_meta,
                    hyper_books,
                    histories,
                ) = await asyncio.gather(
                    self._aster_premiums(client, wanted),
                    self._aster_books(client, wanted),
                    self._aster_funding_info(client, wanted),
                    self._post_hyper(client, {"type": "metaAndAssetCtxs", "dex": "xyz"}),
                    self._hyper_books(client, wanted),
                    self._funding_histories(client, wanted),
                )
        except Exception as exc:
            raise ProviderError(f"BN Wallet/Aster and Hyperliquid public API unavailable: {_describe_exception(exc)}") from exc

        hyper_ctx_map = self._hyper_contexts(hyper_meta)
        records = []
        for symbol in wanted:
            coin = hyper_coin(symbol)
            aster_price = aster_premium.get(symbol)
            aster_book = aster_books.get(symbol)
            aster_info = aster_funding_info.get(symbol, {})
            hyper_ctx = hyper_ctx_map.get(coin)
            hyper_book = hyper_books.get(coin)
            if not aster_price or not aster_book or not hyper_ctx or not hyper_book:
                continue
            record = self._record(symbol, coin, aster_price, aster_book, aster_info, hyper_ctx, hyper_book, histories)
            if record:
                records.append(record)
        if not records:
            raise ProviderError("No configured BN Wallet/Aster and Hyperliquid CXMT symbols were returned by both venues")
        return {"source": "live", "records": records, "error": None}

    async def _aster_premiums(self, client: httpx.AsyncClient, symbols: list[str]) -> dict[str, dict[str, Any]]:
        semaphore = asyncio.Semaphore(5)

        async def one(symbol: str) -> tuple[str, dict[str, Any]]:
            async with semaphore:
                payload = await self._get_json(client, "/fapi/v3/premiumIndex", {"symbol": symbol})
                return symbol, payload if isinstance(payload, dict) else {}

        results = await asyncio.gather(*(one(symbol) for symbol in symbols), return_exceptions=True)
        return {symbol: row for result in results if not isinstance(result, Exception) for symbol, row in [result]}

    async def _aster_books(self, client: httpx.AsyncClient, symbols: list[str]) -> dict[str, dict[str, Any]]:
        semaphore = asyncio.Semaphore(5)

        async def one(symbol: str) -> tuple[str, dict[str, Any]]:
            async with semaphore:
                payload = await self._get_json(client, "/fapi/v3/ticker/bookTicker", {"symbol": symbol})
                return symbol, payload if isinstance(payload, dict) else {}

        results = await asyncio.gather(*(one(symbol) for symbol in symbols), return_exceptions=True)
        return {symbol: row for result in results if not isinstance(result, Exception) for symbol, row in [result]}

    async def _aster_funding_info(self, client: httpx.AsyncClient, symbols: list[str]) -> dict[str, dict[str, Any]]:
        semaphore = asyncio.Semaphore(5)

        async def one(symbol: str) -> tuple[str, dict[str, Any]]:
            async with semaphore:
                payload = await self._get_json(client, "/fapi/v3/fundingInfo", {"symbol": symbol})
                rows = payload if isinstance(payload, list) else []
                row = next((item for item in rows if item.get("symbol") == symbol), {})
                return symbol, row

        results = await asyncio.gather(*(one(symbol) for symbol in symbols), return_exceptions=True)
        return {symbol: row for result in results if not isinstance(result, Exception) for symbol, row in [result]}

    async def _hyper_books(self, client: httpx.AsyncClient, symbols: list[str]) -> dict[str, dict[str, Any]]:
        semaphore = asyncio.Semaphore(5)

        async def one(symbol: str) -> tuple[str, dict[str, Any]]:
            coin = hyper_coin(symbol)
            async with semaphore:
                payload = await self._post_hyper(client, {"type": "l2Book", "coin": coin})
                return coin, payload if isinstance(payload, dict) else {}

        results = await asyncio.gather(*(one(symbol) for symbol in symbols), return_exceptions=True)
        return {coin: row for result in results if not isinstance(result, Exception) for coin, row in [result]}

    def _hyper_contexts(self, payload: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(payload, list) or len(payload) < 2:
            return {}
        meta, contexts = payload[0], payload[1]
        universe = meta.get("universe", []) if isinstance(meta, dict) else []
        if not isinstance(universe, list) or not isinstance(contexts, list):
            return {}
        return {
            asset.get("name"): ctx
            for asset, ctx in zip(universe, contexts)
            if isinstance(asset, dict) and isinstance(ctx, dict)
        }

    async def _funding_histories(
        self,
        client: httpx.AsyncClient,
        symbols: list[str],
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        cached_at, cached = self._history_cache
        if time.time() - cached_at < 300 and all(
            symbol in cached["aster"] and symbol in cached["hyper"] for symbol in symbols
        ):
            return cached
        semaphore = asyncio.Semaphore(5)
        start_time = int((time.time() - 7 * 24 * 60 * 60) * 1000)

        async def aster_one(symbol: str) -> tuple[str, list[dict[str, Any]]]:
            async with semaphore:
                payload = await self._get_json(client, "/fapi/v3/fundingRate", {"symbol": symbol, "limit": 168})
                return symbol, payload if isinstance(payload, list) else []

        async def hyper_one(symbol: str) -> tuple[str, list[dict[str, Any]]]:
            async with semaphore:
                payload = await self._post_hyper(
                    client,
                    {"type": "fundingHistory", "coin": hyper_coin(symbol), "startTime": start_time},
                )
                rows = payload if isinstance(payload, list) else []
                return symbol, rows[-168:]

        aster_results = await asyncio.gather(*(aster_one(symbol) for symbol in symbols), return_exceptions=True)
        hyper_results = await asyncio.gather(*(hyper_one(symbol) for symbol in symbols), return_exceptions=True)
        merged = {"aster": dict(cached["aster"]), "hyper": dict(cached["hyper"])}
        for result in aster_results:
            if not isinstance(result, Exception):
                symbol, rows = result
                merged["aster"][symbol] = rows
        for result in hyper_results:
            if not isinstance(result, Exception):
                symbol, rows = result
                merged["hyper"][symbol] = rows
        self._history_cache = (time.time(), merged)
        return merged

    def _record(
        self,
        symbol: str,
        coin: str,
        aster_price: dict[str, Any],
        aster_book: dict[str, Any],
        aster_info: dict[str, Any],
        hyper_ctx: dict[str, Any],
        hyper_book: dict[str, Any],
        histories: dict[str, dict[str, list[dict[str, Any]]]],
    ) -> dict[str, Any] | None:
        aster_rate = _float(aster_price.get("lastFundingRate"))
        hyper_rate = _float(hyper_ctx.get("funding"))
        aster_interval = _float(aster_info.get("fundingIntervalHours"), 1.0) or 1.0
        hyper_interval = 1.0
        aster_annualized = annualize_funding(aster_rate, aster_interval)
        hyper_annualized = annualize_funding(hyper_rate, hyper_interval)

        aster_bid = _float(aster_book.get("bidPrice"))
        aster_ask = _float(aster_book.get("askPrice"))
        hyper_bid, hyper_ask = self._hyper_bid_ask(hyper_book)
        if min(aster_bid, aster_ask, hyper_bid, hyper_ask) <= 0:
            return None

        if aster_annualized >= hyper_annualized:
            short_exchange = "BN 钱包/Aster"
            long_exchange = "Hyperliquid"
            short_bid = aster_bid
            short_ask = aster_ask
            long_bid = hyper_bid
            long_ask = hyper_ask
            short_rate = aster_rate
            long_rate = hyper_rate
        else:
            short_exchange = "Hyperliquid"
            long_exchange = "BN 钱包/Aster"
            short_bid = hyper_bid
            short_ask = hyper_ask
            long_bid = aster_bid
            long_ask = aster_ask
            short_rate = hyper_rate
            long_rate = aster_rate

        spread_annualized = abs(aster_annualized - hyper_annualized)
        history_rates = self._spread_history_rates(symbol, aster_interval, hyper_interval)
        historical_annualized = annualize_average(history_rates, 8.0)
        entry_basis_bps = (short_bid / long_ask - 1) * 10_000
        funding_times = [_int(aster_price.get("nextFundingTime")), _next_hour_millis()]
        return {
            "symbol": symbol,
            "ticker": symbol.removesuffix("USDT"),
            "name": SYMBOL_NAMES.get(symbol, symbol.removesuffix("USDT")),
            "venue_a_exchange": "BN 钱包/Aster",
            "venue_b_exchange": "Hyperliquid",
            "venue_a_symbol": symbol,
            "venue_b_symbol": coin,
            "hyper_coin": coin,
            "okx_inst_id": coin,
            "funding_rate": spread_annualized / (365 * 3),
            "funding_interval_hours": 8.0,
            "binance_funding_interval_hours": aster_interval,
            "okx_funding_interval_hours": hyper_interval,
            "next_funding_time": min(value for value in funding_times if value),
            "binance_funding_rate": aster_rate,
            "okx_funding_rate": hyper_rate,
            "binance_annualized": aster_annualized,
            "okx_annualized": hyper_annualized,
            "funding_spread_annualized": spread_annualized,
            "annualized_7d": historical_annualized,
            "short_exchange": short_exchange,
            "long_exchange": long_exchange,
            "short_rate": short_rate,
            "long_rate": long_rate,
            "binance_mark_price": _float(aster_price.get("markPrice")),
            "binance_index_price": _float(aster_price.get("indexPrice")),
            "binance_bid": aster_bid,
            "binance_ask": aster_ask,
            "okx_last": _float(hyper_ctx.get("midPx"), _float(hyper_ctx.get("markPx"))),
            "okx_bid": hyper_bid,
            "okx_ask": hyper_ask,
            "short_bid": short_bid,
            "short_ask": short_ask,
            "long_bid": long_bid,
            "long_ask": long_ask,
            "entry_basis_bps": entry_basis_bps,
            "history_rates": history_rates,
            "history": histories["aster"].get(symbol, []),
        }

    def _hyper_bid_ask(self, book: dict[str, Any]) -> tuple[float, float]:
        levels = book.get("levels", [])
        if not isinstance(levels, list) or len(levels) < 2:
            return 0.0, 0.0
        bids = levels[0] if isinstance(levels[0], list) else []
        asks = levels[1] if isinstance(levels[1], list) else []
        best_bid = _float(bids[0].get("px")) if bids and isinstance(bids[0], dict) else 0.0
        best_ask = _float(asks[0].get("px")) if asks and isinstance(asks[0], dict) else 0.0
        return best_bid, best_ask

    def _spread_history_rates(self, symbol: str, aster_interval: float, hyper_interval: float) -> list[float]:
        _, cached = self._history_cache
        aster_rows = cached["aster"].get(symbol, [])
        hyper_rows = cached["hyper"].get(symbol, [])
        hyper_by_hour = {
            _int(row.get("time")) // 3_600_000: _float(row.get("fundingRate"))
            for row in hyper_rows
        }
        rates = []
        for row in aster_rows:
            hour = _int(row.get("fundingTime")) // 3_600_000
            hyper_rate = hyper_by_hour.get(hour)
            if hyper_rate is None:
                continue
            spread_annualized = abs(
                annualize_funding(_float(row.get("fundingRate")), aster_interval)
                - annualize_funding(hyper_rate, hyper_interval)
            )
            rates.append(spread_annualized / (365 * 3))
        return rates[-21:]


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
            record = self._record(
                symbol,
                inst_id,
                binance_price,
                binance_book,
                okx_rate,
                okx_book,
                interval_map.get(symbol, 8.0),
                histories,
            )
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
        binance_interval: float,
        histories: dict[str, dict[str, list[dict[str, Any]]]],
    ) -> dict[str, Any] | None:
        binance_rate = _float(binance_price.get("lastFundingRate"))
        okx_current = _float(okx_rate.get("fundingRate"))
        binance_interval = binance_interval if binance_interval > 0 else 8.0
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
        historical_annualized = self._spread_history_annualized(symbol, binance_interval, okx_interval)
        history_rates = self._spread_history_rates(symbol, binance_interval, okx_interval)
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
            "binance_funding_interval_hours": binance_interval,
            "okx_funding_interval_hours": okx_interval,
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

    def _spread_history_annualized(self, symbol: str, binance_interval: float, okx_interval: float) -> float:
        rates = self._spread_history_rates(symbol, binance_interval, okx_interval)
        return annualize_average(rates, 8.0)

    def _spread_history_rates(self, symbol: str, binance_interval: float, okx_interval: float) -> list[float]:
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
            spread_annualized = abs(
                annualize_funding(_float(row.get("fundingRate")), binance_interval)
                - annualize_funding(okx_rate, okx_interval)
            )
            rates.append(spread_annualized / (365 * 3))
        return rates[-21:]


class DemoProvider:
    BASE = [
        ("CXMTUSDT", 6.70, -0.0078, -0.0036, -18.0),
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
            b_annualized = annualize_funding(binance_rate, 1)
            o_annualized = annualize_funding(okx_rate, 1)
            if b_annualized >= o_annualized:
                short_exchange = "BN 钱包/Aster"
                long_exchange = "Hyperliquid"
                short_bid = b_price - b_spread / 2
                short_ask = b_price + b_spread / 2
                long_bid = o_price - o_spread / 2
                long_ask = o_price + o_spread / 2
                short_rate = binance_rate
                long_rate = okx_rate
            else:
                short_exchange = "Hyperliquid"
                long_exchange = "BN 钱包/Aster"
                short_bid = o_price - o_spread / 2
                short_ask = o_price + o_spread / 2
                long_bid = b_price - b_spread / 2
                long_ask = b_price + b_spread / 2
                short_rate = okx_rate
                long_rate = binance_rate
            spread_annualized = abs(b_annualized - o_annualized)
            history_rates = [
                spread_annualized / (365 * 3) * (0.72 + 0.28 * math.sin(i * 0.7 + index))
                for i in range(21)
            ]
            records.append(
                {
                    "symbol": symbol,
                    "ticker": symbol.removesuffix("USDT"),
                    "name": SYMBOL_NAMES.get(symbol, symbol.removesuffix("USDT")),
                    "venue_a_exchange": "BN 钱包/Aster",
                    "venue_b_exchange": "Hyperliquid",
                    "venue_a_symbol": symbol,
                    "venue_b_symbol": hyper_coin(symbol),
                    "hyper_coin": hyper_coin(symbol),
                    "okx_inst_id": hyper_coin(symbol),
                    "funding_rate": spread_annualized / (365 * 3),
                    "funding_interval_hours": 8.0,
                    "binance_funding_interval_hours": 1.0,
                    "okx_funding_interval_hours": 1.0,
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
