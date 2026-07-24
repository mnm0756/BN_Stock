from app.db import DEFAULT_SETTINGS
from app.service import MonitorService


class DummyDb:
    def get_settings(self) -> dict:
        return DEFAULT_SETTINGS


def test_projection_uses_current_cross_exchange_spread_not_historical_average() -> None:
    service = MonitorService(DummyDb())
    settings = {
        **DEFAULT_SETTINGS,
        "total_capital": 1000,
        "leverage": 1,
        "holding_days": 30,
        "binance_maker_fee": 0,
        "okx_maker_fee": 0,
        "slippage_bps": 0,
        "extra_cost_bps": 0,
        "extra_fixed_fee": 0,
    }
    [item] = service._build_opportunities(
        [
            {
                "symbol": "BTCUSDT",
                "ticker": "BTC",
                "name": "Bitcoin",
                "funding_rate": 0.0,
                "funding_interval_hours": 8.0,
                "next_funding_time": 0,
                "binance_funding_rate": 0.001,
                "okx_funding_rate": 0.001,
                "binance_annualized": 1.095,
                "okx_annualized": 1.095,
                "funding_spread_annualized": 0.0,
                "annualized_7d": 1.0,
                "short_exchange": "Binance",
                "long_exchange": "OKX",
                "short_rate": 0.001,
                "long_rate": 0.001,
                "binance_mark_price": 100.0,
                "binance_index_price": 100.0,
                "binance_bid": 100.0,
                "binance_ask": 100.1,
                "okx_last": 100.0,
                "okx_bid": 99.9,
                "okx_ask": 100.0,
                "short_bid": 100.0,
                "short_ask": 100.1,
                "long_bid": 99.9,
                "long_ask": 100.0,
                "entry_basis_bps": 0.0,
                "history_rates": [0.001] * 21,
                "history": [],
            }
        ],
        settings,
    )

    assert item["annualized_7d"] > 0
    assert item["annualized_current"] == 0
    assert item["projection"]["gross_funding"] == 0
    assert item["projection"]["net_profit"] == 0
    assert "无费差" in item["risk_flags"]
