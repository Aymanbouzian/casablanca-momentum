"""Portable HTML/CSV/JSON report and a standalone research chart."""
from dataclasses import asdict
from pathlib import Path
import html
import io
import json
import platform
import shutil
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(x) for x in value]
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def write_json(path, value):
    path.write_text(json.dumps(clean_json(value), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def chart(results, evaluation_start, cfg):
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.8), sharex=True,
                             gridspec_kw={"height_ratios": [2, 1]}, layout="constrained")
    colors = {"tsmom": "#245be0", "tsmom_vm": "#087f70", "passive": "#9a6128"}
    labels = {"tsmom": f"Selected momentum ({cfg.signal_mode})", "tsmom_vm": f"Selected + {cfg.vm_mode} overlay", "passive": "Constrained passive comparison"}
    for name, result in results.items():
        e = result.equity
        growth = (1 + e["return"]).cumprod()
        axes[0].plot(e.index, 100 * growth, label=labels[name], color=colors[name], linewidth=1.6)
        axes[1].plot(e.index, e.exposure * 100, color=colors[name], linewidth=1.2)
    if evaluation_start is not None:
        for ax in axes:
            ax.axvline(evaluation_start, color="#444", linestyle="--", linewidth=1)
        axes[0].text(evaluation_start, axes[0].get_ylim()[0], "  Common comparison begins", fontsize=8, va="bottom")
    axes[0].set_ylabel("Wealth index (start = 100)")
    axes[0].set_title("Supplied equity sample • after modeled fees and slippage", loc="left", fontsize=13)
    axes[0].legend(loc="best", fontsize=8)
    axes[1].set_ylabel("Equity exposure (%)")
    for ax in axes:
        ax.spines[["right", "top"]].set_visible(False)
        ax.grid(alpha=.2)
    axes[1].set_xlabel("Observed trading dates")
    return fig


def display_table(frame, percent_cols=(), money_cols=()):
    f = frame.copy()
    for col in percent_cols:
        if col in f:
            f[col] = f[col].map(lambda v: f"{v:.2%}" if pd.notna(v) else "n/a")
    for col in money_cols:
        if col in f:
            f[col] = f[col].map(lambda v: f"{v:,.2f}" if pd.notna(v) else "n/a")
    return f.to_html(index=False, border=0, classes="data", escape=True, na_rep="n/a")


