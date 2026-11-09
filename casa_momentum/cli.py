import argparse
from dataclasses import replace
from pathlib import Path
import sys
import pandas as pd
from .config import load_config
from .data import load_dataset
from .signals import make_schedule, snapshot, allocate
from .backtest import simulate, metrics
from .volatility import build_overlay
from .planning import target_worksheet
from .reporting import save_report


def run(config_path: Path, account_file=None, as_of=None, output=None):
    config_path = config_path.resolve()
    root = config_path.parent
    cfg = load_config(config_path)
    if as_of:
        cfg = replace(cfg, as_of=as_of)
    data = load_dataset(root, cfg)
    schedule = make_schedule(data, cfg)
    if not schedule:
        raise ValueError("No complete month-end signal window; provide longer price histories")
    base = simulate(data, cfg, schedule, "tsmom")
    passive = simulate(data, cfg, schedule, "passive")
    ref_cfg = replace(cfg, fee_rate=0., slippage_rate=0.)
    # The optional overlay observes a cost-free version of this strategy.
    reference = simulate(data, ref_cfg, schedule, "tsmom_reference")
    overlay = build_overlay(reference, data.benchmark, schedule, cfg)
    results = {"tsmom": base, "passive": passive}
    if cfg.vm_mode != "off":
        results["tsmom_vm"] = simulate(data, cfg, schedule, "tsmom_vm", overlay.multipliers)
    stats = pd.DataFrame([metrics(r, data, cfg) for r in results.values()])
    # Report every configuration over the SAME sessions. These are exploratory
    # comparisons unless a future data period was set aside in advance.
    variants = {
        "original_12m": replace(cfg, signal_mode="single", lookback_months=12,
                                exposure_mode="paper", min_trade_nav_fraction=0.,
                                min_trade_position_fraction=0., vm_mode="off"),
        "selected_no_band": replace(cfg, min_trade_nav_fraction=0., min_trade_position_fraction=0.),
        "contest_12m": replace(cfg, signal_mode="single", lookback_months=12),
        "contest_12m_breadth": replace(cfg, signal_mode="single", lookback_months=12, breadth_scaling="linear"),
        "contest_6m": replace(cfg, signal_mode="single", lookback_months=6),
        "contest_blend_3_6_12": replace(cfg, signal_mode="blend_3_6_12"),
        "contest_blend_breadth": replace(cfg, signal_mode="blend_3_6_12", breadth_scaling="linear"),
        "contest_12_minus_1": replace(cfg, signal_mode="12-1"),
        "selected": cfg,
    }
    schedules = {label: make_schedule(data, variant) for label, variant in variants.items()}
    complete = {label: d for label, d in schedules.items() if d}
    first_common_signal = max(min(d) for d in complete.values())
    later = data.calendar[data.calendar > first_common_signal]
    training_start = later[0] if len(later) else first_common_signal
    common_start = training_start
    training_end = None
    if cfg.holdout_start:
        common_start = max(common_start, pd.Timestamp(cfg.holdout_start))
        earlier = data.calendar[(data.calendar >= training_start) & (data.calendar < common_start)]
        training_end = earlier[-1] if len(earlier) else None
    comparison_rows = []
    training_rows = []
    for label, variant in variants.items():
        if not schedules[label]:
            comparison_rows.append({"variant": label, "status": "insufficient_signal_history"})
            continue
        result = base if label == "selected" else simulate(data, variant, schedules[label], label)
        record = metrics(result, data, variant, common_start)
        record.update({"variant": label, "signal_mode": variant.signal_mode,
                       "lookback_months": variant.lookback_months,
                       "exposure_mode": variant.exposure_mode, "breadth_scaling": variant.breadth_scaling,
                       "target_invested_weight": variant.target_invested_weight,
                       "no_trade_nav_fraction": variant.min_trade_nav_fraction,
                       "no_trade_position_fraction": variant.min_trade_position_fraction})
        comparison_rows.append(record)
        if training_end is not None:
            train_record = metrics(result, data, variant, training_start, training_end)
            train_record["variant"] = label
            training_rows.append(train_record)
    comparison = pd.DataFrame(comparison_rows)
    training = pd.DataFrame(training_rows)
    common = pd.DataFrame([metrics(r, data, cfg, common_start) for r in results.values()])
    sensitivity_rows = []
    if cfg.run_cost_sensitivity:
        for label, stress_cfg in (("zero_cost", ref_cfg), ("configured_cost", cfg),
                                 ("double_cost", replace(cfg, fee_rate=cfg.fee_rate*2, slippage_rate=cfg.slippage_rate*2))):
            r = reference if label == "zero_cost" else base if label == "configured_cost" else simulate(data, stress_cfg, schedule, "tsmom")
            record = metrics(r, data, stress_cfg)
            record.update({"case": label, "fee_rate": stress_cfg.fee_rate, "slippage_rate": stress_cfg.slippage_rate})
            sensitivity_rows.append(record)
    sensitivity = pd.DataFrame(sensitivity_rows)
    latest = schedule[max(schedule)].copy()
    latest["target_weight"] = allocate(latest, cfg)
    diagnostic = snapshot(data, cfg, data.calendar[-1], monthly=False)
    worksheet, plan_info = target_worksheet(data, cfg, latest, overlay.multipliers[max(schedule)],
                                           Path(account_file).resolve() if account_file else None)
    output_path = Path(output).resolve() if output else root / cfg.output_dir
    manifest = save_report(output_path, cfg, data, schedule, latest, diagnostic,
                           results, stats, common, overlay, worksheet, plan_info, sensitivity,
                           comparison, common_start, training, training_start, training_end)
    print(f"Loaded {len(data.frames)} equities: {', '.join(sorted(data.frames))}")
    print(f"Actual data cutoff: {data.calendar[-1].date()} | mode: {cfg.return_mode}")
    print(f"Latest completed-month signal: {max(schedule).date()}")
    print(f"Eligible at latest month-end: {int(latest.eligible.sum())}; positive: "
          f"{int((latest.eligible & (latest.signal_score > 0)).sum())}")
    print(latest[latest.target_weight > 0][["ticker", "signal_score", "target_weight"]].to_string(index=False))
    print(f"Report: {output_path / 'report.html'}")
    print("No orders submitted. Read data limitations before using target estimates.")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Multi-equity Casablanca time-series momentum research")
    parser.add_argument("--config", default="config.json", help="JSON settings; relative paths resolve beside this file")
    parser.add_argument("--as-of", help="ISO date; ignore later observations")
    parser.add_argument("--output", help="Override output directory")
    parser.add_argument("--account", help="Actual cash, settled holdings and pending orders JSON (optional)")
    args = parser.parse_args()
    try:
        run(Path(args.config), args.account, args.as_of, args.output)
    except (ValueError, FileNotFoundError, KeyError) as error:
        print(f"Input error: {error}", file=sys.stderr)
        sys.exit(2)
