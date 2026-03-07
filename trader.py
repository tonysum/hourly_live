"""Hourly Paper Trader — main orchestrator service.

Ties together data collection, state machine, paper engine, and persistence.
Runs an asyncio event loop that wakes every hour to process new 1H bars.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .config import load_hourly_symbols
from .data_collector import HourlyDataCollector
from .paper_engine import HourlyPaperEngine
from .state_machine import HourlyStateMachine, HourlySignal
from .store import HourlyPaperStore
from .kline_client import BinanceKlineClient

logger = logging.getLogger(__name__)



class HourlyPaperTrader:
    """Main service: runs the Hourly strategy paper trading loop."""

    def __init__(
        self,
        symbols: Optional[list[str]] = None,
        initial_capital: float = 10000.0,
        max_positions: int = 1,
        run_now: bool = False,
        api_port: int = 0,
    ) -> None:
        all_hourly = load_hourly_symbols()

        if symbols:
            # User-specified subset
            self._symbols_config = {
                s.upper(): all_hourly.get(s.upper(), {})
                for s in symbols
                if s.upper() in all_hourly
            }
            if not self._symbols_config:
                logger.error("❌ No matching active hourly symbols found")
                raise ValueError(
                    f"None of {symbols} found in active hourly symbols. "
                    f"Available: {sorted(all_hourly.keys())}"
                )
        else:
            self._symbols_config = all_hourly

        self._initial_capital = initial_capital
        self._max_positions = max_positions
        self._run_now = run_now
        self._api_port = api_port
        self._running = False

        # Components (initialised in start())
        self._client: Optional[BinanceKlineClient] = None
        self._collector: Optional[HourlyDataCollector] = None
        self._state_machine: Optional[HourlyStateMachine] = None
        self._engine: Optional[HourlyPaperEngine] = None
        self._store: Optional[HourlyPaperStore] = None

    @property
    def symbols(self) -> list[str]:
        return sorted(self._symbols_config.keys())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialise components, restore state, and start the hourly loop."""
        sym_list = self.symbols
        logger.info("🚀 Hourly Paper Trader starting with %d symbols", len(sym_list))
        logger.info("  📜 Symbols: %s", ", ".join(sym_list[:10]) + ("..." if len(sym_list) > 10 else ""))

        # -- Binance kline client (no auth needed) --
        self._client = BinanceKlineClient()
        await self._client.__aenter__()

        # -- Components --
        self._collector = HourlyDataCollector(sym_list, self._client)
        self._state_machine = HourlyStateMachine(self._symbols_config)
        self._engine = HourlyPaperEngine(
            initial_capital=self._initial_capital,
            max_positions=self._max_positions,
            max_holding_hours=72.0,
        )
        self._store = HourlyPaperStore()

        # -- PostgreSQL --
        self._store.connect()

        # -- Restore persisted state --
        self._restore_state()

        # -- Fetch historical bars --
        logger.info("📥 Fetching historical 1H bars...")
        await self._collector.fetch_all_history()
        for s in sym_list:
            n = self._collector.bar_count(s)
            if n < 72:
                logger.warning("  ⚠️  %s: only %d bars (need >= 72)", s, n)

        # -- Smart init state machine for symbols without persisted state --
        # (State machine was already restored above; symbols that weren't
        #  persisted will be in "searching" and will self-initialise via
        #  normal tick processing.)
        logger.info("✅ Initialisation complete — starting hourly loop")

        # -- Run --
        self._running = True
        try:
            # Start API server if port specified
            api_task = None
            if self._api_port > 0:
                api_task = asyncio.create_task(self._start_api())
                await asyncio.sleep(0.5)  # Let API server bind

            # Start realtime price refresh loop
            price_task = asyncio.create_task(self._price_refresh_loop())

            # Immediate tick if --now
            if self._run_now:
                logger.info("⚡ --now: 立即执行一次 tick")
                await self._run_one_tick()
            await self._hourly_loop()
        finally:
            price_task.cancel()
            if api_task:
                api_task.cancel()
            await self.stop()

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        self._save_state(force=True)
        if self._store:
            self._store.close()
        if self._client:
            await self._client.__aexit__(None, None, None)
        logger.info("🛑 Hourly Paper Trader stopped")

    # ------------------------------------------------------------------
    # API server
    # ------------------------------------------------------------------

    async def _start_api(self) -> None:
        """Start embedded FastAPI server."""
        try:
            import uvicorn
            from .api import app, set_trader

            set_trader(self)
            config = uvicorn.Config(
                app, host="0.0.0.0", port=self._api_port,
                log_level="warning",
            )
            server = uvicorn.Server(config)
            logger.info("🌐 API server started on http://0.0.0.0:%d", self._api_port)
            await server.serve()
        except Exception as e:
            logger.error("❌ API server failed: %s", e, exc_info=True)

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    async def _run_one_tick(self) -> None:
        """Execute one tick cycle: fetch data, process symbols, save state."""
        logger.info("─" * 60)
        logger.info("⏰ Hourly tick at %s", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))

        # 1. Fetch latest 1H bars
        new_candles = await self._collector.fetch_all_latest()
        fetched = sum(1 for v in new_candles.values() if v is not None)
        logger.info("  📊 Fetched %d/%d new bars", fetched, len(self.symbols))

        # 2. Process each symbol
        signals_count = 0
        for symbol in self.symbols:
            candles = self._collector.get_window(symbol)
            if len(candles) < 10:
                continue

            # State machine tick
            sig = self._state_machine.process_tick(symbol, candles)
            if sig:
                self._handle_signal(sig)
                signals_count += 1

            # Check fills and positions using latest candle
            latest = self._collector.get_latest_candle(symbol)
            if latest:
                now = datetime.now(timezone.utc)
                self._engine.check_fills(symbol, latest, now)
                self._engine.check_positions(symbol, latest, now)

        # 3. Cleanup expired orders
        self._engine.cancel_expired_orders(datetime.now(timezone.utc))

        # 4. Save state (always force — state machine may have changed)
        self._save_state(force=True)

        # 5. Log summary
        stats = self._engine.get_stats()
        logger.info(
            "  💰 Capital: $%s | PnL: $%s (%s%%) | "
            "Trades: %d (W:%d L:%d WR:%s%%) | Pos:%d Orders:%d",
            f"{stats['current_capital']:,.0f}",
            f"{stats['total_pnl']:+,.2f}",
            f"{stats['total_pnl_pct']:.1f}",
            stats["total_trades"], stats["wins"], stats["losses"],
            f"{stats['win_rate']:.0f}",
            stats["open_positions"], stats["pending_orders"],
        )
        if signals_count:
            logger.info("  📡 %d new signals this hour", signals_count)

    async def _hourly_loop(self) -> None:
        """Wait until the top of each hour, then process all symbols."""
        while self._running:
            try:
                # Wait until HH:00:05 (5s after the hour boundary)
                await self._wait_for_next_hour()

                if not self._running:
                    break

                await self._run_one_tick()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("❌ Hourly loop error: %s", e, exc_info=True)
                await asyncio.sleep(30)

    async def _wait_for_next_hour(self) -> None:
        """Sleep until HH:00:05 UTC."""
        now = datetime.now(timezone.utc)
        next_hour = now.replace(minute=0, second=5, microsecond=0)
        if next_hour <= now:
            next_hour += timedelta(hours=1)
        wait = (next_hour - now).total_seconds()
        logger.debug("  ⏳ Next tick in %.0f seconds (%s)", wait, next_hour.strftime("%H:%M:%S"))
        # Sleep in short intervals for graceful shutdown
        while wait > 0 and self._running:
            chunk = min(wait, 10.0)
            await asyncio.sleep(chunk)
            wait -= chunk

    async def _price_refresh_loop(self) -> None:
        """Background task: refresh ticker prices every 30 seconds."""
        while self._running:
            try:
                if self._collector:
                    await self._collector.fetch_realtime_prices()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Price refresh error: %s", e)
            await asyncio.sleep(30)

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _handle_signal(self, signal: HourlySignal) -> None:
        """Process a signal: log, persist, and add limit order."""
        # Persist signal
        if self._store:
            self._store.save_signal(signal)

        # Add limit order to engine
        self._engine.add_limit_order(signal)

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _save_state(self, force: bool = False) -> None:
        """Persist engine + state machine state to PostgreSQL."""
        if not self._store:
            return
        if not force and not self._engine.is_dirty:
            return

        try:
            # Engine state (capital, orders, positions, trades)
            self._store.save_engine_state(self._engine.to_state_dict())

            # State machine states
            self._store.save_state_machine(self._state_machine.get_all_states())

            # Equity snapshot
            self._store.save_equity_snapshot(
                capital=self._engine.capital,
                equity=self._engine.get_equity(),
                positions=len(self._engine.open_positions),
            )

            # Record any new closed trades
            prev_count = getattr(self, "_last_trade_count", 0)
            current_count = len(self._engine.trade_history)
            for trade in self._engine.trade_history[prev_count:]:
                self._store.save_trade(trade.to_dict())
            self._last_trade_count = current_count

            self._engine.mark_clean()
        except Exception as e:
            logger.error("❌ Failed to save state: %s", e)

    def _restore_state(self) -> None:
        """Restore engine + state machine state from PostgreSQL."""
        if not self._store:
            return

        try:
            # Engine state
            engine_data = self._store.load_engine_state()
            if engine_data:
                self._engine.restore_from_state_dict(engine_data)
                self._last_trade_count = len(self._engine.trade_history)

            # State machine
            sm_data = self._store.load_state_machine()
            if sm_data:
                self._state_machine.restore_states(sm_data)
        except Exception as e:
            logger.error("❌ Failed to restore state: %s", e)

    # ------------------------------------------------------------------
    # Query methods (for CLI)
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return current system status."""
        return {
            "symbols": self.symbols,
            "symbol_count": len(self.symbols),
            "states": self._state_machine.get_all_states() if self._state_machine else {},
            "stats": self._engine.get_stats() if self._engine else {},
        }
