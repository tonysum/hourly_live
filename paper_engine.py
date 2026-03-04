"""Paper Trading Engine — simulated order execution for Hourly strategy.

Handles:
  - Limit order management (add / fill / expire)
  - Position tracking (TP / SL / timeout exit)
  - Capital accounting with leverage
  - Trade history recording
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from .models import Candle
from .state_machine import HourlySignal

logger = logging.getLogger(__name__)

# Pending orders expire after 48 hours
ORDER_EXPIRY_HOURS = 48

# Default fee model (same as backtest)
DEFAULT_COMMISSION_RATE = 0.0005   # 0.05% per side
DEFAULT_SLIPPAGE_PCT = 0.0005     # 0.05% per side


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PendingOrder:
    """A limit order waiting to be filled."""
    symbol: str
    direction: str              # "long" | "short"
    level: str
    entry_price: float
    tp_price: float
    sl_price: float
    leverage: float
    invest_ratio: float
    base_price: float
    created_at: str             # ISO datetime
    size_usdt: float            # capital × invest_ratio
    signal_time: str            # ISO datetime

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> PendingOrder:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class OpenPosition:
    """An active position being monitored."""
    symbol: str
    direction: str
    level: str
    entry_price: float
    entry_time: str             # ISO datetime
    tp_price: float
    sl_price: float
    leverage: float
    size_usdt: float            # margin invested
    position_value: float       # size_usdt × leverage
    qty: float                  # position_value / entry_price
    max_holding_hours: float = 0.0   # 0 = no timeout
    _new_this_tick: bool = False      # skip TP/SL on entry tick

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_new_this_tick", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> OpenPosition:
        d.pop("_new_this_tick", None)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ClosedTrade:
    """A completed trade record."""
    symbol: str
    direction: str
    level: str
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    size_usdt: float
    leverage: float
    pnl: float
    pnl_pct: float
    exit_reason: str            # "tp" | "sl" | "timeout"
    hold_hours: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ClosedTrade:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class HourlyPaperEngine:
    """Simulated trading engine for the Hourly strategy."""

    def __init__(
        self,
        initial_capital: float = 10000.0,
        max_positions: int = 1,
        commission_rate: float = DEFAULT_COMMISSION_RATE,
        slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
    ) -> None:
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.max_positions = max_positions
        self.commission_rate = commission_rate
        self.slippage_pct = slippage_pct

        self.pending_orders: list[PendingOrder] = []
        self.open_positions: list[OpenPosition] = []
        self.trade_history: list[ClosedTrade] = []
        self._dirty = False

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def mark_clean(self) -> None:
        self._dirty = False

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def add_limit_order(self, signal: HourlySignal) -> None:
        """Create a pending limit order from a strategy signal."""
        # Deduplicate: skip if same symbol + direction already pending
        for o in self.pending_orders:
            if o.symbol == signal.symbol and o.direction == signal.direction:
                logger.debug("  📋 %s %s: duplicate order, skipping", signal.symbol, signal.direction)
                return

        # Single-position enforcement
        for p in self.open_positions:
            if p.symbol == signal.symbol:
                logger.debug("  📋 %s: already has open position, skipping", signal.symbol)
                return

        size_usdt = self.capital * signal.invest_ratio

        order = PendingOrder(
            symbol=signal.symbol,
            direction=signal.direction,
            level=signal.level,
            entry_price=signal.entry_price,
            tp_price=signal.tp_price,
            sl_price=signal.sl_price,
            leverage=signal.leverage,
            invest_ratio=signal.invest_ratio,
            base_price=signal.base_price,
            created_at=datetime.now(timezone.utc).isoformat(),
            size_usdt=size_usdt,
            signal_time=signal.signal_time.isoformat(),
        )
        self.pending_orders.append(order)
        self._dirty = True
        logger.info(
            "📋 挂单: %s %s [%s] entry=$%s 金额=$%s",
            signal.symbol, signal.direction.upper(), signal.level,
            f"{signal.entry_price:,.4f}", f"{size_usdt:,.0f}",
        )

    def cancel_expired_orders(self, now: datetime) -> int:
        """Remove orders older than ORDER_EXPIRY_HOURS. Returns count removed."""
        before = len(self.pending_orders)
        remaining = []
        for o in self.pending_orders:
            created = datetime.fromisoformat(o.created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_h = (now - created).total_seconds() / 3600
            if age_h <= ORDER_EXPIRY_HOURS:
                remaining.append(o)
            else:
                logger.info("🗑️ 过期挂单: %s %s (%.0fh)", o.symbol, o.direction, age_h)
        self.pending_orders = remaining
        removed = before - len(remaining)
        if removed > 0:
            self._dirty = True
        return removed

    # ------------------------------------------------------------------
    # Fill checking (called each hour with latest candle)
    # ------------------------------------------------------------------

    def check_fills(self, symbol: str, candle: Candle, now: datetime) -> None:
        """Check if any pending orders for *symbol* are triggered by *candle*."""
        remaining = []
        for order in self.pending_orders:
            if order.symbol != symbol:
                remaining.append(order)
                continue

            triggered = False
            if order.direction == "long" and candle.low <= order.entry_price <= candle.high:
                triggered = True
            elif order.direction == "short" and candle.low <= order.entry_price <= candle.high:
                triggered = True

            if triggered and len(self.open_positions) < self.max_positions:
                # Check capital
                if self.capital < order.size_usdt:
                    logger.warning(
                        "⚠️ %s: 资金不足 需$%s 可用$%s",
                        symbol, f"{order.size_usdt:,.0f}", f"{self.capital:,.0f}",
                    )
                    remaining.append(order)
                    continue

                position_value = order.size_usdt * order.leverage
                qty = position_value / order.entry_price

                pos = OpenPosition(
                    symbol=symbol,
                    direction=order.direction,
                    level=order.level,
                    entry_price=order.entry_price,
                    entry_time=now.isoformat(),
                    tp_price=order.tp_price,
                    sl_price=order.sl_price,
                    leverage=order.leverage,
                    size_usdt=order.size_usdt,
                    position_value=position_value,
                    qty=qty,
                    _new_this_tick=True,
                )
                self.open_positions.append(pos)
                self.capital -= order.size_usdt
                self._dirty = True
                logger.info(
                    "✅ 模拟开仓: %s %s [%s] @$%s 杠杆:%dx 金额:$%s",
                    symbol, order.direction.upper(), order.level,
                    f"{order.entry_price:,.4f}", int(order.leverage), f"{order.size_usdt:,.0f}",
                )
            else:
                remaining.append(order)

        self.pending_orders = remaining

    # ------------------------------------------------------------------
    # Position monitoring (TP / SL / Timeout)
    # ------------------------------------------------------------------

    def check_positions(self, symbol: str, candle: Candle, now: datetime) -> None:
        """Check all open positions for *symbol* for exit conditions."""
        remaining = []
        for pos in self.open_positions:
            if pos.symbol != symbol:
                remaining.append(pos)
                continue

            # Skip TP/SL on the same tick as entry (avoid instant stop-out)
            if pos._new_this_tick:
                pos._new_this_tick = False
                remaining.append(pos)
                continue

            exit_price = None
            exit_reason = None

            if pos.direction == "long":
                if candle.high >= pos.tp_price:
                    exit_price = pos.tp_price
                    exit_reason = "tp"
                elif candle.low <= pos.sl_price:
                    exit_price = pos.sl_price
                    exit_reason = "sl"
            else:  # short
                if candle.low <= pos.tp_price:
                    exit_price = pos.tp_price
                    exit_reason = "tp"
                elif candle.high >= pos.sl_price:
                    exit_price = pos.sl_price
                    exit_reason = "sl"

            # Same-bar TP+SL → conservative: SL wins
            if exit_reason is None:
                hit_tp = hit_sl = False
                if pos.direction == "long":
                    hit_tp = candle.high >= pos.tp_price
                    hit_sl = candle.low <= pos.sl_price
                else:
                    hit_tp = candle.low <= pos.tp_price
                    hit_sl = candle.high >= pos.sl_price
                if hit_tp and hit_sl:
                    exit_price = pos.sl_price
                    exit_reason = "sl"

            # Timeout
            if exit_price is None and pos.max_holding_hours > 0:
                entry_dt = datetime.fromisoformat(pos.entry_time)
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                hold_h = (now - entry_dt).total_seconds() / 3600
                if hold_h >= pos.max_holding_hours:
                    exit_price = candle.close
                    exit_reason = "timeout"

            if exit_price is not None:
                self._close_position(pos, exit_price, exit_reason, now)
            else:
                remaining.append(pos)

        self.open_positions = remaining

    def _close_position(
        self, pos: OpenPosition, exit_price: float, reason: str, now: datetime
    ) -> None:
        """Close a position and record the trade."""
        # PnL calculation
        if pos.direction == "long":
            price_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
        else:
            price_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

        leveraged_pct = price_pct * pos.leverage
        gross_pnl = pos.size_usdt * leveraged_pct / 100

        # Fees (round-trip)
        fee_rate = (self.commission_rate + self.slippage_pct) * 2
        fees = pos.size_usdt * fee_rate
        net_pnl = gross_pnl - fees

        # Return margin + PnL to capital
        self.capital += pos.size_usdt + net_pnl

        entry_dt = datetime.fromisoformat(pos.entry_time)
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
        hold_h = (now - entry_dt).total_seconds() / 3600

        trade = ClosedTrade(
            symbol=pos.symbol,
            direction=pos.direction,
            level=pos.level,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time=now.isoformat(),
            size_usdt=pos.size_usdt,
            leverage=pos.leverage,
            pnl=round(net_pnl, 2),
            pnl_pct=round(leveraged_pct - fee_rate * 100, 2),
            exit_reason=reason,
            hold_hours=round(hold_h, 1),
        )
        self.trade_history.append(trade)
        self._dirty = True

        emoji = "🟢" if net_pnl > 0 else "🔴"
        logger.info(
            "%s 模拟平仓: %s %s [%s] @$%,.4f → $%,.4f 盈亏:$%+,.2f (%s) %.1fh",
            emoji, pos.symbol, pos.direction.upper(), pos.level,
            pos.entry_price, exit_price, net_pnl, reason, hold_h,
        )

    # ------------------------------------------------------------------
    # Stats & queries
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return summary statistics."""
        total = len(self.trade_history)
        wins = [t for t in self.trade_history if t.pnl > 0]
        total_pnl = sum(t.pnl for t in self.trade_history)
        return {
            "initial_capital": self.initial_capital,
            "current_capital": round(self.capital, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / self.initial_capital * 100, 2) if self.initial_capital else 0,
            "total_trades": total,
            "wins": len(wins),
            "losses": total - len(wins),
            "win_rate": round(len(wins) / total * 100, 1) if total > 0 else 0,
            "avg_pnl": round(total_pnl / total, 2) if total > 0 else 0,
            "open_positions": len(self.open_positions),
            "pending_orders": len(self.pending_orders),
        }

    def get_positions_data(self) -> list[dict]:
        """Current open positions."""
        return [p.to_dict() for p in self.open_positions]

    def get_orders_data(self) -> list[dict]:
        """Current pending orders."""
        return [o.to_dict() for o in self.pending_orders]

    # ------------------------------------------------------------------
    # Serialisation (for store layer)
    # ------------------------------------------------------------------

    def to_state_dict(self) -> dict:
        """Serialise all engine state."""
        return {
            "capital": self.capital,
            "pending_orders": [o.to_dict() for o in self.pending_orders],
            "open_positions": [p.to_dict() for p in self.open_positions],
            "trade_history": [t.to_dict() for t in self.trade_history],
        }

    def restore_from_state_dict(self, data: dict) -> None:
        """Restore engine state from serialised dict."""
        self.capital = data.get("capital", self.initial_capital)
        self.pending_orders = [
            PendingOrder.from_dict(o) for o in data.get("pending_orders", [])
        ]
        self.open_positions = [
            OpenPosition.from_dict(p) for p in data.get("open_positions", [])
        ]
        self.trade_history = [
            ClosedTrade.from_dict(t) for t in data.get("trade_history", [])
        ]
        logger.info(
            "📂 恢复引擎状态: 资金=$%s 持仓:%d 挂单:%d 历史:%d",
            f"{self.capital:,.0f}", len(self.open_positions),
            len(self.pending_orders), len(self.trade_history),
        )
