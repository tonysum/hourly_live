"""Core data models for hourly_live.

Internalized from engine_v2/models.py — only the types needed by the
hourly paper trading system.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Candle
# ---------------------------------------------------------------------------

@dataclass
class Candle:
    """A single OHLCV candle."""
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def amplitude(self) -> float:
        """(high - low) / low * 100"""
        return (self.high - self.low) / self.low * 100 if self.low > 0 else 0.0

    @property
    def hour_pct(self) -> float:
        """(close - open) / open * 100"""
        return (self.close - self.open) / self.open * 100 if self.open > 0 else 0.0


# ---------------------------------------------------------------------------
# Amplitude level definition
# ---------------------------------------------------------------------------

@dataclass
class AmplitudeLevel:
    """Parameters for one amplitude tier."""
    key: str
    name: str
    amplitude: float           # Window swing threshold (%)
    confirm: float             # Breakout confirmation threshold (%)
    take_profit_pct: float     # Take-profit (%) from entry price
    leverage: float            # Leverage multiplier
    stop_loss_pct: float       # Stop-loss (%) from entry price
    invest_ratio: float        # Fraction of capital to invest

    # Optional extra conditions
    daily_amp_threshold: Optional[float] = None
    hourly_amp_threshold: Optional[float] = None
    hourly_count_threshold: Optional[dict] = None   # {'count': N, 'amp': X}
