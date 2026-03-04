"""Lightweight Binance Futures kline client — no external model dependencies.

Only fetches 1H klines from the public REST API (no authentication needed).
Replaces the heavyweight BinanceFuturesClient dependency for hourly_live.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://fapi.binance.com"


@dataclass
class RawKline:
    """Minimal kline data parsed from Binance REST API."""
    open_time: int       # ms timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int      # ms timestamp


class BinanceKlineClient:
    """Async client for fetching Binance Futures klines.

    Usage:
        async with BinanceKlineClient() as client:
            klines = await client.get_klines("ETHUSDT", "1h", limit=100)
    """

    def __init__(self, base_url: str = BASE_URL, timeout: float = 30.0) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> BinanceKlineClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if not self._client:
            raise RuntimeError("Client not initialized — use 'async with'")
        return self._client

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> list[RawKline]:
        """Fetch kline/candlestick data.

        Args:
            symbol: Trading pair symbol (e.g. "ETHUSDT")
            interval: Kline interval (e.g. "1h")
            limit: Number of klines (max 1500)
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds

        Returns:
            List of RawKline dataclass objects
        """
        params: dict = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        resp = await self.client.get("/fapi/v1/klines", params=params)
        resp.raise_for_status()
        data = resp.json()

        return [
            RawKline(
                open_time=int(k[0]),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
                close_time=int(k[6]),
            )
            for k in data
        ]
