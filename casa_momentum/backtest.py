"""Integer-share, cash-conserving daily simulation with orders decided BEFORE fills."""
from dataclasses import dataclass
import math
import numpy as np
import pandas as pd
from .config import Config
from .data import Dataset
from .signals import allocate


@dataclass
class BacktestResult:
    name: str
    equity: pd.DataFrame
    trades: pd.DataFrame
    events: pd.DataFrame
    positions: pd.DataFrame
    targets: pd.DataFrame


TRADE_COLS = ["date", "decision_date", "ticker", "side", "quantity", "reference_close",
              "fill_price", "notional", "fee", "slippage_cost", "cash_after"]
EVENT_COLS = ["date", "ticker", "event", "detail"]


def rebalance_needed(held: int, goal: int, price: float, nav: float, cfg: Config) -> bool:
    """Ignore small weight drift; always allow full exits and initial meaningful buys.

    A gap must exceed the larger of the NAV and current-position thresholds.
    Hard-cap trims are separately forced by the simulator.
    """
    if goal == held:
        return False
    if goal == 0:
        return True
    if goal < held and held * price / nav >= cfg.preemptive_trim_weight:
        return True
    gap = abs(goal - held) * price
    band = max(cfg.min_trade_nav_fraction * nav,
               cfg.min_trade_position_fraction * held * price)
    return gap > band + 1e-8


