"""Hourly Data Collector — fetch 1H klines from Binance REST API.

Converts Binance kline data to internal Candle dataclass for seamless
strategy reuse.  Maintains per-symbol sliding windows in memory.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from .models import Candle
from .kline_client import BinanceKlineClient, RawKline

logger = logging.getLogger(__name__)

# Maximum 1H bars to keep in memory per symbol (≈ 100 bars = 4+ days)
MAX_HISTORY = 200


def _kline_to_candle(k: RawKline) -> Candle:
    """Convert a RawKline to Candle."""
    return Candle(
        open_time=datetime.fromtimestamp(k.open_time / 1000, tz=timezone.utc),
        open=k.open,
        high=k.high,
        low=k.low,
        close=k.close,
        volume=k.volume,
    )


class HourlyDataCollector:
    """Collect 1H klines from Binance Futures REST API.

    - On startup: fetch last ``initial_bars`` 1H bars per symbol.
    - Each hour: fetch the latest completed 1H bar.
    """

    def __init__(
        self,
        symbols: list[str],
        client: BinanceKlineClient,
        initial_bars: int = 100,
    ) -> None:
        self.symbols = [s.upper() for s in symbols]
        self._client = client
        self._initial_bars = initial_bars
        # symbol → list[Candle] ordered by open_time ascending
        self._windows: dict[str, list[Candle]] = {s: [] for s in self.symbols}

    # ------------------------------------------------------------------
    # Startup — bulk fetch history
    # ------------------------------------------------------------------

    async def fetch_all_history(self) -> None:
        """Fetch initial history for every symbol (concurrently, batched)."""
        batch_size = 5
        for i in range(0, len(self.symbols), batch_size):
            batch = self.symbols[i : i + batch_size]
            tasks = [self._fetch_history_one(s) for s in batch]
            await asyncio.gather(*tasks)
            if i + batch_size < len(self.symbols):
                await asyncio.sleep(0.5)  # rate-limit courtesy

    async def _fetch_history_one(self, symbol: str) -> None:
        """Fetch last N 1H bars for one symbol."""
        try:
            klines = await self._client.get_klines(
                symbol=symbol,
                interval="1h",
                limit=self._initial_bars,
            )
            candles = [_kline_to_candle(k) for k in klines]
            # Drop the last bar if it is still forming (close_time in future)
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            if klines and klines[-1].close_time > now_ms:
                candles = candles[:-1]
            self._windows[symbol] = candles
            logger.info(
                "📥 %s: loaded %d 1H bars (latest %s)",
                symbol,
                len(candles),
                candles[-1].open_time.strftime("%m-%d %H:%M") if candles else "—",
            )
        except Exception as e:
            logger.error("❌ %s: failed to fetch history: %s", symbol, e)

    # ------------------------------------------------------------------
    # Incremental — fetch latest completed bar
    # ------------------------------------------------------------------

    async def fetch_all_latest(self) -> dict[str, Candle | None]:
        """Fetch the latest completed 1H bar for all symbols.

        Returns dict of symbol → new Candle (or None on error).
        """
        results: dict[str, Candle | None] = {}
        batch_size = 10
        for i in range(0, len(self.symbols), batch_size):
            batch = self.symbols[i : i + batch_size]
            tasks = [self._fetch_latest_one(s) for s in batch]
            batch_results = await asyncio.gather(*tasks)
            for s, c in zip(batch, batch_results):
                results[s] = c
            if i + batch_size < len(self.symbols):
                await asyncio.sleep(0.3)
        return results

    async def _fetch_latest_one(self, symbol: str) -> Optional[Candle]:
        """Fetch the 2 most recent 1H bars; keep only the completed one."""
        try:
            klines = await self._client.get_klines(
                symbol=symbol, interval="1h", limit=2,
            )
            if not klines:
                return None

            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            # The last kline might still be forming — pick the latest completed
            completed = [k for k in klines if k.close_time <= now_ms]
            if not completed:
                return None

            candle = _kline_to_candle(completed[-1])

            # Append if new
            win = self._windows[symbol]
            if not win or candle.open_time > win[-1].open_time:
                win.append(candle)
                # Trim
                if len(win) > MAX_HISTORY:
                    self._windows[symbol] = win[-MAX_HISTORY:]
            return candle

        except Exception as e:
            logger.error("❌ %s: failed to fetch latest bar: %s", symbol, e)
            return None

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_window(self, symbol: str, n: int | None = None) -> list[Candle]:
        """Return the last *n* candles for *symbol* (or all if n is None)."""
        win = self._windows.get(symbol.upper(), [])
        if n is None:
            return list(win)
        return win[-n:] if len(win) >= n else list(win)

    def get_current_price(self, symbol: str) -> float | None:
        """Return the close price of the latest bar, or None."""
        win = self._windows.get(symbol.upper(), [])
        return win[-1].close if win else None

    def get_latest_candle(self, symbol: str) -> Candle | None:
        """Return the latest candle, or None."""
        win = self._windows.get(symbol.upper(), [])
        return win[-1] if win else None

    def bar_count(self, symbol: str) -> int:
        """Number of bars currently in memory for *symbol*."""
        return len(self._windows.get(symbol.upper(), []))
