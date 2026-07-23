from app.calculator import (
    ProjectionInput,
    annualize_funding,
    calculate_position_pnl,
    project_profit,
    spot_fee,
)


def test_annualizes_eight_hour_rate() -> None:
    assert annualize_funding(0.001, 8) == 1.095


def test_spot_minimum_fee() -> None:
    assert spot_fee(100, 0.001, 0.35) == 0.35
    assert spot_fee(1000, 0.001, 0.35) == 1.0
    assert spot_fee(451, 0.001, 0.35) == 0.46


def test_projection_uses_smaller_leg_as_hedge_notional() -> None:
    result = project_profit(
        ProjectionInput(
            total_capital=1000,
            perp_allocation=0.3,
            holding_days=30,
            funding_rate=0.001,
            interval_hours=8,
            spot_fee_rate=0.001,
            spot_min_fee=0.35,
            perp_fee_rate=0,
            slippage_bps=0,
            extra_cost_bps=0,
            extra_fixed_fee=0,
            entry_basis_bps=0,
        )
    )
    assert result.hedge_notional == 300
    assert round(result.gross_funding, 2) == 27.0
    assert result.net_profit < result.gross_funding


def test_projection_exposes_round_trip_cost_breakdown() -> None:
    result = project_profit(
        ProjectionInput(
            total_capital=1000,
            perp_allocation=0.5,
            holding_days=30,
            funding_rate=0,
            interval_hours=8,
            spot_fee_rate=0.001,
            spot_min_fee=0.35,
            perp_fee_rate=0.0004,
            slippage_bps=2,
            extra_cost_bps=0,
            extra_fixed_fee=0,
            entry_basis_bps=0,
        )
    )
    assert result.hedge_notional == 500
    assert result.spot_open_fee == 0.5
    assert result.spot_close_fee == 0.5
    assert result.perp_open_fee == 0.2
    assert result.perp_close_fee == 0.2
    assert result.slippage_open == 0.1
    assert result.slippage_close == 0.1
    assert round(result.total_cost, 4) == 1.6
    assert round(result.cost_pct_of_hedge, 6) == 0.0032
    assert round(result.cost_pct_of_capital, 6) == 0.0016


def test_projection_includes_extra_cost_reserve() -> None:
    result = project_profit(
        ProjectionInput(
            total_capital=1000,
            perp_allocation=0.5,
            holding_days=30,
            funding_rate=0,
            interval_hours=8,
            spot_fee_rate=0,
            spot_min_fee=0,
            perp_fee_rate=0,
            slippage_bps=0,
            extra_cost_bps=1,
            extra_fixed_fee=0.25,
            entry_basis_bps=0,
        )
    )
    assert result.extra_open_fee == 0.3
    assert result.extra_close_fee == 0.3
    assert result.total_cost == 0.6
    assert result.net_profit == -0.6


def test_position_pnl_combines_both_legs_and_funding() -> None:
    result = calculate_position_pnl(
        quantity=10,
        spot_entry=100,
        perp_entry=101,
        spot_bid=110,
        perp_ask=111,
        funding_received=8,
        opening_fees=2,
        spot_fee_rate=0.001,
        spot_min_fee=0.35,
        perp_fee_rate=0,
    )
    assert result.spot_pnl == 100
    assert result.perp_pnl == -100
    assert round(result.net_pnl, 2) == 4.89