def simulate(data: Dataset, cfg: Config, schedule: dict, name="tsmom", multipliers=None):
    mode = "passive" if name.startswith("passive") else "tsmom"
    symbols = sorted(data.frames)
    cash = float(cfg.initial_capital)
    shares = {s: 0 for s in symbols}
    # Outstanding quantities are fixed after a decision, not resized using tomorrow's price.
    pending = {}
    curve, trades, events, holdings, targets = [], [], [], [], []
    first_signal = min(schedule) if schedule else None
    if first_signal is None:
        raise ValueError("No complete signal window. Supply longer price histories.")
    calendar = data.calendar[data.calendar >= first_signal]
    previous_nav = cash
    previous_date = None

    def log(date, symbol, kind, detail):
        events.append({"date": date, "ticker": symbol, "event": kind, "detail": detail})

    for i, date in enumerate(calendar):
        fees_today = slippage_today = turnover_today = dividends_today = 0.
        if previous_date is not None:
            cash *= (1 + cfg.cash_annual_yield) ** ((date - previous_date).days / 365)
        # Ex-date split adjustment precedes dividend entitlement and execution.
        for s in symbols:
            row = data.frames[s].loc[date]
            split = float(row.split)
            if split != 1:
                exact = shares[s] * split
                whole = math.floor(exact + 1e-10)
                # Explicit approximation: fractional shares are paid at ex-date closing price.
                cash += (exact - whole) * float(row.close)
                shares[s] = whole
                if s in pending:
                    pending[s]["goal"] = math.floor(pending[s]["goal"] * split + 1e-10)
                log(date, s, "split", f"ratio={split}; fractional cash-in-lieu at close if needed")
            dividend = shares[s] * float(row.dividend)
            cash += dividend
            dividends_today += dividend

        # Both sells and buys are filled at this day's close +/- slippage, using only
        # quantities decided on an EARLIER session. Same-day total volume is an ex-post
        # liquidity constraint, not information used to construct the signal/order.
        for side in ("SELL", "BUY"):
            for s in sorted(list(pending)):
                order = pending.get(s)
                if not order or date <= order["decision_date"]:
                    continue
                difference = order["goal"] - shares[s]
                if not difference:
                    del pending[s]
                    continue
                if (difference < 0) != (side == "SELL"):
                    continue
                row = data.frames[s].loc[date]
                if not row.traded or not row.observed or pd.isna(row.close):
                    log(date, s, "no_fill", "No observed trades; carried closing price is not executable")
                    continue
                volume_cap = math.floor(float(row.shares) * cfg.participation_rate)
                qty = min(abs(difference), volume_cap)
                close = float(row.close)
                fill = close * (1 + cfg.slippage_rate if side == "BUY" else 1 - cfg.slippage_rate)
                if side == "BUY":
                    # Conservative affordability; target was fixed at prior close.
                    qty = min(qty, max(0, math.floor((cash + 1e-8) / (fill * (1 + cfg.fee_rate)))))
                    # A fill-time hard cap limits buying after price jumps; never increases
                    # the precommitted quantity. Use current marks for the cash constraint.
                    nav_now = cash + sum(shares[t] * float(data.frames[t].loc[date, "close"])
                                        for t in symbols if shares[t])
                    h = cfg.hard_max_stock_weight
                    # post-fee NAV = nav - qty*(slippage + fee per share)
                    headroom = h * nav_now - shares[s] * close
                    cost_loss = fill - close + fill * cfg.fee_rate
                    cap_qty = max(0, math.floor((headroom + 1e-8) / (close + h * cost_loss)))
                    qty = min(qty, cap_qty)
                else:
                    qty = min(qty, shares[s])
                # Order slicing: each day's fill is a separate all-or-none child order,
                # sized to that day's volume/cash/position room; the remainder stays pending.
                if not (cfg.allow_partial_fills or cfg.order_slicing) and qty < abs(difference):
                    log(date, s, "no_fill_all_or_none", "Full precommitted quantity exceeds volume, cash or position cap")
                    continue
                if qty <= 0:
                    log(date, s, "no_fill", "Participation, cash or position cap prevents a whole-share fill")
                    continue
                notional = qty * fill
                fee = notional * cfg.fee_rate
                if side == "BUY":
                    cash -= notional + fee
                    shares[s] += qty
                else:
                    cash += notional - fee
                    shares[s] -= qty
                if cash < -1e-6 or shares[s] < 0:
                    raise AssertionError("Cash/long-only conservation failed")
                cash = max(0., cash)
                fees_today += fee
                slippage_today += qty * abs(fill - close)
                turnover_today += qty * close
                trades.append({"date": date, "decision_date": order["decision_date"], "ticker": s,
                               "side": side, "quantity": qty, "reference_close": close,
                               "fill_price": fill, "notional": notional, "fee": fee,
                               "slippage_cost": qty * abs(fill - close), "cash_after": cash})
                if shares[s] == order["goal"]:
                    del pending[s]
        for s, order in list(pending.items()):
            if i - order["created_i"] >= (cfg.slicing_ttl_sessions if cfg.order_slicing else cfg.order_ttl_sessions):
                log(date, s, "order_expired", f"Unfilled quantity={order['goal']-shares[s]}")
                del pending[s]

        values = {s: shares[s] * float(data.frames[s].loc[date, "close"]) if shares[s] else 0.
                  for s in symbols}
        nav = cash + sum(values.values())
        current_weights = {s: values[s] / nav for s in symbols}
        breach = False
        for s in symbols:
            if shares[s] and (not data.frames[s].loc[date, "observed"] or
                              data.frames[s].loc[date, "stale_sessions"] > cfg.max_stale_sessions):
                log(date, s, "stale_valuation", "Position carried at last price; liquidation may be impossible")
            if current_weights[s] > cfg.hard_max_stock_weight + 1e-10:
                breach = True
                log(date, s, "close_weight_breach", f"weight={current_weights[s]:.8f}; trim queued for NEXT session")
        daily_return = nav / previous_nav - 1
        curve.append({"date": date, "nav": nav, "cash": cash, "exposure": 1 - cash / nav,
                      "return": daily_return, "fees": fees_today, "slippage": slippage_today,
                      "traded_notional": turnover_today, "dividends": dividends_today,
                      "max_stock_weight": max(current_weights.values(), default=0.),
                      "weight_breach": breach})
        for s in symbols:
            holdings.append({"date": date, "ticker": s, "shares": shares[s], "value": values[s],
                             "weight": current_weights[s]})

        # Only now, after marking at close, decide orders for following sessions.
        if date in schedule:
            for s in pending:
                log(date, s, "order_replaced", "New monthly decision replaces residual quantity")
            pending = {}
            multiplier = float(multipliers.get(date, 1.)) if multipliers is not None else 1.
            weight = allocate(schedule[date], cfg, mode, multiplier)
            for s in symbols:
                price = data.frames[s].loc[date, "close"]
                # Fees/slippage receive their own cash reserve by reducing share quantities.
                denom = price * (1 + cfg.slippage_rate) * (1 + cfg.fee_rate)
                goal = math.floor(nav * weight[s] / denom) if pd.notna(denom) and denom > 0 else 0
                targets.append({"date": date, "ticker": s, "target_weight": weight[s],
                                "target_shares": goal, "reference_close": price, "vm_multiplier": multiplier})
                if rebalance_needed(shares[s], goal, float(price), nav, cfg):
                    pending[s] = {"goal": goal, "decision_date": date, "created_i": i}
                elif goal != shares[s]:
                    log(date, s, "within_no_trade_band",
                        f"held={shares[s]}; target={goal}; no order")
        # Use every observed close to queue preventive sells BEFORE the 10% hard
        # limit is reached. It remains possible to jump across the buffer in one
        # session or to fail a sell under the all-or-none liquidity constraint.
        for s in symbols:
            if current_weights[s] >= cfg.preemptive_trim_weight - 1e-10:
                goal = math.floor(nav * cfg.target_max_stock_weight / float(data.frames[s].loc[date, "close"]))
                if s in pending:
                    goal = min(goal, pending[s]["goal"])
                if goal < shares[s]:
                    if s not in pending or pending[s]["goal"] != goal:
                        log(date, s, "preemptive_trim_queued", f"weight={current_weights[s]:.8f}; next-session target={goal}")
                        pending[s] = {"goal": goal, "decision_date": date, "created_i": i}
        previous_nav, previous_date = nav, date
    for s, order in pending.items():
        log(calendar[-1], s, "pending_at_data_end", f"Remaining quantity={order['goal']-shares[s]}")
    return BacktestResult(name, pd.DataFrame(curve).set_index("date"),
                          pd.DataFrame(trades, columns=TRADE_COLS),
                          pd.DataFrame(events, columns=EVENT_COLS),
                          pd.DataFrame(holdings), pd.DataFrame(targets))


