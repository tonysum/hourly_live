"""Hourly State Machine — incremental tick-by-tick wrapper around AmplitudeStrategy.

Runs the same searching → cooling → consolidating → monitoring state machine
as ``Runner._run_realtime()`` but driven one candle at a time (for live use).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from .strategy import AmplitudeStrategy, load_levels
from .config import get_config, resolve_strategy
from .models import Candle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal output
# ---------------------------------------------------------------------------

@dataclass
class HourlySignal:
    """A trade signal emitted by the state machine."""
    symbol: str
    direction: str          # "long" | "short"
    level: str              # amplitude level key (e.g. "micro")
    base_price: float
    entry_price: float      # limit-order entry price
    tp_price: float
    sl_price: float
    leverage: float
    invest_ratio: float
    signal_time: datetime


# ---------------------------------------------------------------------------
# Per-symbol state
# ---------------------------------------------------------------------------

@dataclass
class SymbolState:
    """Mutable state for one symbol's state machine."""
    state: str = "searching"
    last_swing_level: Optional[str] = None
    cooling_start_time: Optional[str] = None       # ISO string for serialisation
    base_price: Optional[float] = None
    base_time: Optional[str] = None                # ISO string
    trade_level: Optional[str] = None
    current_market_level: str = "micro"
    pending_cooling: bool = False
    pending_cooling_time: Optional[str] = None     # ISO string

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SymbolState:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Multi-symbol state machine
# ---------------------------------------------------------------------------

