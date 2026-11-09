# Casablanca Momentum

A runnable Python research project for your 1,000,000 MAD virtual portfolio. It reads **every stock CSV in a folder**, computes monthly own-history momentum, and builds a long-only, capped portfolio. The default is a 12-month signal with constrained redistribution and a no-trade band. Other signal windows and volatility rules are explicit config choices.

The two supplied papers were reviewed, including their empirical sections, portfolio equations and appendices. The implemented trading rules and the research analyses that require other datasets are mapped in [docs/METHOD.md](docs/METHOD.md). This is a **long-only equity adaptation**, not a replication of the papers' original futures/factor experiments.

## Open the result first

After extracting the ZIP, open **outputs/report.html** in your browser. The report, calculations, trades, positions, data audit and charts are already generated from the validated price files. No Python installation is needed just to read this report.

The latest uploaded archive contained 76 price CSVs. The importer retained the two existing originals, added **58 distinct, usable ticker-matched histories**, and excluded 16 duplicates, mislabeled copies or nearly empty histories. That leaves **60 stock histories**; at the latest completed month-end, **32** pass the history/liquidity screens. See `outputs/import_audit.csv` for each decision. Synthetic tests never enter the real dataset.

## Run on your Windows PC

Extract the entire ZIP first. Open the resulting `casablanca_momentum` folder.

1. Double-click **SETUP_WINDOWS.cmd** once. It creates a local Python environment and installs dependencies.
2. Double-click **RUN_WINDOWS.cmd** to run the project and open the report.
3. Add further verified stock CSVs to `data/prices`, then run again. For a full archive, run `python scripts/import_exchange_zip.py path\to\archive.zip` inside this folder to validate and log each file.

Alternatively, open PowerShell **inside this folder** and use:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --config config.json
```

These commands call the environment's Python directly; no PowerShell activation script or execution-policy change is needed. A standard supported Python 3.11+ installation is required. Dependency ranges allow current Python-compatible releases; this package's tested versions are recorded in the included run manifest. Your Windows/Python 3.14 installation is not the machine on which these tests were run.

In PyCharm: open this folder as a project, choose `.venv\Scripts\python.exe` as interpreter, and run `run.py` with the project folder as working directory.

On Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py --config config.json
```

## What it calculates

1. At each completed month end, rank eligible equities by their **trailing traded MAD**, keeping up to 40; compare their own 12-month return with the MAD cash benchmark.
2. Positive excess return permits a long position; nonpositive names remain at zero. Redistribute across positive names, favoring lower observed volatility, toward **70% invested when feasible**.
3. Apply **8% per stock**, **30% per sector**, and **95% gross** caps; if there are too few positive names or sectors, the balance stays in cash. The old paper denominator is available with `"exposure_mode": "paper"`.
4. Skip small monthly drift trades unless the value gap exceeds the larger of **1% of NAV** and **25% of the current position**. Each daily close of **9.2% or above** queues a preventive trim to the 8% target for the next session. Full exits also proceed.
5. Simulate next-session close fills, whole shares, cash, fees, slippage and conservative volume constraints. Record any closing weight over 10% even if a price jump or unfilled order made prevention impossible.
6. Optional `"vm_mode": "legacy"` reproduces a lagged inverse-variance reduction; `"target_vol"` scales toward a volatility preference, capped by all position/sector/gross rules. Default `"off"` avoids an extra cash drag.

The original 40% paper input is a per-instrument risk scale, not a return forecast. It is used in `paper` exposure mode; the default redistribution is a contest adaptation.

## Expand to multiple real equities

The 60 validated files are already in **data/prices**. Additional official CSV exports can be imported there without a ticker-list edit. One combined canonical CSV with several tickers also works. Avoid adding files just because they performed well in the past. Thirteen archive entries contained a different ticker than their `Cours_XXX` filenames; three more had only one observed close. Those 16 are excluded and documented in `outputs/import_audit.csv`. The missing names need fresh exports, especially large liquid stocks **ATW, BCP and BOA**.

