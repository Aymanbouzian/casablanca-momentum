"""Review worksheets only; never broker orders or assumptions about pending fills."""
import json
import math
from pathlib import Path
import pandas as pd
from .signals import allocate
from .backtest import rebalance_needed


def target_worksheet(data, cfg, table, multiplier=1., account_file: Path | None = None):
    positions = {}
    cash = cfg.initial_capital
    account_label = "hypothetical_all_cash_account"
    pending = []
    account_asof = "not_supplied"
    if account_file:
        account = json.loads(account_file.read_text(encoding="utf-8-sig"))
        required = {"cash_mad", "positions", "pending_orders", "as_of"}
        if not required <= set(account):
            raise ValueError("Account must supply cash_mad, positions, pending_orders and as_of")
        cash = float(account["cash_mad"])
        raw_positions = {str(t).strip().upper(): float(q) for t, q in account["positions"].items()}
        if not math.isfinite(cash) or cash < 0 or any(not math.isfinite(q) or q < 0 or q != int(q)
                                                   for q in raw_positions.values()):
            raise ValueError("Account cash/quantities must be nonnegative; quantities must be integers")
        positions = {t: int(q) for t, q in raw_positions.items()}
        unknown = set(positions) - set(data.frames)
        if unknown:
            raise ValueError(f"Provide price files for existing positions: {sorted(unknown)}")
        pending = account["pending_orders"]
        if not isinstance(pending, list):
            raise ValueError("pending_orders must be a list; unresolved orders block trade differences")
        account_asof = str(account["as_of"])
        pd.Timestamp(account_asof)
        account_label = "user_supplied_account"
    prices = {s: float(f.close.iloc[-1]) for s, f in data.frames.items()}
    nav = cash + sum(q * prices[s] for s, q in positions.items())
    weights = allocate(table, cfg, multiplier=multiplier)
    rows = []
    for s in sorted(data.frames):
        price = prices[s]
        held = positions.get(s, 0)
        goal = math.floor(nav * weights[s] / (price * (1 + cfg.slippage_rate) * (1 + cfg.fee_rate))) if math.isfinite(price) and price > 0 else 0
        change = goal - held
        required = rebalance_needed(held, goal, price, nav, cfg)
        frame = data.frames[s]
        observed_dates = frame.index[frame.observed]
        traded_dates = frame.index[frame.traded]
        rows.append({"ticker": s, "signal_date": table.loc[s, "signal_date"],
                     "price_date": str(observed_dates[-1].date()) if len(observed_dates) else "unknown",
                     "last_trade_date": str(traded_dates[-1].date()) if len(traded_dates) else "unknown",
                     "reference_price": price,
                     "target_weight": weights[s], "held_quantity": held,
                     "target_quantity": goal, "quantity_change": None if pending else change if required else 0,
                     "estimated_change_fees_mad": None if pending else (abs(change) * price * cfg.fee_rate if required else 0.),
                     "reason": table.loc[s, "reason"],
                     "status": "BLOCKED_UNRESOLVED_PENDING_ORDERS" if pending else
                     "ILLUSTRATIVE_REVIEW_ONLY" if required else "WITHIN_NO_TRADE_BAND"})
    info = {"account": account_label, "account_as_of": account_asof, "cash_mad": cash,
            "estimated_nav_mad": nav, "pending_orders_count": len(pending),
            "target_stock_weight": float(weights.sum()), "target_cash_weight": float(1-weights.sum()),
            "limitations": "Historical quotes; verify current holdings, cash, pending fills, QMQ, suspended/reserved status and current prices before manually sizing an order. Share differences are estimates, not executable instructions."}
    return pd.DataFrame(rows), info