class HourlyStateMachine:
    """Manages per-symbol Amplitude Hourly state machines.

    Each call to ``process_tick(symbol, candles)`` advances the state machine
    by one 1H bar and may emit a HourlySignal.
    """

    def __init__(self, symbols_config: dict[str, dict]) -> None:
        """
        Args:
            symbols_config: {symbol: raw_params_from_coins_json, ...}
        """
        self._strategies: dict[str, AmplitudeStrategy] = {}
        self._states: dict[str, SymbolState] = {}
        self._configs: dict[str, dict] = {}

        for symbol, params in symbols_config.items():
            symbol = symbol.upper()
            self._configs[symbol] = params
            try:
                cfg = get_config(symbol)
                strategy = resolve_strategy(cfg, symbol)
            except KeyError:
                logger.warning("⚠️  %s: not found in coin_config, skipping", symbol)
                continue
            self._strategies[symbol] = strategy
            self._states[symbol] = SymbolState()

    @property
    def symbols(self) -> list[str]:
        return list(self._strategies.keys())

    # ------------------------------------------------------------------
    # State persistence helpers
    # ------------------------------------------------------------------

    def get_all_states(self) -> dict[str, dict]:
        """Serialise all symbol states to dicts."""
        return {s: st.to_dict() for s, st in self._states.items()}

    def restore_states(self, data: dict[str, dict]) -> None:
        """Restore symbol states from serialised dicts."""
        for symbol, state_dict in data.items():
            if symbol in self._states:
                self._states[symbol] = SymbolState.from_dict(state_dict)
                logger.info("  🔄 %s: restored state=%s", symbol, state_dict.get("state"))

    def get_state(self, symbol: str) -> SymbolState:
        return self._states[symbol.upper()]

    # ------------------------------------------------------------------
    # Core tick processing
    # ------------------------------------------------------------------

    def process_tick(
        self, symbol: str, candles: list[Candle],
    ) -> Optional[HourlySignal]:
        """Advance the state machine for *symbol* using *candles* (latest window).

        Args:
            candles: The full window of recent 1H candles (should be >= hourly_lookback + 1).

        Returns:
            A HourlySignal if a breakout entry is detected, else None.
        """
        symbol = symbol.upper()
        strategy = self._strategies.get(symbol)
        if strategy is None:
            return None

        state = self._states[symbol]
        lo = strategy.hourly_lookback

        if len(candles) < lo + 1:
            return None

        c = candles[-1]  # Current (latest) bar
        window = candles[-(lo + 1):]
        current_price = c.close

        # ── Update current_market_level ──
        if len(candles) >= lo:
            w_high = max(cc.high for cc in window)
            w_low = min(cc.low for cc in window)
            w_amp = (w_high - w_low) / w_low * 100 if w_low > 0 else 0.0
            state.current_market_level = strategy.levels[-1].key
            for lv in strategy.levels:
                if w_amp >= lv.amplitude:
                    state.current_market_level = lv.key
                    break

        # ── searching ──
        if state.state == "searching":
            if state.pending_cooling:
                # Transition to cooling after a previous trade exit
                state.cooling_start_time = c.open_time.isoformat()
                state.state = "cooling"
                state.pending_cooling = False
                logger.debug("  🕐 %s: post-trade cooling at %s", symbol, c.open_time.strftime("%m-%d %H:%M"))
                return None

            result = strategy.detect_swing_hourly(window)
            if result:
                level_key, direction, swing_pct = result
                state.last_swing_level = level_key
                state.cooling_start_time = c.open_time.isoformat()
                state.state = "cooling"
                logger.info(
                    "  📈 %s: Swing %s [%s] amp=%.1f%%",
                    symbol, direction, level_key, swing_pct,
                )

        # ── cooling ──
        elif state.state == "cooling":
            if state.cooling_start_time:
                cooling_start = datetime.fromisoformat(state.cooling_start_time)
                elapsed_h = (c.open_time - cooling_start).total_seconds() / 3600
                if elapsed_h >= strategy.cooling_period_hours:
                    state.state = "consolidating"
                    logger.debug("  ✅ %s: cooling done, → consolidating", symbol)

        # ── consolidating ──
        elif state.state == "consolidating":
            # Re-detect swing to update level
            result = strategy.detect_swing_hourly(window)
            if result:
                level_key, _, _ = result
                state.last_swing_level = level_key

            recent = candles[-strategy.consolidation_hours:]
            if strategy.check_consolidation(recent):
                state.base_price = current_price
                state.base_time = c.open_time.isoformat()

                # Trade level = max(current_market_level, last_swing_level)
                trade_level = state.current_market_level
                if (state.last_swing_level and
                    strategy.level_map[state.last_swing_level].amplitude >
                    strategy.level_map[trade_level].amplitude):
                    trade_level = state.last_swing_level
                state.trade_level = trade_level

                state.state = "monitoring"
                logger.info(
                    "  📍 %s: consolidation base=$%s level=%s",
                    symbol, f"{current_price:,.4f}", trade_level,
                )

        # ── monitoring ──
        elif state.state == "monitoring":
            if state.base_price is None or state.trade_level is None:
                state.state = "searching"
                return None

            lv = strategy.level_map[state.trade_level]
            change_pct = strategy.breakout_pct(current_price, state.base_price)

            # Timeout check
            if state.base_time:
                base_dt = datetime.fromisoformat(state.base_time)
                hours_since_base = (c.open_time - base_dt).total_seconds() / 3600
                if hours_since_base > strategy.cycle_days * 24:
                    state.state = "searching"
                    state.base_price = None
                    state.base_time = None
                    state.trade_level = None
                    logger.info("  ⏰ %s: monitoring timeout, → searching", symbol)
                    return None

            # Breakout detection
            breakout_level = state.last_swing_level or state.trade_level
            direction: Optional[str] = None
            if strategy.should_enter_long(change_pct, breakout_level):
                direction = "long"
            elif strategy.should_enter_short(change_pct, breakout_level):
                direction = "short"

            if direction is not None:
                confirm_pct = lv.confirm
                if direction == "long":
                    entry_price = state.base_price * (1 + confirm_pct / 100)
                else:
                    entry_price = state.base_price * (1 - confirm_pct / 100)

                tp, sl = strategy.calculate_tp_sl(entry_price, direction, state.trade_level)

                signal = HourlySignal(
                    symbol=symbol,
                    direction=direction,
                    level=state.trade_level,
                    base_price=state.base_price,
                    entry_price=entry_price,
                    tp_price=tp,
                    sl_price=sl,
                    leverage=lv.leverage,
                    invest_ratio=lv.invest_ratio,
                    signal_time=c.open_time,
                )

                logger.info(
                    "  🎯 %s: %s breakout [%s] entry=$%s tp=$%s sl=$%s",
                    symbol, direction.upper(), state.trade_level,
                    f"{entry_price:,.4f}", f"{tp:,.4f}", f"{sl:,.4f}",
                )

                # Reset state — go back to searching
                state.state = "searching"
                state.base_price = None
                state.base_time = None
                state.trade_level = None
                return signal

        return None
