"""All generated prices here are SYNTHETIC fixtures, never investment evidence."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal, assert_series_equal
from casa_momentum.config import Config
from casa_momentum.data import load_dataset, read_price_file, complete_month_ends, load_benchmark
from casa_momentum.signals import snapshot, allocate, make_schedule, paper_weight
from casa_momentum.backtest import simulate, rebalance_needed
from casa_momentum.volatility import build_overlay, managed_factor
from casa_momentum.planning import target_worksheet


def fixture(root, count=3, start="2023-01-02", end="2025-11-10"):
    """Seeded >2-equity integration data; created only inside temporary test directories."""
    folder = Path(root) / "data/prices"
    folder.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range(start, end)
    for i in range(count):
        rng = np.random.default_rng(113+i)
        r = .0008 + rng.normal(0, .008, len(dates))
        close = (80 + 10*i) * np.exp(r.cumsum())
        pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "ticker": f"S{i:02}",
                      "open": close * .998, "close": close, "shares": 1_000_000,
                      "volume_mad": close * 1_000_000}).to_csv(folder / f"S{i:02}.csv", index=False)
    return Path(root)


def valid_table(tickers):
    return pd.DataFrame({"ticker": tickers, "eligible": True, "excess_return_12m": .20,
                         "annual_volatility": .20, "sector": [f"sector{i%5}" for i in range(len(tickers))],
                         "signal_date": "2025-01-02", "reason": "positive_momentum"}, index=tickers)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = Config(metadata_file=None, run_cost_sensitivity=False)

    def tearDown(self):
        self.tmp.cleanup()

    def dataset(self, count=3, **kwargs):
        fixture(self.root, count=count, **kwargs)
        return load_dataset(self.root, self.cfg)

    def short_data(self, closes, splits=None, dividends=None, volumes=None):
        dates = pd.bdate_range("2025-01-02", periods=len(closes))
        folder = self.root / "data/prices"
        folder.mkdir(parents=True)
        qty = volumes if volumes is not None else np.repeat(100_000, len(closes))
        frame = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "ticker": "TEST",
                              "close": closes, "shares": qty, "volume_mad": np.array(closes)*qty})
        if splits is not None:
            frame["split"] = splits
        if dividends is not None:
            frame["dividend"] = dividends
        frame.to_csv(folder / "test.csv", index=False)
        data = load_dataset(self.root, self.cfg)
        schedule = {dates[0]: valid_table(["TEST"])}
        return data, schedule

    def test_exchange_format_and_unknown_volume(self):
        p = self.root / "sample.csv"
        p.write_text("Session;Ticker;Instrument;Opening;Last Price;Volume (MAD);Traded Shares\n05/11/2026;ABC  ;Example;-;100;-;-\n04/11/2026;ABC  ;Example;99;99;990;10\n", encoding="utf-8-sig")
        f = read_price_file(p)
        self.assertEqual(f.ticker.tolist(), ["ABC", "ABC"])
        self.assertEqual(f.date.iloc[0], pd.Timestamp("2026-11-05"))
        self.assertTrue(pd.isna(f.shares.iloc[0]))

    def test_nontrading_missing_close_stays_unobserved(self):
        p = self.root / "missing_close.csv"
        p.write_text("Session;Ticker;Instrument;Last Price;Volume (MAD);Traded Shares\n"
                     "03/01/2025;ABC;Example;-;0;0\n02/01/2025;ABC;Example;100;100000;1000\n")
        f = read_price_file(p)
        self.assertTrue(pd.isna(f.close.iloc[0]))
        p.write_text(p.read_text().replace("-;0;0", "-;100000;1000"))
        with self.assertRaisesRegex(ValueError, "activity without a closing price"):
            read_price_file(p)

    def test_conflicting_duplicate_rejected(self):
        fixture(self.root, 1)
        p = self.root / "data/prices/S00.csv"
        f = pd.read_csv(p)
        other = f.iloc[[0]].copy()
        other["close"] *= 2
        other.to_csv(p.with_name("conflict.csv"), index=False)
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            load_dataset(self.root, self.cfg)

    def test_exchange_filename_ticker_mismatch_rejected(self):
        p = self.root / "Cours_BCP_2023.csv"
        p.write_text("Session;Ticker;Instrument;Last Price;Volume (MAD);Traded Shares\n"
                     "05/11/2026;ALM;Aluminium;100;100000;1000\n")
        with self.assertRaisesRegex(ValueError, "filename says BCP"):
            read_price_file(p)

    def test_identical_repeated_export_deduplicated(self):
        fixture(self.root, 1)
        p = self.root / "data/prices/S00.csv"
        p.with_name("same.csv").write_bytes(p.read_bytes())
        data = load_dataset(self.root, self.cfg)
        self.assertEqual(len(data.frames["S00"]), len(pd.read_csv(p)))

    def test_calendar_month_anchor_not_252_rows(self):
        data = self.dataset(1)
        d = pd.Timestamp("2025-10-31")
        s = snapshot(data, self.cfg, d)
        self.assertEqual(s.loc["S00", "anchor_date"], "2024-10-31")
        f = data.frames["S00"]
        self.assertAlmostEqual(s.loc["S00", "return_12m"], f.loc[d, "close"]/f.loc["2024-10-31", "close"]-1)

    def test_numerical_zero_is_not_a_buy_signal(self):
        data = self.dataset(1)
        date = pd.Timestamp("2025-10-31")
        frame = data.frames["S00"]
        frame.loc[date, "index"] = frame.loc[pd.Timestamp("2024-10-31"), "index"] * (1 + 4e-16)
        table = snapshot(data, self.cfg, date)
        self.assertEqual(table.loc["S00", "signal_score"], 0.)
        self.assertEqual(table.loc["S00", "signal"], 0)

    def test_ewm_matches_direct_weighted_variance(self):
        data = self.dataset(1)
        f = data.frames["S00"]
        r = f.excess_return.dropna().iloc[:210].to_numpy()
        delta = self.cfg.ewm_com / (self.cfg.ewm_com+1)
        w = delta ** np.arange(len(r)-1, -1, -1)
        w /= w.sum()
        mean = np.dot(w, r)
        expected = np.sqrt(self.cfg.annualization*np.dot(w, (r-mean)**2))
        self.assertAlmostEqual(f.volatility.iloc[210], expected, places=10)

    def test_signal_uses_no_future_prices(self):
        original = self.dataset(3)
        cutoff = pd.Timestamp("2025-06-30")
        a = snapshot(original, self.cfg, cutoff)
        for p in (self.root / "data/prices").glob("*.csv"):
            f = pd.read_csv(p)
            f.loc[f.date > str(cutoff.date()), ["close", "open"]] *= 7
            f.to_csv(p, index=False)
        revised = load_dataset(self.root, self.cfg)
        b = snapshot(revised, self.cfg, cutoff)
        assert_frame_equal(a, b)
        ra = simulate(original, self.cfg, make_schedule(original, self.cfg))
        rb = simulate(revised, self.cfg, make_schedule(revised, self.cfg))
        assert_frame_equal(ra.equity.loc[:cutoff], rb.equity.loc[:cutoff])

    def test_new_listing_not_backfilled(self):
        fixture(self.root, 2)
        p = self.root / "data/prices/S01.csv"
        f = pd.read_csv(p)
        f[f.date >= "2025-02-01"].to_csv(p, index=False)
        data = load_dataset(self.root, self.cfg)
        self.assertTrue(pd.isna(data.frames["S01"].close.iloc[0]))
        s = snapshot(data, self.cfg, pd.Timestamp("2025-10-31"))
        self.assertFalse(s.loc["S01", "eligible"])
        self.assertIn("less_than_12_months", s.loc["S01", "reason"])

    def test_partial_month_is_not_rebalance(self):
        data = self.dataset(1)
        ends = complete_month_ends(data.calendar)
        self.assertEqual(ends[-1], pd.Timestamp("2025-10-31"))
        self.assertNotIn(pd.Timestamp("2025-11-10"), ends)

    def test_twelve_equities_and_constraints(self):
        data = self.dataset(12)
        cfg = replace(self.cfg, max_sector_weight=.30)
        table = valid_table(list(data.frames))
        weights = allocate(table, cfg)
        self.assertEqual(len(weights), 12)
        self.assertGreater((weights > 0).sum(), 2)
        self.assertLessEqual(weights.max(), cfg.target_max_stock_weight + 1e-12)
        self.assertLessEqual(weights.sum(), cfg.max_gross_weight+1e-12)
        for sector, group in table.groupby("sector"):
            self.assertLessEqual(weights.loc[group.index].sum(), cfg.max_sector_weight+1e-12)
        date = pd.Timestamp("2025-10-31")
        result = simulate(data, cfg, {date: table})
        self.assertGreater(result.trades.ticker.nunique(), 2)
        self.assertTrue((result.equity.cash >= -1e-8).all())
        self.assertTrue((result.positions.shares >= 0).all())

    def test_negative_sign_is_cash_not_redistributed(self):
        t = valid_table([f"S{i}" for i in range(20)])
        t.loc[t.index[:10], "excess_return_12m"] = -.1
        t["annual_volatility"] = .8
        w = allocate(t, replace(self.cfg, exposure_mode="paper"))
        self.assertTrue((w.iloc[:10] == 0).all())
        self.assertAlmostEqual(w.iloc[10], .4/.8/20)
        self.assertAlmostEqual(w.sum(), .25)

    def test_original_signed_equation(self):
        self.assertAlmostEqual(paper_weight(-.1, .2, 5), -.4)
        self.assertAlmostEqual(paper_weight(.1, .4, 10), .1)

    def test_redistribution_reaches_feasible_target_and_keeps_caps(self):
        tickers = [f"S{i}" for i in range(20)]
        t = valid_table(tickers)
        t.loc[t.index[:10], "excess_return_12m"] = -.1
        t["annual_volatility"] = .8
        w = allocate(t, self.cfg)
        self.assertTrue((w.iloc[:10] == 0).all())
        self.assertAlmostEqual(w.sum(), .70)
        self.assertLessEqual(w.max(), .08+1e-12)
        self.assertTrue(all(w.loc[g.index].sum() <= .30+1e-12 for _, g in t.groupby("sector")))
        small = allocate(valid_table(["A", "B"]), self.cfg)
        self.assertAlmostEqual(small.sum(), .16)  # 70% impossible with only two eligible stocks

    def test_liquid_universe_is_ranked_at_decision_without_momentum_selection(self):
        data = self.dataset(42)
        cut = pd.Timestamp("2025-10-31")
        a = snapshot(data, self.cfg, cut)
        self.assertEqual(a.eligible.sum(), 40)
        self.assertEqual((a.reason == "outside_top_liquid_universe").sum(), 2)
        # The two excluded names have the least trailing value, irrespective of sign.
        ranked = a.sort_values("average_volume_mad", ascending=False)
        self.assertEqual(set(a.index[a.eligible]), set(ranked.index[:40]))

    def test_no_trade_band_saves_real_fees_without_blocking_exits(self):
        data, schedule = self.short_data([100,100,101,101])
        schedule[data.calendar[2]] = valid_table(["TEST"])
        band = simulate(data, self.cfg, schedule)
        no_band = simulate(data, replace(self.cfg, min_trade_nav_fraction=0,
                                          min_trade_position_fraction=0), schedule)
        self.assertEqual(len(band.trades), 1)
        self.assertEqual(len(no_band.trades), 2)
        self.assertLess(band.trades.fee.sum(), no_band.trades.fee.sum())
        self.assertIn("within_no_trade_band", band.events.event.tolist())
        schedule[data.calendar[2]].loc["TEST", "excess_return_12m"] = -.1
        exited = simulate(data, self.cfg, schedule)
        self.assertEqual(exited.trades.side.tolist(), ["BUY", "SELL"])
        self.assertTrue(rebalance_needed(1, 0, 100, 1_000_000, self.cfg))

    def test_signal_variants_have_explicit_windows_without_future_dependency(self):
        data = self.dataset(1)
        date = pd.Timestamp("2025-10-31")
        old = snapshot(data, replace(self.cfg, signal_mode="single"), date)
        six = snapshot(data, replace(self.cfg, signal_mode="single", lookback_months=6), date)
        blend_cfg = replace(self.cfg, signal_mode="blend_3_6_12")
        blend = snapshot(data, blend_cfg, date)
        skip = snapshot(data, replace(self.cfg, signal_mode="12-1"), date)
        self.assertEqual(old.loc["S00", "anchor_date"], "2024-10-31")
        self.assertEqual(six.loc["S00", "anchor_date"], "2025-04-30")
        self.assertIn("return_6m", six.columns)
        self.assertNotIn("return_12m", six.columns)
        self.assertEqual(skip.loc["S00", "window_end_date"], "2025-09-30")
        self.assertIn("return_12m_skip1", skip.columns)
        self.assertAlmostEqual(blend.loc["S00", "signal_score"],
                               np.mean([12*blend.loc["S00",f"excess_return_{m}m"]/m for m in (3,6,12)]))
        f = pd.read_csv(self.root / "data/prices/S00.csv")
        f.loc[f.date > "2025-10-31", ["close", "open"]] *= 10
        f.to_csv(self.root / "data/prices/S00.csv", index=False)
        revised = load_dataset(self.root, self.cfg)
        assert_frame_equal(blend, snapshot(revised, blend_cfg, date))

    def test_no_same_day_fills(self):
        data, schedule = self.short_data([100, 110, 110])
        result = simulate(data, self.cfg, schedule)
        self.assertTrue((result.trades.date > result.trades.decision_date).all())
        self.assertEqual(result.trades.reference_close.iloc[0], 110)
        # Adverse next-day price rise cannot secretly resize upward.
        self.assertLessEqual(result.trades.quantity.iloc[0], result.targets.target_shares.iloc[0])

    def test_no_fills_on_zero_or_unknown_activity(self):
        data, schedule = self.short_data([100, 100, 100], volumes=[10000, 0, 0])
        result = simulate(data, self.cfg, schedule)
        self.assertTrue(result.trades.empty)
        self.assertTrue((result.equity.nav == self.cfg.initial_capital).all())

    def test_participation_cap(self):
        data, schedule = self.short_data([100, 100, 100], volumes=[1000, 1000, 1000])
        result = simulate(data, replace(self.cfg, allow_partial_fills=True), schedule)
        self.assertFalse(result.trades.empty)
        self.assertTrue((result.trades.quantity <= 10).all())

    def test_contest_default_rejects_partial_fills(self):
        data, schedule = self.short_data([100,100,100], volumes=[1000,1000,1000])
        result = simulate(data, self.cfg, schedule)
        self.assertTrue(result.trades.empty)
        self.assertIn("no_fill_all_or_none", result.events.event.tolist())

    def test_fees_and_cash_conservation(self):
        data, schedule = self.short_data([100, 100, 100])
        r = simulate(data, self.cfg, schedule)
        expected = self.cfg.initial_capital - r.trades.fee.sum() - r.trades.slippage_cost.sum()
        self.assertAlmostEqual(r.equity.nav.iloc[-1], expected, places=6)
        for date, group in r.positions.groupby("date"):
            self.assertAlmostEqual(r.equity.loc[date,"nav"], r.equity.loc[date,"cash"]+group.value.sum(), places=6)

    def test_split_does_not_destroy_wealth(self):
        data, schedule = self.short_data([100, 100, 50, 50], splits=[1,1,2,1])
        r = simulate(data, replace(self.cfg, fee_rate=0, slippage_rate=0), schedule)
        self.assertTrue(np.allclose(r.equity.nav, self.cfg.initial_capital))
        self.assertAlmostEqual(data.frames["TEST"]["return"].iloc[2], 0)
        pos = r.positions.set_index("date")
        self.assertEqual(pos.shares.iloc[2], 2*pos.shares.iloc[1])

    def test_dividend_offsets_ex_date_price_drop(self):
        data, schedule = self.short_data([100,100,99,99], dividends=[0,0,1,0])
        r = simulate(data, replace(self.cfg, fee_rate=0, slippage_rate=0), schedule)
        self.assertTrue(np.allclose(r.equity.nav, self.cfg.initial_capital))
        self.assertGreater(r.equity.dividends.sum(), 0)

    def test_external_corporate_actions_adjust_return_and_reject_conflicts(self):
        dates = pd.bdate_range("2025-01-02", periods=3)
        folder = self.root / "data/prices"
        folder.mkdir(parents=True)
        pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "ticker":"EX",
                      "close":[100,100,99], "shares":100_000,
                      "volume_mad":10_000_000}).to_csv(folder / "ex.csv", index=False)
        actions = self.root / "data/actions.csv"
        actions.write_text(f"date,ticker,dividend,split\n{dates[2].date()},EX,1,1\n")
        cfg = replace(self.cfg, corporate_actions_file="data/actions.csv")
        data = load_dataset(self.root, cfg)
        self.assertAlmostEqual(data.frames["EX"].loc[dates[2], "return"], 0)
        f = pd.read_csv(folder / "ex.csv")
        f["dividend"] = [0,0,2]
        f.to_csv(folder / "ex.csv", index=False)
        with self.assertRaisesRegex(ValueError, "Conflicting dividend"):
            load_dataset(self.root, cfg)

    def test_jump_breach_logged_not_retroactively_fixed(self):
        data, schedule = self.short_data([100,100,200,200])
        r = simulate(data, replace(self.cfg, fee_rate=0, slippage_rate=0), schedule)
        self.assertTrue(r.equity.weight_breach.iloc[2])
        self.assertIn("close_weight_breach", r.events.event.tolist())
        sells = r.trades[r.trades.side == "SELL"]
        self.assertEqual(sells.date.iloc[0], data.calendar[3])

    def test_daily_guard_trims_winner_before_hard_cap(self):
        data, schedule = self.short_data([100, 100, 120, 130, 130])
        safe = simulate(data, self.cfg, schedule)
        late = simulate(data, replace(self.cfg, preemptive_trim_weight=.0999), schedule)
        self.assertIn("preemptive_trim_queued", safe.events.event.tolist())
        self.assertEqual(safe.trades.side.tolist()[:2], ["BUY", "SELL"])
        self.assertEqual(safe.equity.weight_breach.sum(), 0)
        self.assertGreater(late.equity.weight_breach.sum(), 0)
        self.assertTrue(rebalance_needed(930, 800, 100, 1_000_000, self.cfg))

    def test_cash_benchmark_uses_available_rate(self):
        p = self.root / "rates.csv"
        p.write_text("date,annual_rate\n2025-01-01,0.03\n2025-01-03,0.90\n")
        cal = pd.date_range("2025-01-01", "2025-01-05")
        idx = load_benchmark(p, cal)
        self.assertAlmostEqual(idx.iloc[2], 1.03**(2/365))
        self.assertAlmostEqual(idx.iloc[3]/idx.iloc[2], 1.90**(1/365))

    def test_missing_benchmark_rejected_in_total_return_mode(self):
        with self.assertRaisesRegex(ValueError, "MAD cash benchmark"):
            replace(self.cfg, return_mode="total_return", corporate_actions_verified=True).validate()

    def test_overlay_uses_previous_variance_and_frozen_training(self):
        dates = pd.bdate_range("2022-01-03", "2024-11-08")
        rng = np.random.default_rng(6)
        r = pd.Series(rng.normal(.0004,.01,len(dates)), index=dates)
        r.iloc[0] = 0
        ref = SimpleNamespace(equity=pd.DataFrame({"return": r}))
        sched = {d: None for d in complete_month_ends(dates)}
        cfg = replace(self.cfg, vm_calibration_months=6, vm_mode="legacy")
        a = build_overlay(ref, pd.Series(1., index=dates), sched, cfg)
        self.assertIsNotNone(a.constant)
        cutoff = pd.Timestamp("2023-06-30")
        altered = r.copy()
        altered.loc[altered.index > cutoff] *= 10
        b = build_overlay(SimpleNamespace(equity=pd.DataFrame({"return":altered})), pd.Series(1.,index=dates), sched,cfg)
        self.assertEqual(a.constant, b.constant)
        assert_frame_equal(a.history[a.history.signal_date<=cutoff], b.history[b.history.signal_date<=cutoff])
        history = a.history.set_index("signal_date")
        m = r[r.index.to_period("M")==cutoff.to_period("M")]
        rv = ((m-m.mean())**2).sum()
        self.assertAlmostEqual(history.loc[cutoff,"reference_variance"], rv)
        self.assertAlmostEqual(history.loc[cutoff,"multiplier"], min(1,a.constant/rv))

    def test_target_vol_flat_reference_does_not_increase_exposure(self):
        dates = pd.bdate_range("2023-01-02", "2024-11-08")
        ref = SimpleNamespace(equity=pd.DataFrame({"return": np.zeros(len(dates))}, index=dates))
        sched = {d: None for d in complete_month_ends(dates)}
        cfg = replace(self.cfg, vm_mode="target_vol", vm_max_multiplier=2.)
        overlay = build_overlay(ref, pd.Series(1., index=dates), sched, cfg)
        self.assertEqual(set(overlay.multipliers.values()), {1.})
        self.assertIn("flat_reference_keep_base", overlay.history.status.tolist())

    def test_inverse_variance_not_inverse_volatility(self):
        self.assertAlmostEqual(managed_factor(.02, .04, .01), .005)
        self.assertAlmostEqual(managed_factor(.02, .01, .01), .02)

    def test_pending_orders_block_trade_differences(self):
        data = self.dataset(1)
        date = pd.Timestamp("2025-10-31")
        table = snapshot(data, self.cfg, date)
        p = self.root / "account.json"
        p.write_text(json.dumps({"as_of":"2025-11-10","cash_mad":1_000_000,"positions":{},
                                 "pending_orders":[{"ticker":"S00","quantity":5}]}))
        sheet, info = target_worksheet(data, self.cfg, table, account_file=p)
        self.assertTrue(sheet.quantity_change.isna().all())
        self.assertEqual(info["pending_orders_count"], 1)

    def test_order_slicing_fills_across_sessions_within_volume_cap(self):
        # 20,000 shares/day -> 200-share daily cap; target is far larger than one day's cap.
        n = 9
        vols = np.repeat(20_000, n)
        data, schedule = self.short_data([100.] * n, volumes=vols)
        sliced = replace(self.cfg, order_slicing=True, slicing_ttl_sessions=10)
        r = simulate(data, sliced, schedule)
        goal = int(r.targets.target_shares.iloc[0])
        self.assertGreater(goal, 200)
        self.assertGreaterEqual(len(r.trades), 3)
        self.assertTrue((r.trades.quantity <= 200).all())
        self.assertEqual(int(r.positions.shares.iloc[-1]), min(goal, 200 * (n - 1)))
        self.assertTrue((r.equity.cash >= -1e-8).all())
        # Default all-or-none behaviour is unchanged: the same order never fills.
        plain = simulate(data, self.cfg, schedule)
        self.assertTrue(plain.trades.empty)
        self.assertIn("no_fill_all_or_none", plain.events.event.tolist())

    def test_order_slicing_expires_remainder_after_ttl(self):
        data, schedule = self.short_data([100.] * 9, volumes=np.repeat(2_000, 9))  # 20-share cap
        r = simulate(data, replace(self.cfg, order_slicing=True, slicing_ttl_sessions=3), schedule)
        self.assertIn("order_expired", r.events.event.tolist())
        self.assertLessEqual(int(r.positions.shares.iloc[-1]), 20 * 3)

    def test_breadth_scaling_reduces_target_with_few_positive_names(self):
        t = valid_table([f"S{i}" for i in range(20)])
        t.loc[t.index[10:], "excess_return_12m"] = -.1   # 10 of 20 positive
        flat = allocate(t, self.cfg)
        scaled = allocate(t, replace(self.cfg, breadth_scaling="linear"))
        self.assertAlmostEqual(flat.sum(), .70)
        self.assertAlmostEqual(scaled.sum(), .35)
        self.assertLessEqual(scaled.max(), .08 + 1e-12)
        self.assertTrue((scaled.iloc[10:] == 0).all())
        # Full breadth leaves the target unchanged.
        full = valid_table([f"S{i}" for i in range(20)])
        self.assertAlmostEqual(allocate(full, replace(self.cfg, breadth_scaling="linear")).sum(), .70)

    def test_breadth_scaling_value_validated(self):
        with self.assertRaisesRegex(ValueError, "breadth_scaling"):
            replace(self.cfg, breadth_scaling="quadratic").validate()

    def test_liquidity_floor_blocks_thin_stock_for_an_80k_position(self):
        self.assertEqual(self.cfg.min_average_volume_mad, 1_000_000)
        data = self.dataset(1)
        date = pd.Timestamp("2025-10-31")
        f = data.frames["S00"]
        f["average_volume_mad"] = 500_000.
        s = snapshot(data, self.cfg, date)
        self.assertFalse(s.loc["S00", "eligible"])
        self.assertIn("low_traded_value", s.loc["S00", "reason"])
        f["average_volume_mad"] = 2_000_000.
        self.assertTrue(snapshot(data, self.cfg, date).loc["S00", "eligible"])

    def test_french_export_headers_are_read(self):
        import tempfile, importlib.util
        from casa_momentum.data import read_price_file
        text = ("Séance;Ticker;Instrument;Ouverture;Dernier Cours;Plus Haut;Plus Bas;Volume (MAD);"
                "Titres Échangés;Nb Transactions;Capitalisation\n"
                "11/09/2026;ATW  ;ATTIJARIWAFA BANK;706.10;705.20;709.00;705.20;27776582.90;39315;99;1.0\n"
                "10/09/2026;ATW  ;ATTIJARIWAFA BANK;708.60;706.50;710.10;706.50;4674878.40;6597;38;1.0\n")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "Cours_ATW__2026-09-10_2026-09-11.csv"
            path.write_text(text, encoding="utf-8-sig")
            f = read_price_file(path)
        self.assertEqual(set(f.ticker), {"ATW"})
        latest = f.sort_values("date").iloc[-1]
        self.assertEqual(float(latest.close), 705.2)
        self.assertEqual(int(latest.shares), 39315)

    def _merge_module(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_exports", Path(__file__).resolve().parents[1] / "scripts/merge_exports.py")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return mod

    def _export(self, d, name, ticker, rows):
        head = "Session;Ticker;Instrument;Opening;Last Price;High;Low;Volume (MAD);Traded Shares;Number of Trades;Market Capitalization\n"
        lines = "".join(f"{dt};{ticker}  ;X;{c};{c};{c};{c};{v};10;1;1.0\n" for dt, c, v in rows)
        p = Path(d) / name; p.write_text(head + lines, encoding="utf-8-sig"); return p

    def test_merge_exports_combines_overlap_and_detects_conflicts(self):
        import tempfile
        m = self._merge_module()
        with tempfile.TemporaryDirectory() as d:
            a = self._export(d, "a.csv", "ATW", [("03/01/2026", 102, 1000), ("02/01/2026", 101, 900)])
            b = self._export(d, "b.csv", "ATW", [("05/01/2026", 104, 700), ("03/01/2026", 102, 1000)])
            merged, rep = m.merge([a, b])
            self.assertEqual(len(merged), 3)
            self.assertEqual(rep["overlap_sessions"], 1)
            self.assertEqual(rep["conflicts"], [])
            self.assertTrue(merged.index.is_monotonic_decreasing)
            bad = self._export(d, "c.csv", "ATW", [("03/01/2026", 999, 1000)])
            _, rep2 = m.merge([a, bad])
            self.assertTrue(any(c[1] == "Last Price" for c in rep2["conflicts"]))

    def test_merge_exports_rejects_different_tickers(self):
        import tempfile
        m = self._merge_module()
        with tempfile.TemporaryDirectory() as d:
            a = self._export(d, "a.csv", "ATW", [("02/01/2026", 101, 900)])
            b = self._export(d, "b.csv", "ALM", [("03/01/2026", 102, 1000)])
            with self.assertRaisesRegex(ValueError, "different tickers"):
                m.merge([a, b])


if __name__ == "__main__":
    unittest.main()