def metrics(result: BacktestResult, data: Dataset, cfg: Config, start=None, end=None):
    equity = result.equity
    selected = equity if start is None else equity[equity.index >= start]
    if end is not None:
        selected = selected[selected.index <= end]
    if len(selected) < 2:
        return {"strategy": result.name, "sessions": len(selected), "status": "insufficient_period"}
    # Include first day's trading cost in a common-window evaluation.
    r = selected["return"]
    growth = (1 + r).cumprod()
    previous = equity.index[equity.index < selected.index[0]]
    origin_date = previous[-1] if len(previous) else selected.index[0]
    years = max((selected.index[-1] - origin_date).days / 365.25, 1 / 365.25)
    rf = data.benchmark.pct_change(fill_method=None).fillna(0).reindex(selected.index)
    if selected.index[0] == equity.index[0]:
        rf.iloc[0] = 0.  # initialization close is not an invested return interval
    excess = r - rf
    vol = r.std(ddof=1) * np.sqrt(cfg.annualization)
    sharpe = excess.mean() / excess.std(ddof=1) * np.sqrt(cfg.annualization) if excess.std(ddof=1) > 1e-12 else np.nan
    peak = growth.cummax().clip(lower=1.)
    return {"strategy": result.name, "start": str(selected.index[0].date()),
            "end": str(selected.index[-1].date()), "sessions": len(selected),
            "total_return": growth.iloc[-1] - 1, "cagr": growth.iloc[-1] ** (1 / years) - 1,
            "annual_volatility": vol, "sharpe_proxy": sharpe,
            "max_drawdown": (growth / peak - 1).min(), "mean_exposure": selected.exposure.mean(),
            "fees_mad": selected.fees.sum(), "slippage_mad": selected.slippage.sum(),
            "one_way_turnover": (selected.traded_notional / selected.nav).sum(),
            "close_breach_days": int(selected.weight_breach.sum()),
            "status": "research_price_proxy" if cfg.return_mode == "price_proxy" else "research_total_return"}
