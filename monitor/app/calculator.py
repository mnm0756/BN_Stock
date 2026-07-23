from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from typing import Iterable


SETTLEMENTS_PER_YEAR = 365 * 24


def annualize_funding(rate: float, interval_hours: float) -> float:
    """Return simple annualized funding as a decimal, not a percentage."""
    if interval_hours <= 0:
        return 0.0
    return rate * SETTLEMENTS_PER_YEAR / interval_hours


def annualize_average(rates: Iterable[float], interval_hours: float) -> float:
    values = list(rates)
    if not values:
        return 0.0
    return annualize_funding(sum(values) / len(values), interval_hours)


def spot_fee(notional: float, rate: float, minimum: float) -> float:
    if notional <= 0:
        return 0.0
    raw_fee = max(notional * rate, minimum)
    return ceil(raw_fee * 100) / 100


@dataclass(frozen=True)
class ProjectionInput:
    total_capital: float
    perp_allocation: float
    holding_days: int
    funding_rate: float
    interval_hours: float
    spot_fee_rate: float
    spot_min_fee: float
    perp_fee_rate: float
    slippage_bps: float
    extra_cost_bps: float
    extra_fixed_fee: float
    entry_basis_bps: float


@dataclass(frozen=True)
class Projection:
    hedge_notional: float
    gross_funding: float
    entry_basis_pnl: float
    opening_cost: float
    closing_cost: float
    spot_open_fee: float
    spot_close_fee: float
    perp_open_fee: float
    perp_close_fee: float
    slippage_open: float
    slippage_close: float
    extra_open_fee: float
    extra_close_fee: float
    total_cost: float
    cost_pct_of_hedge: float
    cost_pct_of_capital: float
    net_profit: float
    return_pct: float
    account_annualized: float


def project_profit(item: ProjectionInput) -> Projection:
    capital = max(item.total_capital, 0.0)
    allocation = min(max(item.perp_allocation, 0.0), 1.0)
    perp_budget = capital * allocation
    spot_budget = capital * (1.0 - allocation)
    hedge_notional = min(perp_budget, spot_budget)
    events = max(item.holding_days, 0) * 24 / max(item.interval_hours, 0.001)
    gross_funding = hedge_notional * item.funding_rate * events
    entry_basis_pnl = hedge_notional * item.entry_basis_bps / 10_000

    one_way_spot = spot_fee(hedge_notional, item.spot_fee_rate, item.spot_min_fee)
    one_way_perp = hedge_notional * max(item.perp_fee_rate, 0.0)
    one_way_slippage = hedge_notional * max(item.slippage_bps, 0.0) / 10_000
    one_way_extra = (
        hedge_notional * max(item.extra_cost_bps, 0.0) / 10_000
        + max(item.extra_fixed_fee, 0.0)
    )
    opening_cost = one_way_spot + one_way_perp + one_way_slippage + one_way_extra
    closing_cost = one_way_spot + one_way_perp + one_way_slippage + one_way_extra
    total_cost = opening_cost + closing_cost
    net_profit = gross_funding + entry_basis_pnl - total_cost
    return_pct = net_profit / capital if capital else 0.0
    account_annualized = return_pct * 365 / item.holding_days if item.holding_days else 0.0
    return Projection(
        hedge_notional=hedge_notional,
        gross_funding=gross_funding,
        entry_basis_pnl=entry_basis_pnl,
        opening_cost=opening_cost,
        closing_cost=closing_cost,
        spot_open_fee=one_way_spot,
        spot_close_fee=one_way_spot,
        perp_open_fee=one_way_perp,
        perp_close_fee=one_way_perp,
        slippage_open=one_way_slippage,
        slippage_close=one_way_slippage,
        extra_open_fee=one_way_extra,
        extra_close_fee=one_way_extra,
        total_cost=total_cost,
        cost_pct_of_hedge=total_cost / hedge_notional if hedge_notional else 0.0,
        cost_pct_of_capital=total_cost / capital if capital else 0.0,
        net_profit=net_profit,
        return_pct=return_pct,
        account_annualized=account_annualized,
    )


@dataclass(frozen=True)
class PositionPnl:
    spot_pnl: float
    perp_pnl: float
    funding: float
    fees: float
    exit_cost: float
    net_pnl: float
    net_return: float


def calculate_position_pnl(
    *,
    quantity: float,
    spot_entry: float,
    perp_entry: float,
    spot_bid: float,
    perp_ask: float,
    funding_received: float,
    opening_fees: float,
    spot_fee_rate: float,
    spot_min_fee: float,
    perp_fee_rate: float,
) -> PositionPnl:
    qty = max(quantity, 0.0)
    spot_pnl = (spot_bid - spot_entry) * qty
    perp_pnl = (perp_entry - perp_ask) * qty
    exit_spot_notional = spot_bid * qty
    exit_perp_notional = perp_ask * qty
    exit_cost = spot_fee(exit_spot_notional, spot_fee_rate, spot_min_fee) + (
        exit_perp_notional * perp_fee_rate
    )
    net_pnl = spot_pnl + perp_pnl + funding_received - opening_fees - exit_cost
    invested = spot_entry * qty + max(perp_entry * qty, 0.0)
    return PositionPnl(
        spot_pnl=spot_pnl,
        perp_pnl=perp_pnl,
        funding=funding_received,
        fees=opening_fees,
        exit_cost=exit_cost,
        net_pnl=net_pnl,
        net_return=net_pnl / invested if invested else 0.0,
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
