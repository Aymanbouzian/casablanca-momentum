"""Merge several exchange exports of ONE stock (English or French headers) into one clean file.

Checks every input: single ticker, ticker matches across files, overlapping sessions agree.
Usage: python scripts/merge_exports.py OLD.csv NEW.csv [--out data/prices] [--tolerance 0.0]
"""
import argparse
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from casa_momentum.data import numeric

ENGLISH = ["Session", "Ticker", "Instrument", "Opening", "Last Price", "High", "Low",
           "Volume (MAD)", "Traded Shares", "Number of Trades", "Market Capitalization"]
FRENCH = {"Séance": "Session", "Ouverture": "Opening", "Dernier Cours": "Last Price",
          "Plus Haut": "High", "Plus Bas": "Low", "Titres Échangés": "Traded Shares",
          "Nb Transactions": "Number of Trades", "Capitalisation": "Market Capitalization"}
COMPARE = ["Opening", "Last Price", "High", "Low", "Volume (MAD)", "Traded Shares"]


def load(path: Path) -> pd.DataFrame:
    f = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig", dtype=str,
                    na_values=["-", "", "NA", "null"])
    f.columns = f.columns.str.strip()
    f = f.rename(columns=FRENCH)
    missing = [c for c in ENGLISH if c not in f]
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}")
    f["Ticker"] = f["Ticker"].str.strip().str.upper()
    if f["Ticker"].nunique() != 1:
        raise ValueError(f"{path.name}: expected one ticker, found {sorted(f['Ticker'].unique())}")
    f["_date"] = pd.to_datetime(f["Session"], format="%d/%m/%Y")
    if f["_date"].duplicated().any():
        raise ValueError(f"{path.name}: duplicate sessions")
    return f


def merge(paths, tolerance=0.0):
    frames = [load(p) for p in paths]
    tickers = {f["Ticker"].iloc[0] for f in frames}
    if len(tickers) != 1:
        raise ValueError(f"Files are for different tickers: {sorted(tickers)}")
    report = {"ticker": next(iter(tickers)), "inputs": [], "overlap_sessions": 0, "conflicts": []}
    base = frames[0].set_index("_date")
    report["inputs"].append((paths[0].name, len(frames[0])))
    for path, f in zip(paths[1:], frames[1:]):
        new = f.set_index("_date")
        common = base.index.intersection(new.index)
        report["overlap_sessions"] += len(common)
        for d in common:
            for c in COMPARE:
                a, b = numeric(pd.Series([base.at[d, c]])).iloc[0], numeric(pd.Series([new.at[d, c]])).iloc[0]
                if pd.isna(a) and pd.isna(b):
                    continue
                if pd.isna(a) != pd.isna(b) or abs(a - b) > tolerance:
                    report["conflicts"].append((d.date(), c, a, b))
        # Prefer the first file for overlapping sessions (conflicts are reported, not hidden)
        base = pd.concat([base, new.loc[new.index.difference(base.index)]])
        report["inputs"].append((path.name, len(f)))
    merged = base.sort_index(ascending=False)
    return merged, report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data/prices")
    ap.add_argument("--tolerance", type=float, default=0.0)
    ap.add_argument("--allow-conflicts", action="store_true")
    a = ap.parse_args()
    merged, rep = merge([p.resolve() for p in a.files], a.tolerance)
    print(f"Ticker {rep['ticker']}: inputs {rep['inputs']}; overlapping sessions {rep['overlap_sessions']}; "
          f"conflicts {len(rep['conflicts'])}")
    for c in rep["conflicts"][:20]:
        print("  conflict", c)
    if rep["conflicts"] and not a.allow_conflicts:
        sys.exit("Overlapping sessions disagree; not writing. Inspect the files or use --allow-conflicts.")
    start, end = merged.index.min(), merged.index.max()
    out = a.out / f"Cours_{rep['ticker']}_merged_{start:%Y-%m-%d}_{end:%Y-%m-%d}.csv"
    a.out.mkdir(parents=True, exist_ok=True)
    merged = merged.reset_index(drop=True)[ENGLISH]
    merged.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"Wrote {out} ({len(merged)} sessions, {start.date()} to {end.date()})")
