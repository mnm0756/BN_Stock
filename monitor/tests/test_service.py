from app.db import DEFAULT_SETTINGS
from app.service import MonitorService


class DummyDb:
    def get_settings(self) -> dict:
        return DEFAULT_SETTINGS


def test_projection_uses_current_funding_not_historical_average() -> None:
    service = MonitorService(DummyDb())
    settings = {
        **DEFAULT_SETTINGS,
        "total_capital": 1000,
        "perp_allocation": 0.5,
        "holding_days": 30,
        "spot_fee_rate": 0,
        "spot_min_fee": 0,
        "perp_maker_fee": 0,
        "slippage_bps": 0,
        "extra_cost_bps": 0,
        "extra_fixed_fee": 0,
    }
    [item] = service._build_opportunities(
        [
            {
                "symbol": "MUUSDT",
                "ticker": "MU",
                "name": "Micron",
                "mark_price": 100.0,
                "index_price": 100.0,
                "funding_rate": 0.0,
                "funding_interval_hours": 8.0,
                "next_funding_time": 0,
                "perp_bid": 100.0,
                "perp_ask": 100.0,
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
    assert "当前为0" in item["risk_flags"]
