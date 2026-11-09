# From the papers to executable rules

## 1. Scope and source versions

Reviewed source files:

- **Moskowitz, Ooi and Pedersen (2012), Time Series Momentum**, Journal of Financial Economics 104, 228–250. Supplied file: `1-s2.0-S0304405X11002613-main (1).pdf`, 23 PDF pages. [DOI](https://doi.org/10.1016/j.jfineco.2011.11.003).
- **Moreira and Muir (2016), Volatility Managed Portfolios**, NBER Working Paper 22208, April 2016 / June 2016 revision in the supplied `w22208 (1).pdf`, 57 PDF pages. [NBER source](https://www.nber.org/papers/w22208). This mapping uses the supplied working-paper version, not silently a different journal version.

Both papers contain empirical tests and economic explanations in addition to trading definitions. The project implements their central portfolio construction equations, with the equity adaptations below. It does not claim to reproduce every regression, figure, asset-pricing model or original historical return.

## 2. The question being translated into code

Momentum asks: **does the direction of an asset's own past return help choose its next exposure?**

Volatility management asks: **can a portfolio improve its risk-adjusted behavior by reducing exposure after high realized variance?**

These are separate questions. A stock does not need to beat every other stock to have a positive time-series signal. The hybrid combines the two rules; that combination on individual Casablanca equities is an implementation choice, not an experiment directly demonstrated by either supplied paper.

## 3. Signal data and equity return construction

Let P be the unadjusted closing share price, D the cash dividend per post-split share on its ex-date, and S the new-shares/old-shares split ratio. The daily equity total-return proxy is:

```text
r[i,d] = S[i,d] * (P[i,d] + D[i,d]) / P[i,d-1] - 1
I[i,d] = I[i,d-1] * (1 + r[i,d])
```

For a two-for-one split, S = 2. This assumes the split is effective before that day's dividend entitlement. A dividend series must use a consistent gross/net convention. Rights issues, spin-offs and mergers require appropriate event processing or a vendor-cleaned series; the simple split/dividend formula cannot model them completely.

With the supplied files, D = 0 and S = 1 because those fields are absent, and the run is explicitly labeled `price_proxy`. Unknown actions are not asserted to be absent. A move larger than 25% within the momentum window blocks a proxy signal for review; this is only a coarse data check and cannot detect all actions.

For stocks, the 12-month signal in this adaptation compares cumulative equity return with cumulative cash return:

```text
R_equity_12m = I[end] / I[start] - 1
R_cash_12m   = B[end] / B[start] - 1
signal      = sign(R_equity_12m - R_cash_12m)
```

B is a MAD cash accumulation index. An effective annual rate of 0.025 means 2.5%, accrued ACT/365. A rate timestamp is the date it became available at close; the code applies it only to later intervals. The benchmark and actual account cash interest are separate: the simulated competition cash earns zero by default, even when a positive benchmark is supplied.

The original paper uses futures/forward **excess returns**, not ordinary stock prices. Do not feed a futures excess-return series into this equity importer and subtract cash a second time. The equity comparison here is a declared change of asset type and return accounting.

## 4. Twelve months means calendar months

For a completed October 2026 decision, use the last observed session of October 2026 and the last observed session of October 2025. Do not assume 252 rows always represents one year. Do not omit the latest month: the classic cross-sectional 12-to-2 construction is not this paper's main time-series rule.

The code uses only dates at or before the decision. It requires an observed anchor close and an anchor within seven calendar days of the intended month end. New listings with insufficient history are excluded. The separate latest-observation diagnostic uses the same configured window measured back from the latest observed date; its anchor can differ from the scheduled month-end anchor and never triggers a mid-month rebalance.

Implementation: `signals.snapshot`, `signals.make_schedule`, `data.complete_month_ends`.

## 5. Stock volatility: first paper, Section 2.4 / Equation 1

PDF page 6, journal page 233 defines an annualized exponentially weighted variance. The decay has center of mass 60 sessions:

```text
delta = 60 / 61
weight(age) is proportional to (1 - delta) * delta**age
variance_daily = weighted_mean((daily_excess_return - weighted_mean_return)**2)
volatility_annual = sqrt(261 * variance_daily)
```

The implementation uses `ewm(com=60, adjust=True).var(bias=True)`, normalized over the finite available history, and needs at least 180 daily returns. It includes returns known at the signal close and applies the resulting forecast only to later fills. The paper's lag convention likewise excludes the future payoff being forecast. The finite-history normalization, 180-observation requirement and 10% annual volatility floor are explicit practical choices. The annualization constant of 261 and center of mass of 60 follow the paper.

Illiquid stocks can appear artificially safe when unchanged quotes are treated as zero returns. Therefore the volatility floor is supplemented with liquidity and stale-price filters; it is not a replacement for them.

Implementation: `data.load_dataset`. The direct-weight numerical test independently verifies the EWM result.

## 6. Stock weights: first paper, Section 4.1 / Equation 5

PDF page 9, journal page 236: each instrument's sign is multiplied by a 40% risk scale divided by its volatility; portfolio returns average across available instruments.

```text
paper_weight[i] = sign(excess_12m[i]) * 0.40 / volatility[i] / N
```

The original portfolio can short and lever. The 40% is per-instrument volatility scaling before diversification, not a promised return, a 40% cash allocation or the diversified portfolio's volatility target.

Our contest adaptation:

```text
candidate[i] = max(0, sign(excess_12m[i])) * 0.40 / max(volatility[i], 0.10) / N
target[i] = min(candidate[i], 0.08)
```

`breadth_scaling="linear"` (default `off`) multiplies the redistribute-mode invested target by the share of eligible names with a positive signal, so exposure falls when few stocks trend up. It is compared against the fixed target in `model_comparison_*.csv` (variants `contest_12m_breadth`, `contest_blend_breadth`).

`exposure_mode=paper` preserves this formula: N counts all eligible stocks, including negative-momentum names; their allocations stay in cash. The default `exposure_mode=redistribute` instead assigns inverse-volatility preference across **positive** names and progressively fills toward 70% gross. Each name remains at or below 8%; each sector stays at or below 30%; gross stays at or below 95%. The target can be unreachable: two names can provide at most 16%, and a single `UNCLASSIFIED` sector can provide at most 30%. No optimizer uses future returns or selects a best-performing configuration.

`signal_mode=single` uses `lookback_months` (12 default, 6 or 3 optional). `blend_3_6_12` averages `12 × excess_return / months` for the three completed-month windows, requiring all three anchors. `12-1` uses the return from the end of month t−12 to t−1, excluding the latest month. A selected signal is positive only when its excess score is positive. All prices and liquidity statistics are dated no later than the decision close. Up to `max_liquid_universe_size=40` eligible names are selected at each date by trailing 60-session traded MAD before considering signal signs. This is a research design choice, not the original paper's specification.

Every target is long-only. Unknown sectors share the `UNCLASSIFIED` bucket. The 60 supplied symbols currently have provisional sector labels; see `SECTORS.md`. Sector and gross limits are target-time controls; weights can drift during the month. An observed single-stock close at or above 9.2% queues a preventive trim toward 8% for the next session. A jump past 10% or an unfilled trim is still recorded as a breach; the simulator never erases it retroactively.

Implementation: `signals.paper_weight` preserves the original signed algebra; `signals.allocate` performs the constrained equity allocation.

## 7. Eligibility and liquidity: declared implementation choices

Defaults, applied using information available at the decision:

| Requirement | Default |
|---|---:|
| Valid observed anchor for all selected windows | Required |
| Daily returns for volatility | At least 180 |
| Observed-price coverage over latest 180 market sessions | At least 95% |
| Sessions with positive reported shares AND traded MAD over latest 60 sessions | At least 80% |
| Average traded MAD over those 60 sessions | At least 1,000,000 MAD (raised from 100,000: an 8% position is about 80,000 MAD, which was most of a thin stock's daily value) |
| Sessions since last observed trade | At most 3 |
| Unreviewed daily return over 25% in price-proxy lookback | Exclude |

Missing activity is recorded separately from explicit zero activity. For the liquidity screen it contributes zero to confirmed traded activity and to the conservative volume estimate. Such rows cannot produce fills. Missing prices are carried forward only for valuation/return alignment; they never make an unobserved session tradable. Historical universe dates are honored if provided.

These numerical thresholds are not extracted from the papers, not calibrated to win this competition, and not proven optimal for Casablanca. They are transparent defaults intended to prevent obvious stale/illiquid allocations. Exchange rows with a missing close and no reported activity are retained as unobserved, not converted to zero-price trades.

## 8. Optional portfolio variance overlay: second paper, Section 2.2 / Equation 1

The default `vm_mode=off` uses only per-stock inverse-volatility sizing. Set `legacy` for the following paper-based inverse-variance rule, which can reduce exposure and add cash drag. Set `target_vol` to scale by `(target annual volatility / last complete month's annualized reference volatility)` with a configurable multiplier limit; then apply the name, sector and gross caps. If the reference is flat/zero variance, the target-vol multiplier is **one**, not a spurious increase. It is not a guarantee that target volatility can be reached, particularly with few positive names. Neither rule is chosen as a winner from the inspected sample.

Equation 1 begins on PDF page 7 (printed page 5); the realized-variance definition is Equation 2 on PDF page 8 (printed page 6):

```text
RV[t] = sum over days d in completed month t of (f[d] - mean(f in t))**2
managed_excess_return[t+1] = c / RV[t] * f[t+1]
```

RV is a **sum of daily squared deviations**, not a mean and not a standard deviation. f is a portfolio/factor excess return. The code first simulates an untimed, cost-free reference version of the constrained momentum portfolio, with the same shares and liquidity mechanics. Daily reference excess returns subtract the MAD cash benchmark.

The paper sets c to equalize unconditional volatility in the sample for its empirical comparison. Estimating c using all future test returns would leak information into a deployable capped strategy. This implementation instead:

1. Builds complete reference months, excluding its initialization month and the unfinished final month.
2. Forms chronological pairs `(next_month_excess_return, next_month_excess_return / previous_month_RV)` with valid prior variance.
3. Uses the **first 12 valid pairs**, available by the decision date, to fit:

```text
c = sample_std(next_month_excess_return) /
    sample_std(next_month_excess_return / previous_month_RV)
```

4. Freezes c permanently for that run.
5. Uses the base allocation while calibrating; an overlay-only evaluation can begin on the session after calibration completes.
6. Applies `multiplier = min(1, vm_max_multiplier, c / RV)` after base weights for `legacy`. Unused exposure stays cash.

Months with RV at/below 1e-6 are excluded as denominators for calibration. Once calibrated, such months receive the capped scale of one. Missing variance after calibration yields zero exposure. The 8/30/95% target caps always apply. The supplied sample calibrates on a short history; the resulting estimate is uncertain.

The constant scales exposure, not the relative stock weights. This is different from cross-sectional risk parity. The reference portfolio is kept separate from the scaled portfolio to prevent circular variance feedback.

Implementation: `volatility.managed_factor` exposes the uncapped equation; `volatility.build_overlay` supplies the lagged, calibrated scale; `cli.run` maintains the separate reference.

## 9. Execution and accounting timeline

1. Carry prior settled positions and cash into a session. Accrue optional cash yield.
2. Apply reported splits and dividend entitlement to existing holdings before simulated trades.
3. Try outstanding orders decided after earlier closes. Each child order is **all-or-none**; with `order_slicing` a target larger than one day's volume cap is filled over several sessions (expiring after `slicing_ttl_sessions`, default 10). No same-session decision/fill is allowed.
4. Simulate execution at this later session's observed close, plus 10 bps adverse slippage for buys or minus 10 bps for sells. Impose a cap of 1% of that day's reported shares. The realized volume is an ex-post fill constraint, not a predictive input to order sizing.
5. Sell before buying; restrict buys to available cash and the hard stock cap; charge 77 bps of fees per side by default. Without slicing, an order that cannot fit in full is left unfilled; with slicing, that day's fillable part is filled and the rest stays pending. `allow_partial_fills` remains a research-only option.
6. Mark the portfolio at close; record cash, NAV, fees, positions, stale valuations and any weight breaches.
7. Only after that mark, form the next monthly target and fixed integer-share orders. Ignore a drift gap at or below `max(1% NAV, 25% of the current position)` by default; always permit a full exit or a trim at/above the 9.2% guard. Reduce share quantities to reserve estimated fees and slippage. Residual orders expire after five market sessions or are replaced by a later monthly decision.
8. At **every** observed close, queue a preventive sell to the 8% target if any stock reaches 9.2%. If a stock still closes over 10%, keep the breach in the record and queue a reduction for the next session. A jump or an unfilled order can still breach the hard cap; never erase the breach by pretending the closing price was known earlier. The real platform's alert, buy block and automatic regularization are described in its FAQ, but not fully simulated.

This daily-close fill model is a research approximation. It is not the platform's opening/fixing/intraday matching process. QMQ is set by the organizers and is not inferred from the volume participation cap. Quotes can be stale or untradeable, and the model lacks historical reserved/suspended flags. The review worksheet is not an order file.

Dividends are credited on the ex-date as a simplification; actual payment dates and withholding can differ. Fractional shares after a split are converted to cash at the ex-date close. No automatic delisting liquidation, rights subscription or merger conversion is assumed. These limitations remain material even in `total_return` mode.

## 10. Portfolio evaluation

The selected strategy and a passive comparator run over common data; the optional overlay is additionally run when enabled:

- `tsmom`: constrained long-only time-series momentum.
- `tsmom_vm`: selected strategy with a non-off portfolio multiplier.
- `passive`: equal allocations to eligible supplied stocks, clipped by the same caps; no positive-momentum requirement.

The passive comparator shares the history/liquidity filters and is not a broad market total-return index. A proper MASI total-return benchmark can be added when the relevant clean history is available; no such series is invented here.

NAV compounds realized daily account returns. Reported statistics include total return, calendar-time CAGR, daily standard deviation times sqrt(261), Sharpe relative to the supplied/zero benchmark, maximum drawdown, equity exposure, modeled fees/slippage, one-way turnover and closing-cap breach days. Metrics include first-day costs in each evaluation window. Zero risk does not produce an infinite Sharpe; it is reported as missing.

The full-period table includes all supplied observations after the initial signal. With `holdout_start=2026-05-01`, `model_comparison_training.csv` reports the common earlier interval and `model_comparison_same_period.csv` reports later sessions beginning 2026-05-04. This is an **exploratory** chronological comparison; the existing files have already been inspected. A genuine held-out test requires setting aside future observations before choosing any rule. The 12-month signal remains the default rather than selecting a faster variant from these results. No statistical significance or probability of winning is inferred. The history includes periods before the competition and is not a reconstruction of the user's actual account.

## 11. Coverage of all paper sections

| Paper / section | What it contributes | Code or scope decision |
|---|---|---|
| TSMOM §1 | Own-history predictability; distinction from ranking assets | `snapshot`; no top-N ranking substituted |
| TSMOM §2.1–2.3, Appendix A | Futures/forwards, factor and trader-position datasets | Replaced with supplied equity data; not a reproduction of the original universe |
| TSMOM §2.4, Eq.1 | Exponential volatility estimate | `load_dataset` EWM, 261 sessions, COM 60 |
| TSMOM §3.1 | Return-predictability regressions | Empirical evidence, not an extra trading signal; not reproduced without the original panel |
| TSMOM §3.2 | Lookback/holding-horizon portfolios | Main k=12, h=1 configuration implemented; no overlapping h>1 cohort experiment |
| TSMOM §4.1, Eq.5 | Main diversified strategy | `paper_weight`, `allocate`, monthly schedule |
| TSMOM §4.2–4.4 | Factor relations, extreme markets, liquidity and sentiment | Risks documented; liquidity screens and drawdown/cost diagnostics added; original regressions require absent factors |
| TSMOM §5 | Analytical comparison with cross-sectional momentum | Used to preserve the own-history signal; cross-sectional strategy and linear analytical decomposition are not substituted |
| TSMOM §6 | CFTC positions, VAR, spot/roll return decomposition | Economic analysis; CFTC/futures/roll datasets absent, so not fabricated |
| TSMOM §7 | Interpretation and limits | No causal or guaranteed-profit claim inferred |
| Volatility Managed §1–2.1 | Motivation and equity-factor datasets | Defines the second question; Casablanca stocks are a different dataset |
| Volatility Managed §2.2, Eq.1 | Lagged inverse portfolio variance | `managed_factor`, `build_overlay` |
| Volatility Managed §2.3–2.4 | Alphas and utility gains | Report performance/drawdown; do not import the paper's gains as expected Moroccan returns |
| Volatility Managed §2.5, Eq.5 | Timing an MVE multifactor portfolio | MVE weights use unavailable factors and in-sample estimates; not fitted to two equities; hybrid uses a fixed construction instead |
| Volatility Managed §3.1–3.3 | Recessions, transaction costs, leverage limits | Costs and no-leverage cap implemented; no recession/option-embedded leverage study |
| Volatility Managed §3.4–3.5 | Risk parity/BAB distinction, common variance component | Overlay scales total exposure; no PCA or BAB inference from absent factor data |
| Volatility Managed §3.6–3.7 | Horizons and robustness | Monthly strategy selected; longer-horizon VAR and international robustness not replicated |
| Volatility Managed §§4–5 | Price of risk, stochastic discount factors, equilibrium models | Theory interpreted in this guide, not additional buy/sell inputs |
| Volatility Managed §6 | Conclusion | Motivation only, not a guarantee for this application |
| Appendix A.1–A.5, tables/figures | Expected-variance alternatives, OECD checks, tail/option-like performance | No fabricated countries/factors; plain lagged RV chosen instead of fitting a new forecasting model |
| Appendix A.6, including individual-stock and correlated-factor discussion | Proofs and scope of systematic-risk interpretation | No claim that individual-stock gains identify the aggregate price of risk |

## 12. Validation

Tests compare EWM volatility with independent direct weights; distinguish inverse variance from inverse volatility; validate split/dividend wealth conservation; test calendar anchors, new listings and duplicate conflicts; verify fee/cash accounting, no same-day fills, volume caps and all-or-none execution; retain a simulated closing-cap breach; and block account trade differences when orders are pending.

The no-lookahead test changes every future price after a cutoff and requires earlier signals and NAVs to remain identical. A separate overlay test changes future returns and requires the frozen constant and earlier multipliers to remain unchanged. A 42-equity synthetic fixture checks the dated liquidity ranking; a separate rising-price fixture verifies that the daily guard prevents a 10% breach when the next-session trim fills.

## 13. External implementation references

- [Official e-bourse rules PDF, labeled 2025](https://www.e-bourse.ma/sites/default/files/reglements.pdf), §5: order execution, 10% closing cap, QMQ and costs. The [published official FAQ](https://www.e-bourse.ma/comment-jouer) describes an alert, blocked buys and automatic opening-price regularization on the fourth day if a price-driven breach is not corrected within three days. No separate points penalty is stated in that FAQ answer. Confirm which edition's rules apply to the current contest. The simulator models only a subset; TPCVM and automatic regularization are not deducted.
- [Official Casablanca historical prices](https://www.casablanca-bourse.com/en/market-data/Cours): instrument/date selection and three-year free history description. Retrieved 9 November 2026.
- [pandas exponentially weighted calculations](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.ewm.html).
- [Python virtual environments](https://docs.python.org/3/library/venv.html).

Read the currently applicable competition rules before placing manual orders. The implementation settings and the code, not the papers' reported historical returns, determine the results in this package.