def save_report(output: Path, cfg, data, schedule, latest, diagnostic, results,
                stats, common_stats, overlay, worksheet, plan_info, sensitivity,
                comparison, comparison_start, training, training_start, training_end):
    output.mkdir(parents=True, exist_ok=True)
    data.audit.to_csv(output / "data_audit.csv", index=False)
    pd.concat(schedule.values(), ignore_index=True).to_csv(output / "signals_monthly.csv", index=False)
    latest.to_csv(output / "signals_latest_completed_month.csv", index=False)
    diagnostic.to_csv(output / "signals_latest_observation_DIAGNOSTIC.csv", index=False)
    stats.to_csv(output / "performance_full_period.csv", index=False)
    common_stats.to_csv(output / "performance_common_evaluation.csv", index=False)
    comparison.to_csv(output / "model_comparison_same_period.csv", index=False)
    training.to_csv(output / "model_comparison_training.csv", index=False)
    sensitivity.to_csv(output / "cost_sensitivity.csv", index=False)
    overlay.history.to_csv(output / "volatility_overlay.csv", index=False)
    worksheet.to_csv(output / "target_holdings_REVIEW_ONLY.csv", index=False)
    write_json(output / "plan_context.json", plan_info)
    for obsolete in {"tsmom", "tsmom_vm", "passive"} - set(results):
        stale = output / obsolete
        if stale.is_dir():
            shutil.rmtree(stale)
    for name, result in results.items():
        folder = output / name
        folder.mkdir(exist_ok=True)
        result.equity.to_csv(folder / "equity_daily.csv")
        result.trades.to_csv(folder / "trades.csv", index=False)
        result.events.to_csv(folder / "events.csv", index=False)
        result.positions.to_csv(folder / "positions_daily.csv", index=False)
        result.targets.to_csv(folder / "targets_monthly.csv", index=False)
    manifest = {"engine_version": "1.2.0", "built_utc": str(pd.Timestamp.now(tz="UTC")),
                "data_cutoff": str(data.calendar[-1].date()), "tickers": sorted(data.frames),
                "configuration": asdict(cfg), "sources": data.sources, "warnings": data.warnings,
                "vm_constant": overlay.constant, "vm_calibration_end": overlay.calibration_end,
                "common_evaluation_start": comparison_start,
                "holdout_start_requested": cfg.holdout_start,
                "training_start": training_start,
                "training_end": training_end,
                "holdout_independence_verified": False,
                "environment": {"python": platform.python_version(), "pandas": pd.__version__,
                                "numpy": np.__version__, "matplotlib": matplotlib.__version__}}
    write_json(output / "run_manifest.json", manifest)
    fig = chart(results, comparison_start, cfg)
    fig.savefig(output / "performance.png", dpi=170)
    buffer = io.StringIO()
    fig.savefig(buffer, format="svg")
    plt.close(fig)
    svg = buffer.getvalue()[buffer.getvalue().find("<svg"):]
    sig_cols = ["ticker", "anchor_date", "window_end_date", "close", "window_return", "window_cash_return",
                "signal_score", "annual_volatility", "traded_fraction", "eligible", "reason", "target_weight"]
    metric_cols = ["strategy", "total_return", "cagr", "annual_volatility", "sharpe_proxy",
                   "max_drawdown", "mean_exposure", "fees_mad", "close_breach_days"]
    metric_cols = [c for c in metric_cols if c in stats]
    percents = ["return_12m", "window_return", "window_cash_return", "signal_score", "annual_volatility", "traded_fraction", "target_weight"]
    metric_perc = ["total_return", "cagr", "annual_volatility", "max_drawdown", "mean_exposure"]
    input_note = ("The supplied sample uses unverified price data." if cfg.return_mode == "price_proxy"
                  else "Corporate actions are included under the user's completeness assertion.")
    benchmark_note = ("The missing cash benchmark is explicitly zero." if not cfg.cash_rates_file
                      else "The supplied MAD cash benchmark is accrued using only rates already available.")
    warnings = "".join(f"<li>{html.escape(w)}</li>" for w in data.warnings)
    eligible_count = int(latest.eligible.sum())
    positive_count = int((latest.eligible & (latest.signal_score > 0)).sum())
    import_audit = output / "import_audit.csv"
    import_note = ""
    if import_audit.exists():
        counts = pd.read_csv(import_audit).status.value_counts()
        import_note = (f"Archive import: {int(counts.get('imported', 0))} new files validated, "
                       f"{int(counts.get('excluded', 0))} excluded; inspect import_audit.csv. ")
    universe_note = (f"{len(data.frames)} distinct price histories; {eligible_count} pass the current history and liquidity checks, "
                     f"and {positive_count} {'has' if positive_count == 1 else 'have'} a positive signal. {import_note}"
                     "Sector labels are provisional; dividends, cash rates, the official calendar, historical membership and the platform QMQ still need verification.")
    signal_label = (f"{cfg.lookback_months}-month excess return" if cfg.signal_mode == "single"
                    else "3/6/12-month excess-return blend" if cfg.signal_mode == "blend_3_6_12"
                    else "12-1 excess return (last month skipped)")
    evaluation_html = (f"<p>All models measured on the same sessions from {comparison_start.date()}. "
                       "This is an exploratory chronological split: these data were already inspected, so it is not an independent holdout.</p>"
                       + display_table(common_stats[[c for c in metric_cols if c in common_stats]], metric_perc, ["fees_mad"]))
    training_html = ("<p>No separate training interval configured.</p>" if training.empty else
                     f"<p>Earlier interval: {training_start.date()} to {training_end.date()}. "
                     "Do not pick a rule using the later comparison rows.</p>" +
                     display_table(training[[c for c in ['variant','total_return','max_drawdown','mean_exposure','fees_mad','status'] if c in training]], metric_perc, ['fees_mad']))
    feasibility = (f"{latest.target_weight.sum():.1%} of NAV allocated against a {cfg.target_invested_weight:.0%} preference; "
                   f"{positive_count} eligible positive {'name' if positive_count == 1 else 'names'}. "
                   "The name and sector caps may prevent reaching the preference.")
    summary = (f"# Casablanca competition — one-page strategy summary\n\n"
               f"Data cutoff: {data.calendar[-1].date()}. {universe_note}\n\n"
               f"Signal: {signal_label}. "
               f"Use completed-month data; own-history excess return above MAD cash proxy triggers a long. "
               f"Size inverse to trailing volatility, then apply 8% per-stock, 30% sector and 95% gross caps. "
               f"Exposure mode: {cfg.exposure_mode}"
               f"{' with breadth scaling (target x share of eligible names with a positive signal)' if cfg.breadth_scaling == 'linear' else ''}. "
               f"Liquidity floor: {cfg.min_average_volume_mad:,.0f} MAD average daily traded value. {feasibility}\n\n"
               f"Orders: following-session close proxy"
               f"{', sliced into daily child orders capped at ' + format(cfg.participation_rate, '.0%') + ' of volume for up to ' + str(cfg.slicing_ttl_sessions) + ' sessions' if cfg.order_slicing else ', all-or-none'}; fee {cfg.fee_rate:.2%} per side plus slippage "
               f"{cfg.slippage_rate:.2%} per side. Ignore drift at or below max({cfg.min_trade_nav_fraction:.0%} NAV, "
               f"{cfg.min_trade_position_fraction:.0%} of held position); full exits and hard-cap corrections remain. "
               f"An observed weight of {cfg.preemptive_trim_weight:.1%} queues a preventive trim for the next session; "
               "a sudden price jump or an unfilled order can still breach 10%. "
               f"Variance overlay: {cfg.vm_mode}.\n\n"
               f"Training: {training_start.date()} to {training_end.date() if training_end is not None else 'not available'} "
               f"in model_comparison_training.csv; later comparison begins {comparison_start.date()} "
               f"in model_comparison_same_period.csv. This is exploratory, not an untouched holdout. "
               f"Costs and results rely on price proxy, incomplete corporate actions and approximate fills. "
               f"Validate dividends, official calendar, MAD cash rates, QMQ and historical membership before trading.\n")
    (output / "ONE_PAGE_SUMMARY.md").write_text(summary, encoding="utf-8")
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Casablanca Momentum Research</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;color:#15243a;background:#f5f7fa;margin:0}}main{{max-width:1200px;margin:auto;padding:30px}}h1{{font-size:32px;margin:4px 0}}h2{{font-size:22px;margin-top:32px}}.muted{{color:#52647a}}.notice{{background:#fff1ce;border-left:5px solid #c48606;padding:16px}}.card{{background:white;border-radius:10px;padding:22px;margin:18px 0;box-shadow:0 2px 10px #15243a0b}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:9px;text-align:left;border-bottom:1px solid #dfe5ec;vertical-align:top}}th{{background:#eaf0f8;white-space:nowrap}}svg{{width:100%;height:auto}}code{{background:#eaf0f8;padding:2px 5px}}a{{color:#245be0}}li{{margin:6px 0}}</style>
<main><p class="muted">RESEARCH PROJECT • MAD • {cfg.return_mode.upper()}</p>
<h1>Casablanca time-series momentum</h1><p>Monthly {html.escape(cfg.signal_mode)} signal; {html.escape(cfg.exposure_mode)} exposure; {html.escape(cfg.vm_mode)} variance overlay.</p>
<div class="notice"><strong>{html.escape(universe_note)}</strong> Actual last observation: <strong>{data.calendar[-1].date()}</strong>. File names do not establish freshness. This report does not submit orders.</div>
<div class="card"><h2>Latest completed-month decision — {max(schedule).date()}</h2>
<p>Buy eligibility requires a positive own-history score and the data/liquidity checks. Targets below are base momentum weights before the optional overlay. {feasibility}</p>
<div class="scroll">{display_table(latest[sig_cols], percents, ['close'])}</div>
<p>Each window's excess return is equity return minus cash benchmark return. The blend averages 3, 6 and 12-month excess returns scaled to 12 months. The 12-1 signal skips the latest month. {input_note} {benchmark_note}</p></div>
<div class="card"><h2>Newer data check — {data.calendar[-1].date()}</h2><p>Diagnostic only; it does not create a mid-month rebalance.</p>
<div class="scroll">{display_table(diagnostic[['ticker','anchor_date','window_end_date','close','signal_score','traded_fraction','reason']], percents, ['close'])}</div></div>
<div class="card"><h2>Backtest</h2>{svg}<p>The passive comparison uses the same supplied stocks, eligibility filters and exposure caps. It is not the MASI index and is not a fully invested market benchmark.</p>
<h3>Full period</h3><div class="scroll">{display_table(stats[metric_cols], metric_perc, ['fees_mad'])}</div>
<h3>Common evaluation period</h3><div class="scroll">{evaluation_html}</div>
<h3>Earlier training period</h3><div class="scroll">{training_html}</div>
<h3>Rule comparison on the same sessions</h3><p>Includes old paper sizing, redistribution, no-band sensitivity, 6-month, blend and 12-1 signals. No winner is selected automatically; this sample was already inspected.</p>
<div class="scroll">{display_table(comparison[[c for c in ['variant','total_return','max_drawdown','mean_exposure','fees_mad','status'] if c in comparison]], metric_perc, ['fees_mad'])}</div>
<p>The original paper's 40% figure is instrument risk scaling, not a portfolio return. The competition caps materially change the paper strategy.</p></div>
<div class="card"><h2>Target holdings worksheet</h2><p>Account basis: {html.escape(plan_info['account'])}. Estimated NAV: {plan_info['estimated_nav_mad']:,.2f} MAD. Target cash weight: {plan_info['target_cash_weight']:.2%}. Unresolved pending orders: {plan_info['pending_orders_count']}.</p>
<div class="scroll">{display_table(worksheet, ['target_weight'], ['reference_price','estimated_change_fees_mad'])}</div><p>{html.escape(plan_info['limitations'])}</p></div>
<div class="card"><h2>Cost sensitivity</h2><p>Preset stress cases; no parameter search or selection of the best case.</p><div class="scroll">{display_table(sensitivity, metric_perc, ['fees_mad'])}</div></div>
<div class="card"><h2>Data audit and limits</h2><div class="scroll">{display_table(data.audit, ['traded_fraction_last_60'], ['mean_volume_last_60_mad'])}</div><ul>{warnings}</ul>
<p>An observed {cfg.preemptive_trim_weight:.1%} stock weight queues a preventive trim toward 8% for the following session. A price jump or an unfilled order can still breach 10%; those days remain in the log. The organizer's published FAQ describes an alert, blocked purchases, and automatic opening-price regularization on the fourth day if the position is not corrected within three days; it does not specify a separate point penalty in that answer. Confirm the current edition's rules. <a href="https://www.e-bourse.ma/comment-jouer">Official FAQ</a>.</p></div>
<div class="card"><h2>Reproduce and extend</h2><p>Add same-format stock CSVs to <code>data/prices</code>, then run <code>python run.py --config config.json</code>. Read <a href="../README.md">README.md</a>, <a href="../docs/METHOD.md">the paper-to-code mapping</a> and <a href="../docs/DATA.md">the data contract</a>. Exact inputs, hashes and settings are recorded in <code>run_manifest.json</code>.</p></div></main></html>"""
    (output / "report.html").write_text(page, encoding="utf-8")
    return manifest
