"""Paper equations with explicit long-only equity/competition constraints."""
import numpy as np
import pandas as pd
from .config import Config
from .data import Dataset, complete_month_ends


def paper_weight(excess_12m: float, volatility: float, count: int, risk_target=.40):
    """Moskowitz et al. (2012), Eq.5 and equal averaging across available assets.

    Signed, possibly levered theoretical weight. Never sent to the competition.
    """
    if count <= 0 or not np.isfinite(volatility) or volatility <= 0:
        return 0.
    return float(np.sign(excess_12m) * risk_target / volatility / count)


def required_months(cfg: Config) -> int:
    return 12 if cfg.signal_mode in {"blend_3_6_12", "12-1"} else cfg.lookback_months


def signal_windows(cfg: Config):
    if cfg.signal_mode == "blend_3_6_12":
        return ((3, 0), (6, 0), (12, 0))
    if cfg.signal_mode == "12-1":
        return ((12, 1),)
    return ((cfg.lookback_months, 0),)


def snapshot(data: Dataset, cfg: Config, asof: pd.Timestamp, monthly=True) -> pd.DataFrame:
    asof = pd.Timestamp(asof)
    if asof not in data.calendar:
        raise ValueError("Signal date must be an observed market session")
    windows = []
    for months, skip in signal_windows(cfg):
        end_limit = ((asof.to_period("M") - skip).end_time.normalize()
                     if monthly else asof - pd.DateOffset(months=skip))
        anchor_limit = ((asof.to_period("M") - months).end_time.normalize()
                        if monthly else asof - pd.DateOffset(months=months))
        prior = data.calendar[data.calendar <= anchor_limit]
        ends = data.calendar[data.calendar <= end_limit]
        windows.append((months, skip, anchor_limit, prior[-1] if len(prior) else None,
                        end_limit, ends[-1] if len(ends) else None))
    anchor = windows[-1][3]
    rows = []
    for ticker, f in data.frames.items():
        row = f.loc[asof]
        reasons = []
        components = []
        component_scores = {}
        for months, skip, anchor_limit, a, end_limit, end in windows:
            if a is None or end is None or pd.isna(f.loc[a, "index"]):
                reasons.append(f"less_than_{months}_months_history")
                continue
            if (anchor_limit-a).days > cfg.max_anchor_gap_days or not f.loc[a, "observed"]:
                reasons.append(f"missing_{months}_month_anchor")
                continue
            if (end_limit-end).days > cfg.max_anchor_gap_days or pd.isna(f.loc[end, "index"]) or not f.loc[end,"observed"]:
                reasons.append(f"missing_{months}_month_endpoint")
                continue
            ret = f.loc[end, "index"] / f.loc[a, "index"] - 1
            cashret = data.benchmark.loc[end] / data.benchmark.loc[a] - 1
            components.append((months, ret, cashret, ret - cashret))
            suffix = f"_{months}m" + ("_skip1" if skip else "")
            component_scores[f"return{suffix}"] = ret
            component_scores[f"cash_return{suffix}"] = cashret
            component_scores[f"excess_return{suffix}"] = ret - cashret
        excess = (float(np.mean([12 * x[3] / x[0] for x in components]))
                  if len(components) == len(windows) else np.nan)
        if np.isfinite(excess) and abs(excess) < 1e-10:
            excess = 0.  # Remove floating point noise around flat total returns.
        only = components[0] if len(components) == 1 else None
        ret = only[1] if only else np.nan
        cashret = only[2] if only else np.nan
        meta = data.metadata.loc[ticker]
        if pd.notna(meta.active_from) and asof < meta.active_from:
            reasons.append("not_yet_in_universe")
        if pd.notna(meta.active_to) and asof > meta.active_to:
            reasons.append("no_longer_in_universe")
        if not row.observed:
            reasons.append("missing_current_observation")
        if pd.isna(row.volatility) or row.volatility <= 0:
            reasons.append("insufficient_or_zero_volatility")
        if pd.isna(row.coverage) or row.coverage < cfg.min_history_coverage:
            reasons.append("incomplete_history")
        if pd.isna(row.liquidity_fraction) or row.liquidity_fraction < cfg.min_traded_fraction:
            reasons.append("too_few_trading_days")
        if pd.isna(row.average_volume_mad) or row.average_volume_mad < cfg.min_average_volume_mad:
            reasons.append("low_traded_value")
        if pd.isna(row.stale_sessions) or row.stale_sessions > cfg.max_stale_sessions:
            reasons.append("stale_price")
        # An unverified discontinuity in the signal window blocks the price-proxy signal.
        window = f.loc[(f.index > (anchor if anchor is not None else f.index[0])) & (f.index <= asof)]
        if cfg.return_mode == "price_proxy" and window.large_move.any():
            reasons.append("large_price_move_needs_corporate_action_review")
        eligible = not reasons and np.isfinite(excess)
        rows.append({"signal_date": asof.date().isoformat(), "ticker": ticker,
                     "name": str(f.loc[:asof, "name"].dropna().iloc[-1]) if f.loc[:asof, "name"].notna().any() else ticker,
                     "sector": meta.sector,
                     "anchor_date": anchor.date().isoformat() if anchor is not None else "",
                     "anchor_close": f.loc[anchor, "close"] if anchor is not None else np.nan,
                     "window_end_date": windows[-1][5].date().isoformat() if windows[-1][5] is not None else "",
                     "close": row.close,
                     "window_return": ret, "window_cash_return": cashret,
                     "signal_score": excess, "signal_mode": cfg.signal_mode,
                     "signal": int(np.sign(excess)) if np.isfinite(excess) else 0,
                     "annual_volatility": row.volatility, "traded_fraction": row.liquidity_fraction,
                     "average_volume_mad": row.average_volume_mad,
                     "stale_sessions": row.stale_sessions, "eligible": bool(eligible),
                     "reason": ";".join(reasons) if reasons else ("positive_momentum" if excess > 0 else "nonpositive_momentum_cash"),
                     "return_mode": cfg.return_mode, **component_scores})
    result = pd.DataFrame(rows).set_index("ticker", drop=False)
    # Rank only by information available on the decision date, before looking
    # at momentum signs. Previously delisted names can enter earlier months.
    liquid = result[result.eligible].sort_index().sort_values("average_volume_mad", ascending=False, kind="stable")
    outside = liquid.index[cfg.max_liquid_universe_size:]
    result.loc[outside, "eligible"] = False
    result.loc[outside, "reason"] = "outside_top_liquid_universe"
    return result