The official [historical prices page](https://www.casablanca-bourse.com/en/market-data/Cours) provides an instrument selector, date range and download control. Choose the same date range and daily frequency for each instrument. The page states a free history depth of three years. Start from a defined universe of competition-eligible equities; do not add only historical winners. More history and delisted stocks are needed for stronger historical validation.

The package now includes provisional current sector labels for the 60 validated symbols in `data/universe.csv`; see [docs/SECTORS.md](docs/SECTORS.md) for sourcing and limits. Unknown sectors still share the `UNCLASSIFIED` 30% cap. Optional `active_from` / `active_to` dates constrain historical eligibility; they do not automatically recreate an historical exchange constituent database.

The exact schemas, adjustment rules and required supplementary data are in [docs/DATA.md](docs/DATA.md). Verified dividends/splits can be provided in `corporate_actions_file`; MAD cash rates and an official trading calendar have separate config paths. Empty templates live outside the price folder in `data/templates`, so examples never become real trading inputs.

## Current supplied-data result

The last observed session in the validated files is **9 November 2026**. The latest scheduled signal is **30 October 2026**: 32 of 60 histories pass all quality/liquidity checks, and only **SMI** has a strictly positive 12-month excess-price signal (+95.02%). The base target is **8% SMI and 92% cash**, subject to an account and live-price review. AKT is negative (−32.01%); AGMA's positive price change fails liquidity and staleness checks. This is a research signal, not an automatic order.

The raw files do not establish complete dividend/split histories, and MAD cash rates and an official trading calendar were not supplied. The report uses `price_proxy` mode with a zero cash-benchmark proxy. These omissions can change the signals and backtest. The sector mapping is provisional.

## Files to read

| File | Purpose |
|---|---|
| `outputs/report.html` | Main readable result; chart is embedded |
| `outputs/signals_latest_completed_month.csv` | Most recent scheduled signal with calculations and exclusions |
| `outputs/signals_latest_observation_DIAGNOSTIC.csv` | Newer observation check, not a rebalance |
| `outputs/target_holdings_REVIEW_ONLY.csv` | Illustrative whole-share target worksheet |
| `outputs/performance_common_evaluation.csv` | Aligned performance over common sessions |
| `outputs/model_comparison_same_period.csv` | Original paper sizing, redistribution, no-band sensitivity, 6-month, blend, 12-1 and chosen settings on common sessions |
| `outputs/model_comparison_training.csv` | Same variants on the earlier chronological interval |
| `outputs/import_audit.csv` | File-by-file accepted/excluded decision from the uploaded archive |
| `outputs/ONE_PAGE_SUMMARY.md` | Short competition strategy and data limitations |
| `outputs/tsmom/` and `outputs/passive/` | Daily NAV, positions, decisions, fills and event logs; `tsmom_vm/` only when enabled |
| `outputs/cost_sensitivity.csv` | Zero, configured and double-cost scenarios |
| `outputs/run_manifest.json` | Input hashes, exact configuration, versions and limitations |
| `docs/METHOD.md` | Equations, assumptions and paper-to-code mapping |
| `docs/DEMARRAGE_FR.md` | Short French explanation and instructions |

The passive comparison holds the same eligible supplied equities under the same caps. It is **not MASI**.

## Compare contest settings

Edit `config.json`: `signal_mode` accepts `single`, `blend_3_6_12` or `12-1`; in `single` mode change `lookback_months` to 6 or 3. The blend averages the 3, 6 and 12-month excess returns converted to a common 12-month scale; `12-1` measures from 12 months ago through the end of the preceding month. `exposure_mode` accepts `redistribute` or `paper`. `target_invested_weight`, `max_liquid_universe_size`, the two `min_trade_...` band settings, and `vm_mode` are configurable. `vm_mode=target_vol` also uses `vm_target_annual_vol` and `vm_max_multiplier`; scaling cannot override any contest cap.

The report shows an earlier period through **30 April 2026**, and a later interval from the first session after `holdout_start: "2026-05-01"` through 9 November 2026. We have already seen results from this historical period, so the split is **exploratory, not an untouched holdout**. The 12-month signal remains the default; do not select the 6-month or blend rule from these observed results. For a genuinely untouched evaluation, set a date beyond previously inspected data before obtaining it.

## Optional actual-account worksheet

Without an account file, the worksheet explicitly assumes a hypothetical all-cash account of 1,000,000 MAD. It does not assume your earlier Sanlam or Managem orders were filled.

Copy `data/templates/account.example.json` to `data/account.json`, enter your **actual cash, settled holdings, account date and unresolved pending orders**, and run:

```powershell
.\.venv\Scripts\python.exe run.py --account data/account.json
```

Supply a price CSV for every existing holding. Unresolved pending orders block the worksheet's trade-difference column. Prices remain historical, and the worksheet does not implement actual QMQ, reserved/suspended status, tick-size checks or live routing. It sends nothing to e-bourse or a broker.

## Tests and historical cutoff

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe run.py --as-of 2026-09-15 --output outputs_september
```

Cutoff dates filter input before analysis. The engine conservatively uses a following-month observation to establish that a month is complete; the last observed month is always excluded from scheduled decisions. This avoids treating an incomplete download as a finished month.

## Practical limits

This is a working research engine, not an exact e-bourse matching-engine emulator. It uses later daily closes plus slippage, while the platform has opening/intraday and fixing rules. Each child order is all-or-none, but with `order_slicing` (on in `config.json`) a large target is split into daily child orders, each capped at `participation_rate` of that day's volume, for up to `slicing_ttl_sessions` sessions. Set `order_slicing` to false for strict single-order all-or-none; a research-only `allow_partial_fills` option also exists. The 1% observed-volume limit is an execution assumption, **not the organizers' QMQ**.

The fee setting of 0.77% per side reflects the published maximum commission plus VAT calculation. Capital-gains tax/TPCVM, dividend withholding, settlement rules, rights issues, mergers and liquidation proceeds are not fully modeled. The default cash yield is zero. Splits and cash dividends are supported when valid inputs are provided, with the timing assumptions described in the method guide.

The daily 9.2% guard acts on the observed close and queues a next-session trim; it cannot prevent a single-session price jump or an unfilled sell. The organizer's [FAQ](https://www.e-bourse.ma/comment-jouer) says a >10% holding receives an alert, further buys are blocked, and an unresolved position is sold down at the fourth session's opening after three days; the FAQ does not state a separate points penalty for that situation. This simulator does not reproduce that automatic sale. Historical test success does not establish future profitability or a probability of winning.

## Changes in this version

- **Liquidity floor** raised to 1,000,000 MAD average daily traded value (was 100,000).
- **Order slicing** (`order_slicing`, `slicing_ttl_sessions`): large targets fill over several sessions instead of failing all-or-none. Unfilled orders fell from 1,097 to 0 and expired orders from 205 to 14, so backtested turnover and fees are now more realistic (higher).
- **Breadth scaling** (`breadth_scaling`: `off` or `linear`), compared in the model-comparison tables. Default stays `off`.
- 5 new tests (39 total).
