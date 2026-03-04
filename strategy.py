"""Amplitude Strategy — internalized from engine_v2/_amplitude_strategy.py.

Contains the core strategy logic used by the hourly paper trading system:
- Swing detection (hourly rolling window)
- Consolidation checking
- Breakout entry conditions
- TP/SL calculation
- Level loading from coins.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .models import Candle, AmplitudeLevel


# ---------------------------------------------------------------------------
# JSON level loader
# ---------------------------------------------------------------------------

def _coins_json_path() -> Path:
    """Resolve coins.json path — configurable via COINS_JSON_PATH env var."""
    env = os.environ.get("COINS_JSON_PATH")
    if env:
        return Path(env)
    # 1. Check package directory (standalone deployment)
    local = Path(__file__).parent / "coins.json"
    if local.exists():
        return local
    # 2. Fall back to engine_v2 (running from duo project)
    return Path(__file__).parent.parent / "backtest" / "engine_v2" / "coins.json"


def _parse_levels(raw: list[dict]) -> list[AmplitudeLevel]:
    """Convert raw JSON dicts to AmplitudeLevel objects."""
    levels: list[AmplitudeLevel] = []
    for r in raw:
        levels.append(AmplitudeLevel(
            key=r["key"],
            name=r["name"],
            amplitude=float(r["amplitude"]),
            confirm=float(r["confirm"]),
            take_profit_pct=float(r["take_profit_pct"]),
            leverage=float(r["leverage"]),
            stop_loss_pct=float(r["stop_loss_pct"]),
            invest_ratio=float(r["invest_ratio"]),
            daily_amp_threshold=r.get("daily_amp_threshold"),
            hourly_amp_threshold=r.get("hourly_amp_threshold"),
            hourly_count_threshold=r.get("hourly_count_threshold"),
        ))
    return levels


def load_levels(key: str) -> list[AmplitudeLevel]:
    """Load amplitude levels from coins.json.

    *key* can be:
      - A symbol (e.g. 'ETHUSDT') → reads inline ``levels`` array
      - A template name (e.g. 'eth', 'sol', 'btc') → reads from ``_templates``

    Raises KeyError if nothing matches.
    """
    coins_path = _coins_json_path()
    with open(coins_path, encoding="utf-8") as f:
        data = json.load(f)

    # Try as symbol first (inline levels)
    sym = key.upper()
    if sym in data and isinstance(data[sym], dict) and "levels" in data[sym]:
        return _parse_levels(data[sym]["levels"])

    # Try as template name
    templates = data.get("_templates", {})
    if key in templates:
        return _parse_levels(templates[key])

    raise KeyError(f"No levels found for {key!r} in {coins_path}")


# ---------------------------------------------------------------------------
# Unified strategy class
# ---------------------------------------------------------------------------

class AmplitudeStrategy:
    """Unified amplitude strategy for hourly model.

    Only includes methods needed by the live trading state machine:
    - detect_swing_hourly(): swing detection in 72h rolling window
    - check_consolidation(): narrow range check
    - should_enter_long/short(): breakout confirmation
    - calculate_tp_sl(): take-profit/stop-loss prices
    """

    def __init__(
        self,
        model: str = "hourly",
        levels: Optional[list[AmplitudeLevel]] = None,
        cooling_period_hours: int = 10,
        consolidation_hours: int = 4,
        consolidation_threshold: float = 1.3,
        cycle_days: int = 15,
        weekly_lookback_days: int = 7,
        hourly_lookback: int = 72,
    ):
        self.model = model
        # Sort descending so the largest level is matched first
        self.levels: list[AmplitudeLevel] = sorted(
            levels or load_levels("eth"),
            key=lambda x: x.amplitude,
            reverse=True,
        )
        self.level_map: dict[str, AmplitudeLevel] = {lv.key: lv for lv in self.levels}

        self.cooling_period_hours = cooling_period_hours
        self.consolidation_hours = consolidation_hours
        self.consolidation_threshold = consolidation_threshold
        self.cycle_days = cycle_days
        self.weekly_lookback_days = weekly_lookback_days
        self.hourly_lookback = hourly_lookback

    # ------------------------------------------------------------------
    # Hourly model — rolling 72h window swing detection
    # ------------------------------------------------------------------

    def detect_swing_hourly(
        self,
        window: list[Candle],
        daily_amp: Optional[float] = None,
    ) -> Optional[tuple[str, str, float]]:
        """Detect swing completion using a rolling hourly window.

        Returns (level_key, direction, swing_pct) or None.
        """
        if len(window) < self.hourly_lookback:
            return None

        current_price = window[-1].close
        max_price = max(c.high for c in window)
        min_price = min(c.low  for c in window)

        up_swing     = (max_price - min_price) / min_price * 100
        down_from_hi = (max_price - current_price) / max_price * 100
        up_from_lo   = (current_price - min_price) / min_price * 100

        for lv in self.levels:     # largest first (huge → ... → micro)
            matched = False

            if up_swing >= lv.amplitude:
                matched = True

            if lv.daily_amp_threshold and daily_amp is not None:
                if daily_amp >= lv.daily_amp_threshold:
                    matched = True

            if lv.hourly_amp_threshold:
                if any(c.amplitude >= lv.hourly_amp_threshold for c in window):
                    matched = True

            if lv.hourly_count_threshold:
                cnt = lv.hourly_count_threshold["count"]
                amp = lv.hourly_count_threshold["amp"]
                if sum(1 for c in window if c.amplitude >= amp) >= cnt:
                    matched = True

        # solr1 original: check outside loop — uses last iterated level (smallest)
        if matched:
            if down_from_hi <= up_from_lo:
                return lv.key, "up", up_swing
            else:
                return lv.key, "down", up_swing

        return None

    # ------------------------------------------------------------------
    # Shared — consolidation + entry/exit helpers
    # ------------------------------------------------------------------

    def check_consolidation(self, recent: list[Candle]) -> bool:
        """Return True if the last N candles form a tight consolidation."""
        if len(recent) < self.consolidation_hours:
            return False
        window = recent[-self.consolidation_hours:]
        w_high = max(c.high for c in window)
        w_low  = min(c.low  for c in window)
        w_amp  = (w_high - w_low) / w_low * 100 if w_low > 0 else 999.0
        return w_amp < self.consolidation_threshold

    def breakout_pct(self, current_price: float, base_price: float) -> float:
        return (current_price - base_price) / base_price * 100

    def should_enter_long(self, change_pct: float, level_key: str) -> bool:
        return change_pct >= self.level_map[level_key].confirm

    def should_enter_short(self, change_pct: float, level_key: str) -> bool:
        return change_pct <= -self.level_map[level_key].confirm

    def calculate_tp_sl(
        self, entry_price: float, direction: str, level_key: str
    ) -> tuple[float, float]:
        lv = self.level_map[level_key]
        if direction == "long":
            return entry_price * (1 + lv.take_profit_pct / 100), entry_price * (1 - lv.stop_loss_pct / 100)
        return entry_price * (1 - lv.take_profit_pct / 100), entry_price * (1 + lv.stop_loss_pct / 100)

    def invest_amount(self, capital: float, level_key: str) -> float:
        return capital * self.level_map[level_key].invest_ratio
