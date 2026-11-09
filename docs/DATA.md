# Data contract and expanding the universe

## Inputs already included

The original `AGM.csv` and `AKT.csv` are kept unchanged. The latest archive supplied 74 additional price CSVs, of which 58 passed identity and usable-history validation. `data/prices` therefore holds 60 distinct tickers. `outputs/import_audit.csv` identifies the 13 mislabeled copies and three nearly empty files that were excluded; their guessed filenames must **not** be used as evidence of other stocks' prices. In particular, the file named `Cours_MNG...` contains GTM, not Managem. A valid SAH file now supplies a Sanlam price history. Output hashes identify the exact accepted bytes.

The original AGM/AKT files contain 735 rows from 2023-11-10 to 2026-11-05. Some newer accepted exports reach 2026-11-09. The importer sorts ascending. AGM has many rows with unknown activity and few confirmed trades; consult `outputs/data_audit.csv` for each stock's history and latest liquidity.

## Option A — original exchange exports

Put CSV downloads in `data/prices`. Ticker identity is always read from content. For an exchange export named `Cours_XXX...`, the importer also checks that `XXX` matches its embedded ticker; arbitrary canonical filenames still work. Multiple equities in one canonical file are supported. For a ZIP of exchange exports, run `python scripts/import_exchange_zip.py path/to/archive.zip` to validate and keep an audit of rejected files.

Recognized columns are:

```text
Session;Ticker;Instrument;Opening;Last Price;High;Low;Volume (MAD);Traded Shares;Number of Trades;Market Capitalization
```

`Session` uses DD/MM/YYYY. A UTF-8 BOM and trailing spaces in ticker labels are accepted. `-` means missing. Every observed closing price must be positive; a missing close is accepted **only** if reported trading activity is zero or unknown. That session remains unobserved for liquidity/coverage and cannot fill an order. Missing opening/high/low values are accepted. Missing traded quantities/values are retained as unknown in the audit and are never interpreted as proof of a tradable session.

The backtest uses raw closing prices; `Market Capitalization` and `Number of Trades` are not needed for the calculations. Opening/high/low columns are retained for possible extensions, not used to claim an intraday execution reconstruction.

## Option B — canonical CSV

Use a comma-delimited file with ISO dates:

```text
date,ticker,name,open,close,volume_mad,shares,dividend,split
```

Required: `date,ticker,close,volume_mad,shares`. Optional: `name,open,high,low,dividend,split`.

| Column | Meaning |
|---|---|
| `date` | YYYY-MM-DD, local market session date |
| `ticker` | Stable uppercase symbol; surrounding spaces are trimmed |
| `close` | Raw unadjusted closing share price in MAD |
| `volume_mad` | Actual traded value in MAD, not quantity |
| `shares` | Actual whole shares traded during the session |
| `dividend` | Cash dividend per post-split share, on the ex-date; zero on other dates |
| `split` | New/old shares, e.g. 2 for two-for-one; one on other dates |

Use numeric decimals without thousands separators. The numeric parser tolerates decimal commas and spaces, but standard decimals are easier to audit. Rejecting malformed data is preferable to silently deleting a price.

**Do not put an adjusted close in `close` while also supplying split/dividend adjustments.** That double-counts the actions and misprices actual share trades. Adjusted-only vendor histories must first be converted to a consistent raw-price + action dataset if you want this share-account backtest. A header named `adjusted_close` is not automatically used.

If replacing an exchange export with an enriched canonical file, remove the original from `data/prices` or move it to an archive folder. Identical overlapping observations are deduplicated; conflicting overlaps stop the run. This catches unnoticed revisions and accidental double inputs.

## Total-return mode

1. Obtain and verify complete corporate-action histories for **every supplied equity** over the full sample.
2. Merge supported dividend and split events into the canonical prices on their effective dates. Verify price/share unit consistency.
3. Provide a MAD cash-rate file covering the sample start.
4. Set `return_mode` to `total_return`, `corporate_actions_verified` to `true`, and `cash_rates_file` to the relative path.

The verification flag is your completeness assertion, not an automatic vendor certification. The code cannot infer unreported dividends, rights, delistings or mergers. Total-return mode improves return construction but does not make the execution/tax model an exact competition simulator.

## Cash benchmark

Copy the empty template to `data/cash_rates.csv` and populate:

```text
date,annual_rate
```

`annual_rate` is an effective annual decimal rate in MAD. A hypothetical 2.5% rate would be written `0.025`; no such rate is supplied as if it were observed. Dates must represent when the rate was available, not a later announcement backdated to a reference period. The first rate must be known on or before the first market observation. The last known rate is carried forward; no future rate is backfilled.

The accounting cash yield is a separate config field, `cash_annual_yield`, default 0. The benchmark is used to define excess returns; it is not silently paid to the virtual account.

## Universe metadata

`data/universe.csv` has:

```text
ticker,sector,active_from,active_to
```

Dates are optional ISO dates. They represent when that stock belongs in the research universe, based on information available at the time. Leaving them blank does not prove a stock was historically investable. To assess survivorship bias, add histories for stocks that disappeared too; do not just select today's survivors.

Static sector labels are used for target caps, not for a claim of historically exact classifications. The current provisional mapping and its source are explained in [SECTORS.md](SECTORS.md). Unlisted metadata rows are ignored until a price file exists. Stocks missing from metadata still load and share the `UNCLASSIFIED` sector bucket.

## Trading calendar

By default the engine uses the union of all supplied price dates. It can detect a missing row for one stock but cannot detect a day absent from **every** file.

An optional `calendar_file` contains one column, `date`, listing official market sessions. Observations outside that calendar fail validation. Missing sessions inside the sample remain explicit gaps. A following-month observation is required to treat an earlier month as complete; the final observed month is conservatively excluded even if its last row appears to be a month end.

## Account input for the review worksheet

`data/templates/account.example.json` contains an explicit hypothetical empty account. Copy it outside the templates directory and enter actual values if you want differences against your holdings.

- `cash_mad`: current available cash, not initial contest capital if you have already traded.
- `positions`: dictionary of settled whole shares, e.g. ticker-to-quantity. A price CSV is required for every ticker.
- `pending_orders`: unresolved orders; any nonempty list blocks proposed quantity differences.
- `as_of`: account snapshot date.

Do not put a pending buy into settled positions. The engine does not query the platform or verify that an order executed. It also does not reserve cash for unresolved orders; it blocks differences instead.

## QMQ and market status

The actual organizers' QMQ values, live reserved/suspended status, quote freshness and account commitments must be checked on the platform before a manual order. No historical QMQ dataset was supplied. The simulator's volume participation setting is only a research liquidity constraint and is never labeled QMQ.

## Getting more histories

Use the official [Prices page](https://www.casablanca-bourse.com/en/market-data/Cours), select the cash market and equities, choose an instrument and a date range, then use the download control. Repeat for your defined eligible universe. The official page describes free access to up to three years of history. Longer histories can be requested from the exchange's Market Data service.

The project now contains 60 validated distinct histories. At the latest completed-month decision, 32 pass the signal-history/liquidity checks and one has positive 12-month momentum. The engine ranks up to 40 eligible names by **past** 60-session traded MAD before checking their momentum signs. Download the 16 excluded names again if their real histories are needed, including weak performers and former listings. Historical membership and complete dividend coverage remain unverified; the accepted file count does not establish unbiased performance. Current sector labels are provisional and historical `active_from` / `active_to` dates remain blank.

Use the existing `data/templates/canonical_prices.csv` to add verified ex-date dividend cash and split ratios directly to canonical prices, **or** put them in a separate `data/corporate_actions.csv` using `data/templates/corporate_actions.csv` and set `corporate_actions_file` to that path in `config.json`. The separate file needs an ISO date, ticker and nonnegative dividend per share and/or positive split ratio; every row must match an exported price date. Conflicting actions stop the run. A missing row is **not** evidence that no dividend occurred. Use `data/templates/cash_rates.csv` for dated effective annual MAD rates, and `data/templates/calendar.csv` for official session dates. Do not switch to `total_return` until dividend/split coverage and the cash benchmark are verified. The exchange's [market-data offer](https://www.casablanca-bourse.com/market-data/offre-market-data) describes historical dividends and corporate actions; access may require arranging the data directly with the provider.

An unofficial API or Yahoo suffix is not assumed to work. This package is deliberately based on validated local exports and does not scrape the competition account, require credentials or invent missing histories. Extra real datasets are needed before producing a market-wide portfolio result.