def allocate(table: pd.DataFrame, cfg: Config, mode="tsmom", multiplier=1.) -> pd.Series:
    """Map momentum to long-only weights, then apply all name/sector/gross caps.

    Redistribute mode fills toward a configurable gross target, stopping whenever
    caps make further investment impossible. No positive names means all cash.
    """
    if mode not in {"tsmom", "passive"}:
        raise ValueError("Unknown allocation mode")
    weights = pd.Series(0., index=table.index)
    if multiplier < 0 or not np.isfinite(multiplier):
        raise ValueError("Invalid overlay multiplier")
    eligible = table[table.eligible]
    n = len(eligible)
    if not n:
        return weights
    score_col = "signal_score" if "signal_score" in eligible else "excess_return_12m"
    if mode == "tsmom" and cfg.exposure_mode == "redistribute":
        positives = eligible[eligible[score_col] > 0]
        if positives.empty:
            return weights
        risk = 1. / positives.annual_volatility.clip(lower=cfg.vol_floor)
        risk = risk.replace([np.inf, -np.inf], np.nan).fillna(0.)
        sectors = table.sector.astype(str)
        # Progressive water filling: distribute surplus only to names and sectors
        # with capacity, retaining the inverse-volatility preference where possible.
        desired = min(cfg.max_gross_weight, cfg.target_invested_weight * float(multiplier))
        if cfg.breadth_scaling == "linear":
            # Scale the invested target by the share of eligible names with a positive
            # signal, so exposure falls when few stocks trend up (market-level timing).
            desired *= len(positives) / n
        room = pd.Series(cfg.target_max_stock_weight, index=positives.index)
        for _ in range(100):
            remaining = desired - weights.sum()
            if remaining < 1e-10:
                break
            for sector in sectors.loc[positives.index].unique():
                members = positives.index[sectors.loc[positives.index] == sector]
                available = max(0., cfg.max_sector_weight - weights.loc[members].sum())
                if available < 1e-10:
                    room.loc[members] = 0.
            active = room[room > 1e-10].index
            if len(active) == 0 or risk.loc[active].sum() <= 0:
                break
            proposal = remaining * risk.loc[active] / risk.loc[active].sum()
            proposal = proposal.clip(upper=room.loc[active])
            for sector in sectors.loc[active].unique():
                members = active[sectors.loc[active] == sector]
                sector_room = max(0., cfg.max_sector_weight - weights.loc[members].sum())
                if proposal.loc[members].sum() > sector_room:
                    proposal.loc[members] *= sector_room / proposal.loc[members].sum()
            if proposal.sum() < 1e-10:
                break
            weights.loc[active] += proposal
            room.loc[active] -= proposal
        return weights
    for ticker, row in eligible.iterrows():
        if mode == "passive":
            weight = 1 / n
        else:
            weight = max(0., paper_weight(row[score_col], max(row.annual_volatility, cfg.vol_floor), n, cfg.instrument_risk_target))
            if cfg.vm_mode == "target_vol":
                weight *= multiplier
        weights[ticker] = min(weight, cfg.target_max_stock_weight)
    for _, members in table.groupby("sector"):
        gross = weights.loc[members.index].sum()
        if gross > cfg.max_sector_weight:
            weights.loc[members.index] *= cfg.max_sector_weight / gross
    gross = weights.sum()
    if gross > cfg.max_gross_weight:
        weights *= cfg.max_gross_weight / gross
    return weights * min(multiplier, 1.) if mode == "tsmom" and cfg.vm_mode == "legacy" else weights


def make_schedule(data: Dataset, cfg: Config):
    tables = {}
    for date in complete_month_ends(data.calendar):
        # Require the longest endpoint history; avoid startup-cash distortion.
        anchor_limit = (date.to_period("M") - required_months(cfg)).end_time.normalize()
        if anchor_limit < data.calendar[0]:
            continue
        tables[date] = snapshot(data, cfg, date, monthly=True)
    return tables
