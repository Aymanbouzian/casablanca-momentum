"""Validate every input, preserve raw execution prices, and align without backfilling."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import re
import numpy as np
import pandas as pd
from .config import Config

EXPORT_COLUMNS = {
    "Session": "date", "Ticker": "ticker", "Instrument": "name",
    "Opening": "open", "Last Price": "close", "Volume (MAD)": "volume_mad",
    "Traded Shares": "shares", "High": "high", "Low": "low",
    # French-language export of the same file
    "Séance": "date", "Instrument ": "name", "Ouverture": "open", "Dernier Cours": "close",
    "Plus Haut": "high", "Plus Bas": "low", "Titres Échangés": "shares",
}


@dataclass
class Dataset:
    frames: dict[str, pd.DataFrame]
    calendar: pd.DatetimeIndex
    metadata: pd.DataFrame
    benchmark: pd.Series
    audit: pd.DataFrame
    sources: list[dict]
    warnings: list[str]


def numeric(series):
    return pd.to_numeric(series.astype(str).str.replace("\u202f", "", regex=False)
                         .str.replace("\xa0", "", regex=False)
                         .str.replace(" ", "", regex=False)
                         .str.replace(",", ".", regex=False), errors="coerce")


def read_price_file(path: Path) -> pd.DataFrame:
    f = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig",
                    na_values=["-", "", "NA", "null"])
    f.columns = f.columns.str.strip()
    exchange_export = "Session" in f or "Séance" in f
    f = f.rename(columns=EXPORT_COLUMNS)
    required = {"date", "ticker", "close", "shares", "volume_mad"}
    if not required <= set(f.columns):
        raise ValueError(f"{path.name}: missing {sorted(required-set(f.columns))}")
    f["date"] = pd.to_datetime(f.date, format="%d/%m/%Y" if exchange_export else "%Y-%m-%d", errors="raise")
    if f.ticker.isna().any():
        raise ValueError(f"{path.name}: missing ticker")
    f["ticker"] = f.ticker.astype(str).str.strip().str.upper()
    if (f.ticker == "").any():
        raise ValueError(f"{path.name}: empty ticker")
    named = re.match(r"^Cours_([A-Za-z0-9]{3})(?:\s|_|\.)", path.name)
    if named is not None and set(f.ticker) != {named.group(1).upper()}:
        raise ValueError(f"{path.name}: filename says {named.group(1).upper()}, contents say {sorted(f.ticker.unique())}")
    for col in ("close", "shares", "volume_mad", "open", "high", "low", "dividend", "split"):
        if col not in f:
            f[col] = {"dividend": 0., "split": 1.}.get(col, np.nan)
        original = f[col]
        f[col] = numeric(original)
        if (original.notna() & f[col].isna()).any():
            raise ValueError(f"{path.name}: nonnumeric {col}")
    if (f.close.dropna() <= 0).any() or not f.close.notna().any():
        raise ValueError(f"{path.name}: at least one positive close is required; observed closes must be positive")
    # Exchange exports can contain '-' on a session with no trade. Keep the
    # missing observation so liquidity/coverage checks see it; never invent a
    # valid closing quote or silently drop a session with actual activity.
    if (f.close.isna() & ((f.shares.fillna(0) > 0) | (f.volume_mad.fillna(0) > 0))).any():
        raise ValueError(f"{path.name}: trading activity without a closing price")
    if f.split.isna().any() or (f.split <= 0).any():
        raise ValueError(f"{path.name}: split must be positive and nonmissing")
    for col in ("shares", "volume_mad", "dividend"):
        if (col == "dividend" and f[col].isna().any()) or (f[col].dropna() < 0).any():
            raise ValueError(f"{path.name}: invalid or missing {col}")
    if any((~np.isfinite(f[col].dropna())).any() for col in ["close", "split", "shares", "volume_mad", "dividend"]):
        raise ValueError(f"{path.name}: non-finite numeric values")
    if ((f.shares % 1).abs() > 1e-8).any():
        raise ValueError(f"{path.name}: shares must be whole units")
    if (f.open.dropna() <= 0).any():
        raise ValueError(f"{path.name}: invalid opening price")
    if "name" not in f:
        f["name"] = f.ticker
    return f[["date", "ticker", "name", "open", "close", "high", "low",
              "volume_mad", "shares", "dividend", "split"]]


def load_benchmark(path: Path | None, calendar: pd.DatetimeIndex) -> pd.Series:
    """Effective annual cash rate known at interval START, accrued ACT/365.

    A rate dated t is available at close t and first applies from t to the next day.
    Accrual integrates changes on nontrading dates instead of taking a future rate.
    """
    if path is None:
        return pd.Series(1., index=calendar, name="cash_index")
    f = pd.read_csv(path)
    if not {"date", "annual_rate"} <= set(f):
        raise ValueError("cash_rates needs date,annual_rate")
    f["date"] = pd.to_datetime(f.date, format="%Y-%m-%d")
    if f.date.duplicated().any():
        raise ValueError("Duplicate cash rate dates")
    rates = pd.Series(numeric(f.annual_rate).to_numpy(), index=f.date).sort_index()
    if rates.isna().any() or (~np.isfinite(rates)).any() or (rates <= -1).any():
        raise ValueError("Invalid cash rates: use decimal annual rates, e.g. 0.025")
    if rates.index.min() > calendar.min():
        raise ValueError("Cash benchmark must cover first price date")
    dates = pd.date_range(calendar.min(), calendar.max(), freq="D")
    daily = rates.reindex(rates.index.union(dates)).sort_index().ffill().reindex(dates)
    growth = (1 + daily.shift(1)).pow(1/365).fillna(1).cumprod()
    return growth.reindex(calendar).rename("cash_index")


def load_dataset(root: Path, cfg: Config) -> Dataset:
    files = sorted((root / cfg.prices_dir).glob("*.csv"))
    if not files:
        raise ValueError("No CSVs in prices_dir; put exchange exports there")
    pieces, sources = [], []
    for path in files:
        pieces.append(read_price_file(path))
        sources.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    raw = pd.concat(pieces, ignore_index=True)
    raw = raw.drop_duplicates()
    if raw.duplicated(["ticker", "date"]).any():
        rows = raw[raw.duplicated(["ticker", "date"], keep=False)][["ticker", "date"]]
        raise ValueError(f"Conflicting duplicate observations:\n{rows.head(10)}")
    if cfg.corporate_actions_file:
        action_path = root / cfg.corporate_actions_file
        actions = pd.read_csv(action_path, encoding="utf-8-sig")
        if not {"date", "ticker"} <= set(actions) or not {"dividend", "split"} & set(actions):
            raise ValueError("Corporate actions need date,ticker and dividend and/or split")
        actions["date"] = pd.to_datetime(actions.date, format="%Y-%m-%d", errors="raise")
        actions["ticker"] = actions.ticker.astype(str).str.strip().str.upper()
        if actions.duplicated(["ticker", "date"]).any():
            raise ValueError("Duplicate corporate action dates for the same ticker")
        for col, neutral in (("dividend", 0.), ("split", 1.)):
            if col not in actions:
                actions[col] = neutral
            actions[col] = numeric(actions[col])
            if actions[col].isna().any() or (~np.isfinite(actions[col])).any() or (actions[col] < 0).any() or (col == "split" and (actions[col] <= 0).any()):
                raise ValueError(f"Invalid corporate-action {col}")
        keys = raw[["date", "ticker"]]
        if len(actions.merge(keys, on=["date", "ticker"], how="left", indicator=True).query("_merge != 'both'")):
            raise ValueError("Corporate actions reference dates/tickers absent from price exports")
        raw = raw.merge(actions[["date", "ticker", "dividend", "split"]], on=["date", "ticker"],
                        how="left", suffixes=("", "_action"), validate="one_to_one")
        for col, neutral in (("dividend", 0.), ("split", 1.)):
            revised = raw[f"{col}_action"]
            conflicts = revised.notna() & ~np.isclose(raw[col], neutral) & ~np.isclose(raw[col], revised)
            if conflicts.any():
                raise ValueError(f"Conflicting {col} between price export and corporate actions")
            raw[col] = revised.combine_first(raw[col])
            raw = raw.drop(columns=f"{col}_action")
        sources.append({"file": cfg.corporate_actions_file,
                        "sha256": hashlib.sha256(action_path.read_bytes()).hexdigest()})
    if cfg.as_of:
        raw = raw[raw.date <= pd.Timestamp(cfg.as_of)].copy()
    if raw.empty:
        raise ValueError("No data on or before as_of")
    calendar = pd.DatetimeIndex(sorted(raw.date.unique()))
    warnings = []
    if cfg.calendar_file:
        cal = pd.read_csv(root / cfg.calendar_file)
        calendar = pd.DatetimeIndex(pd.to_datetime(cal.date, format="%Y-%m-%d")).sort_values().unique()
        calendar = calendar[(calendar >= raw.date.min()) & (calendar <= raw.date.max())]
        if not raw.date.isin(calendar).all():
            raise ValueError("Some prices fall outside supplied trading calendar")
    else:
        warnings.append("Calendar inferred from union of input dates; a market-wide missing session cannot be detected. Supply an official calendar for validation.")
    if cfg.return_mode == "price_proxy":
        warnings.append("PRICE PROXY: dividends/splits are not certified complete. Results are research illustrations, not a faithful total-return replication.")
    if not cfg.cash_rates_file:
        warnings.append("No MAD cash benchmark supplied: zero is used explicitly as a proxy.")
    warnings += [
        "Universe is limited to supplied files; historical selection/delistings are not verified (survivorship risk).",
        "QMQ, suspended/reserved status and live quotes are not supplied. No executable orders are produced.",
        "Fees and slippage are modeled; capital-gains tax/TPCVM and settlement restrictions are not modeled.",
        "Backtests mark holdings at last available prices. Stale holding valuations and weight breaches are logged; returns can be overstated by stale prices.",
    ]
    metadata = pd.DataFrame(index=sorted(raw.ticker.unique()))
    metadata.index.name = "ticker"
    metadata["sector"] = "UNCLASSIFIED"
    metadata["active_from"] = pd.NaT
    metadata["active_to"] = pd.NaT
    if cfg.metadata_file and (root / cfg.metadata_file).exists():
        meta = pd.read_csv(root / cfg.metadata_file).fillna("")
        meta["ticker"] = meta.ticker.str.strip().str.upper()
        if meta.ticker.duplicated().any():
            raise ValueError("Duplicate universe metadata ticker")
        meta = meta.set_index("ticker")
        for col in metadata:
            if col in meta:
                vals = meta[col].reindex(metadata.index)
                if col.startswith("active_"):
                    metadata[col] = pd.to_datetime(vals.replace("", pd.NA), format="%Y-%m-%d")
                else:
                    metadata[col] = vals.replace("", "UNCLASSIFIED").fillna("UNCLASSIFIED")
    benchmark = load_benchmark(root / cfg.cash_rates_file if cfg.cash_rates_file else None, calendar)
    frames, audit = {}, []
    for ticker, original in raw.groupby("ticker", sort=True):
        original = original.sort_values("date").set_index("date")
        f = original.reindex(calendar).copy()
        f["observed"] = f.close.notna()
        f["activity_known"] = f.shares.notna() & f.volume_mad.notna()
        f["close"] = f.close.ffill()  # never backfill an IPO
        for col in ("shares", "volume_mad", "dividend"):
            f[col] = f[col].fillna(0.)
        f["split"] = f.split.fillna(1.)
        f["traded"] = f.observed & f.activity_known & (f.shares > 0) & (f.volume_mad > 0)
        f["return"] = (f.close + f.dividend) * f.split / f.close.shift(1) - 1
        f["index"] = (1 + f["return"].fillna(0)).cumprod().where(f.close.notna())
        rf = benchmark.pct_change(fill_method=None).fillna(0)
        f["excess_return"] = f["return"] - rf
        # Finite-history normalized exponential population variance; all observations <= date.
        f["volatility"] = np.sqrt(cfg.annualization * f.excess_return.ewm(
            com=cfg.ewm_com, adjust=True, min_periods=cfg.min_vol_observations).var(bias=True))
        last_trade = pd.Series(np.where(f.traded, np.arange(len(f)), np.nan), index=calendar).ffill()
        f["stale_sessions"] = np.arange(len(f)) - last_trade
        f["liquidity_fraction"] = f.traded.astype(float).rolling(cfg.liquidity_sessions, min_periods=cfg.liquidity_sessions).mean()
        f["average_volume_mad"] = f.volume_mad.rolling(cfg.liquidity_sessions, min_periods=cfg.liquidity_sessions).mean()
        f["coverage"] = f.observed.astype(float).rolling(cfg.min_vol_observations, min_periods=cfg.min_vol_observations).mean()
        # Flag large unadjusted moves for review; never silently winsorize them.
        f["large_move"] = f["return"].abs() > cfg.large_return_flag
        frames[ticker] = f
        audit.append({"ticker": ticker, "name": str(original.name.iloc[-1]) if "name" in original else ticker,
                      "first_date": original.index.min().date().isoformat(),
                      "last_observation": original[original.close.notna()].index.max().date().isoformat(),
                      "rows": len(original), "missing_market_sessions": int((~f.observed & f.close.notna()).sum()),
                      "missing_close_export_rows": int(original.close.isna().sum()),
                      "zero_trade_rows": int((original.shares == 0).sum()),
                      "unknown_activity_rows": int((original.shares.isna() | original.volume_mad.isna()).sum()),
                      "traded_fraction_last_60": f.liquidity_fraction.iloc[-1],
                      "mean_volume_last_60_mad": f.average_volume_mad.iloc[-1],
                      "large_moves_to_review": int(f.large_move.sum()),
                      "split_events": int((f.split != 1).sum()), "dividend_events": int((f.dividend > 0).sum())})
    return Dataset(frames, calendar, metadata, benchmark, pd.DataFrame(audit), sources, warnings)


def complete_month_ends(calendar: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """Conservatively omit the final observed month, even if its last row looks final.

    A following-month observation proves that an earlier month is complete.
    """
    last_period = calendar[-1].to_period("M")
    return [pd.Timestamp(group.max()) for period, group in
            pd.Series(calendar, index=calendar).groupby(calendar.to_period("M"))
            if period < last_period]
