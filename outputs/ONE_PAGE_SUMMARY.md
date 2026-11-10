# Casablanca competition — one-page strategy summary

Data cutoff: 2026-11-09. 64 distinct price histories; 25 pass the current history and liquidity checks, and 2 have a positive signal. Archive import: 58 new files validated, 16 excluded; inspect import_audit.csv. Sector labels are provisional; dividends, cash rates, the official calendar, historical membership and the platform QMQ still need verification.

Signal: 12-month excess return. Use completed-month data; own-history excess return above MAD cash proxy triggers a long. Size inverse to trailing volatility, then apply 8% per-stock, 30% sector and 95% gross caps. Exposure mode: redistribute. Liquidity floor: 1,000,000 MAD average daily traded value. 16.0% of NAV allocated against a 70% preference; 2 eligible positive names. The name and sector caps may prevent reaching the preference.

Orders: following-session close proxy, sliced into daily child orders capped at 1% of volume for up to 10 sessions; fee 0.77% per side plus slippage 0.10% per side. Ignore drift at or below max(1% NAV, 25% of held position); full exits and hard-cap corrections remain. An observed weight of 9.2% queues a preventive trim for the next session; a sudden price jump or an unfilled order can still breach 10%. Variance overlay: off.

Training: 2024-10-01 to 2026-04-30 in model_comparison_training.csv; later comparison begins 2026-05-01 in model_comparison_same_period.csv. This is exploratory, not an untouched holdout. Costs and results rely on price proxy, incomplete corporate actions and approximate fills. Validate dividends, official calendar, MAD cash rates, QMQ and historical membership before trading.
