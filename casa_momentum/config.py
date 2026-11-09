from dataclasses import dataclass, asdict
from pathlib import Path
import json
import math


@dataclass(frozen=True)
class Config:
    prices_dir: str = "data/prices"
    corporate_actions_file: str | None = None
    metadata_file: str | None = "data/universe.csv"
    cash_rates_file: str | None = None
    calendar_file: str | None = None
    output_dir: str = "outputs"
    as_of: str | None = None
    holdout_start: str | None = None
    return_mode: str = "price_proxy"
    corporate_actions_verified: bool = False
    initial_capital: float = 1_000_000
    lookback_months: int = 12
    signal_mode: str = "single"
    exposure_mode: str = "redistribute"
    breadth_scaling: str = "off"
    target_invested_weight: float = .70
    max_liquid_universe_size: int = 40
    min_trade_nav_fraction: float = .01
    min_trade_position_fraction: float = .25
    preemptive_trim_weight: float = .092
    annualization: int = 261
    ewm_com: float = 60
    min_vol_observations: int = 180
    vol_floor: float = .10
    instrument_risk_target: float = .40
    target_max_stock_weight: float = .08
    hard_max_stock_weight: float = .10
    max_sector_weight: float = .30
    max_gross_weight: float = .95
    liquidity_sessions: int = 60
    min_traded_fraction: float = .80
    min_average_volume_mad: float = 1_000_000
    min_history_coverage: float = .95
    max_stale_sessions: int = 3
    max_anchor_gap_days: int = 7
    large_return_flag: float = .25
    fee_rate: float = .0077
    slippage_rate: float = .001
    participation_rate: float = .01
    allow_partial_fills: bool = False
    order_slicing: bool = False
    slicing_ttl_sessions: int = 10
    order_ttl_sessions: int = 5
    cash_annual_yield: float = 0.0
    vm_calibration_months: int = 12
    vm_variance_floor: float = .000001
    vm_max_multiplier: float = 1.0
    vm_mode: str = "off"
    vm_target_annual_vol: float = .15
    run_cost_sensitivity: bool = True

    def validate(self):
        for name, value in asdict(self).items():
            if isinstance(value, (float, int)) and not math.isfinite(value):
                raise ValueError(f"Non-finite config: {name}")
        for name in ("initial_capital", "lookback_months", "annualization", "ewm_com",
                     "min_vol_observations", "vol_floor", "instrument_risk_target",
                     "liquidity_sessions", "order_ttl_sessions", "vm_variance_floor"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 < self.target_max_stock_weight <= self.hard_max_stock_weight <= .10:
            raise ValueError("Require 0 < target cap <= hard cap <= 10%")
        if not self.target_max_stock_weight < self.preemptive_trim_weight < self.hard_max_stock_weight:
            raise ValueError("Preemptive trim must sit between target and hard stock caps")
        for name in ("max_sector_weight", "max_gross_weight", "participation_rate",
                     "min_traded_fraction", "min_history_coverage"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in (0,1]")
        if not 0 < self.target_invested_weight <= self.max_gross_weight:
            raise ValueError("target_invested_weight must be within (0,max_gross_weight]")
        if self.max_liquid_universe_size <= 0:
            raise ValueError("max_liquid_universe_size must be positive")
        if not 0 <= self.min_trade_nav_fraction <= 1 or not 0 <= self.min_trade_position_fraction <= 1:
            raise ValueError("Trade-band fractions must be in [0,1]")
        if self.vm_max_multiplier <= 0 or self.vm_target_annual_vol <= 0:
            raise ValueError("Volatility limits must be positive")
        if self.signal_mode not in {"single", "blend_3_6_12", "12-1"}:
            raise ValueError("signal_mode must be single, blend_3_6_12 or 12-1")
        if self.exposure_mode not in {"paper", "redistribute"}:
            raise ValueError("exposure_mode must be paper or redistribute")
        if self.breadth_scaling not in {"off", "linear"}:
            raise ValueError("breadth_scaling must be off or linear")
        if self.slicing_ttl_sessions <= 0:
            raise ValueError("slicing_ttl_sessions must be positive")
        if self.vm_mode not in {"off", "legacy", "target_vol"}:
            raise ValueError("vm_mode must be off, legacy or target_vol")
        if self.holdout_start:
            from datetime import date
            try:
                date.fromisoformat(self.holdout_start)
            except ValueError as error:
                raise ValueError("holdout_start must be an ISO date") from error
        if not 0 <= self.fee_rate < 1 or not 0 <= self.slippage_rate < 1:
            raise ValueError("Invalid trading costs")
        if self.vm_calibration_months < 3:
            raise ValueError("At least 3 calibration pairs required (12 default)")
        if self.cash_annual_yield <= -1 or self.max_stale_sessions < 0 or self.max_anchor_gap_days < 0:
            raise ValueError("Invalid cash yield or staleness settings")
        if self.return_mode not in {"price_proxy", "total_return"}:
            raise ValueError("return_mode must be price_proxy or total_return")
        if self.return_mode == "total_return" and not self.corporate_actions_verified:
            raise ValueError("Verify a complete dividend/split history before total_return mode")
        if self.return_mode == "total_return" and not self.cash_rates_file:
            raise ValueError("Supply a MAD cash benchmark for total_return mode")


def load_config(path: Path) -> Config:
    obj = Config(**json.loads(path.read_text(encoding="utf-8-sig")))
    obj.validate()
    return obj
