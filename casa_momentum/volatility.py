"""Moreira-Muir Eq.1: inverse LAST completed month's portfolio variance."""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from .config import Config


@dataclass
class VolatilityOverlay:
    multipliers: dict
    history: pd.DataFrame
    constant: float | None
    calibration_end: pd.Timestamp | None
    evaluation_start: pd.Timestamp | None


def managed_factor(future_excess_return, previous_month_variance, constant):
    """Unconstrained algebraic equation from the paper; separate from competition caps."""
    if previous_month_variance <= 0:
        raise ValueError("Variance must be strictly positive")
    return constant / previous_month_variance * future_excess_return


def build_overlay(reference, benchmark: pd.Series, schedule: dict, cfg: Config):
    if cfg.vm_mode == "off":
        history = pd.DataFrame([{"signal_date": d, "reference_variance": np.nan,
                                 "calibration_pairs_available": 0, "constant_c": np.nan,
                                 "multiplier": 1., "status": "disabled"} for d in sorted(schedule)])
        return VolatilityOverlay({d: 1. for d in schedule}, history, None, None, None)
    r = reference.equity["return"]
    rf = benchmark.pct_change(fill_method=None).fillna(0).reindex(r.index)
    x = r - rf
    monthly_rows = []
    first_period = r.index[0].to_period("M")
    last_period = r.index[-1].to_period("M")
    for period, values in x.groupby(x.index.to_period("M")):
        # First period starts at the initialization close; last can be incomplete.
        if period <= first_period or period >= last_period:
            continue
        dates = values.index
        realized_variance = float(((values - values.mean()) ** 2).sum())
        f = float((1 + r.loc[dates]).prod() - (1 + rf.loc[dates]).prod())
        monthly_rows.append({"period": period, "date": dates[-1], "excess_return": f,
                             "realized_variance": realized_variance, "sessions": len(values)})
    history, multipliers = [], {}
    c = calibration_end = evaluation_start = None
    monthly = pd.DataFrame(monthly_rows)
    if not monthly.empty:
        monthly = monthly.set_index("period")
    for date in sorted(schedule):
        period = date.to_period("M")
        past = monthly[monthly.date <= date].copy() if not monthly.empty else pd.DataFrame()
        variance = float(past.loc[period, "realized_variance"]) if not past.empty and period in past.index else np.nan
        if cfg.vm_mode == "target_vol":
            sessions = int(past.loc[period, "sessions"]) if not past.empty and period in past.index else 0
            if not np.isfinite(variance) or sessions < 10:
                scale, status = 1., "no_complete_reference_month"
            elif variance <= cfg.vm_variance_floor:
                scale, status = 1., "flat_reference_keep_base"
            else:
                realized_vol = np.sqrt(variance / (sessions - 1) * cfg.annualization)
                scale = min(cfg.vm_max_multiplier, cfg.vm_target_annual_vol / realized_vol)
                status = "target_vol_active"
                if evaluation_start is None:
                    after = r.index[r.index > date]
                    evaluation_start = after[0] if len(after) else None
            multipliers[date] = scale
            history.append({"signal_date": date, "reference_variance": variance,
                            "calibration_pairs_available": 0, "constant_c": np.nan,
                            "multiplier": scale, "status": status})
            continue
        pairs = []
        if not past.empty:
            for p, row in past.iterrows():
                if p - 1 in past.index:
                    prior = past.loc[p - 1, "realized_variance"]
                    if prior > cfg.vm_variance_floor and row.sessions >= 10:
                        pairs.append((row.excess_return, row.excess_return / prior))
        if c is None and len(pairs) >= cfg.vm_calibration_months:
            # Freeze the FIRST calibration block. No full-sample normalization or refit.
            train = np.asarray(pairs[:cfg.vm_calibration_months])
            raw_sd, scaled_sd = train[:, 0].std(ddof=1), train[:, 1].std(ddof=1)
            if raw_sd > 1e-12 and scaled_sd > 1e-12:
                c = float(raw_sd / scaled_sd)
                calibration_end = date
                after = r.index[r.index > date]
                evaluation_start = after[0] if len(after) else None
        if c is None:
            scale, status = 1., "calibrating_use_base_exposure"
        elif not np.isfinite(variance):
            scale, status = 0., "missing_previous_month_variance_cash"
        elif variance <= cfg.vm_variance_floor:
            # Zero-variance cash references need no artificial leverage. Cap at one.
            scale, status = min(1., cfg.vm_max_multiplier), "variance_floor_cap"
        else:
            scale, status = min(1., cfg.vm_max_multiplier, c / variance), "active"
        multipliers[date] = scale
        history.append({"signal_date": date, "reference_variance": variance,
                        "calibration_pairs_available": len(pairs), "constant_c": c,
                        "multiplier": scale, "status": status})
    return VolatilityOverlay(multipliers, pd.DataFrame(history), c, calibration_end, evaluation_start)
