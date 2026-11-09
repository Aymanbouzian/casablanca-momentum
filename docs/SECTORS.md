# Sector labels used by the research cap

`data/universe.csv` includes a provisional sector for each of the 60 validated symbols. Most labels were mapped from the current 9 November 2026 [Pivo Bourse sector board](https://pivobourse.com/); REB follows the [exchange's issuer listing](https://www.casablanca-bourse.com/en/emetteurs/liste-emetteurs), which places it in Mining. The mapping gives the research 30% sector cap a meaningful grouping instead of putting almost every stock in `UNCLASSIFIED`.

These labels are a **current reference**, not a time-stamped sector-membership history or an exchange certification of the complete CSV. Review the issuer-sector classifications against the exchange before relying on the backtest, and supply `active_from` / `active_to` dates plus delisted histories to improve historical universe coverage. Missing sector labels share one `UNCLASSIFIED` bucket and therefore one 30% cap.

The exchange competition's 10% limit applies per stock; this project's 30% sector target is an additional strategy constraint.
