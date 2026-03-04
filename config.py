"""Configuration loader for hourly_live — internalized from engine_v2/coin_config.py.

Reads coins.json to discover active hourly symbols and their parameters.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .strategy import AmplitudeStrategy, load_levels


# ---------------------------------------------------------------------------
# CoinConfig (subset of engine_v2's CoinConfig — only fields we need)
# ---------------------------------------------------------------------------

@dataclass
class CoinConfig:
    """Per-symbol configuration for hourly model."""
    model: str = "hourly"
    strategy: str = "eth"
    cooling_period_hours: int = 10
    consolidation_threshold: float = 1.3
    consolidation_hours: int = 4
    cycle_days: int = 15
    amplitude_levels: Optional[str] = None


# ---------------------------------------------------------------------------
# coins.json path
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


_coins_cache: dict | None = None


def read_coins_json() -> dict:
    """Read and return the full coins.json content (cached after first read)."""
    global _coins_cache
    if _coins_cache is not None:
        return _coins_cache
    p = _coins_json_path()
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        _coins_cache = json.load(f)
    return _coins_cache


# ---------------------------------------------------------------------------
# Load active hourly symbols
# ---------------------------------------------------------------------------

def load_hourly_symbols() -> dict[str, dict]:
    """Load all active hourly symbols from coins.json.

    Returns:
        Dict[symbol, raw_params] for hourly+active entries
    """
    raw = read_coins_json()
    result = {}
    for symbol, params in raw.items():
        if symbol.startswith("_"):
            continue
        if not isinstance(params, dict):
            continue
        if params.get("model") != "hourly":
            continue
        if not params.get("active", False):
            continue
        result[symbol.upper()] = params
    return result


def get_config(symbol: str) -> CoinConfig:
    """Build a CoinConfig for the given symbol from coins.json."""
    raw = read_coins_json()
    sym = symbol.upper()
    if sym not in raw:
        raise KeyError(f"No config for {sym!r} in coins.json")
    p = raw[sym]
    return CoinConfig(
        model=p.get("model", "hourly"),
        strategy=p.get("strategy", "eth"),
        cooling_period_hours=p.get("cooling_period_hours", 10),
        consolidation_threshold=p.get("consolidation_threshold", 1.3),
        consolidation_hours=p.get("consolidation_hours", 4),
        cycle_days=p.get("cycle_days", 15),
        amplitude_levels=p.get("amplitude_levels"),
    )


def resolve_strategy(cfg: CoinConfig, symbol: str | None = None) -> AmplitudeStrategy:
    """Instantiate the strategy object from a CoinConfig."""
    levels = None
    if cfg.amplitude_levels:
        levels = load_levels(cfg.amplitude_levels)
    elif symbol:
        try:
            levels = load_levels(symbol)
        except KeyError:
            pass
    if levels is None:
        levels = load_levels(cfg.strategy)

    return AmplitudeStrategy(
        model=cfg.model,
        levels=levels,
        cooling_period_hours=cfg.cooling_period_hours,
        consolidation_threshold=cfg.consolidation_threshold,
        consolidation_hours=cfg.consolidation_hours,
        cycle_days=cfg.cycle_days,
        hourly_lookback=72,
    )
