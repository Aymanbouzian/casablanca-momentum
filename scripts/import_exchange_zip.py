"""Import validated exchange CSVs from an archive; keep rejected files in the source ZIP."""
import argparse
import hashlib
from pathlib import Path
import sys
import tempfile
from zipfile import ZipFile

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from casa_momentum.data import read_price_file


def import_zip(source: Path, root: Path):
    prices = root / "data/prices"
    prices.mkdir(parents=True, exist_ok=True)
    existing = {}
    for p in prices.glob("*.csv"):
        f = read_price_file(p)
        symbols = set(f.ticker)
        if len(symbols) != 1:
            raise ValueError(f"Existing file {p.name} has multiple tickers; review before importing")
        existing[next(iter(symbols))] = p
    records = []
    with ZipFile(source) as archive, tempfile.TemporaryDirectory() as tmp:
        for member in archive.infolist():
            name = member.filename
            if member.is_dir() or not name.lower().endswith(".csv"):
                continue
            if "/data/prices/" not in name and "/" in name:
                continue
            filename = Path(name).name
            row = {"file": filename, "ticker": "", "rows": 0, "observed_closes": 0,
                   "status": "", "reason": "", "sha256": ""}
            contents = archive.read(member)
            row["sha256"] = hashlib.sha256(contents).hexdigest()
            candidate = Path(tmp) / filename
            candidate.write_bytes(contents)
            try:
                frame = read_price_file(candidate)
                symbols = set(frame.ticker)
                if len(symbols) != 1:
                    raise ValueError("One ticker per exchange CSV required for this importer")
                ticker = next(iter(symbols))
                row.update(ticker=ticker, rows=len(frame), observed_closes=int(frame.close.notna().sum()))
                if row["observed_closes"] < 2:
                    row.update(status="excluded", reason="fewer than two observed closes")
                elif ticker in existing:
                    prior = existing[ticker]
                    row.update(status="already_present" if prior.read_bytes() == contents else "excluded",
                               reason="identical existing file" if prior.read_bytes() == contents else
                                      f"another export for {ticker}: {prior.name}; review overlaps")
                elif (prices / filename).exists():
                    row.update(status="excluded", reason="filename collision")
                else:
                    (prices / filename).write_bytes(contents)
                    existing[ticker] = prices / filename
                    row.update(status="imported", reason="validated ticker and usable history")
            except (ValueError, KeyError) as error:
                row.update(status="excluded", reason=str(error))
            records.append(row)
    result = pd.DataFrame(records)
    if result.empty:
        raise ValueError("No price CSVs found in ZIP")
    output = root / "outputs/import_audit.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)
    print(result.status.value_counts().to_string())
    print(f"Import audit: {output}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate and import price CSVs from a ZIP")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    import_zip(args.archive.resolve(), args.project.resolve())
