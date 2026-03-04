"""Hourly Paper Trading System.

A self-contained package for paper-trading the Amplitude Hourly strategy.
All dependencies are internalized — no imports from engine_v2 or backend.models.

External dependencies (pip):
    httpx, psycopg2-binary (optional), python-dotenv, rich
"""

from .trader import HourlyPaperTrader
from .state_machine import HourlyStateMachine
from .paper_engine import HourlyPaperEngine
from .store import HourlyPaperStore
from .data_collector import HourlyDataCollector
from .kline_client import BinanceKlineClient

__all__ = [
    "HourlyPaperTrader",
    "HourlyStateMachine",
    "HourlyPaperEngine",
    "HourlyPaperStore",
    "HourlyDataCollector",
    "BinanceKlineClient",
]
