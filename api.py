"""Embedded FastAPI status API for hourly paper trading.

Runs inside the trader's asyncio event loop — zero extra processes.
Provides read-only endpoints for remote monitoring.

If `frontend/dist/` exists (built React app), also serves the dashboard UI.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

if TYPE_CHECKING:
    from .trader import HourlyPaperTrader

logger = logging.getLogger(__name__)

app = FastAPI(title="Hourly Paper Trading", version="1.0.0")

# Allow cross-origin requests (local Vite dev server, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Reference to the running trader instance — set by trader.py on startup
_trader: HourlyPaperTrader | None = None
_start_time: datetime | None = None

# Frontend dist directory
_DIST_DIR = Path(__file__).parent / "frontend" / "dist"


def set_trader(trader: HourlyPaperTrader) -> None:
    """Register the running trader instance for API access."""
    global _trader, _start_time
    _trader = trader
    _start_time = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    """Health check."""
    return {
        "service": "hourly-paper-trading",
        "status": "running" if _trader and _trader._running else "stopped",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/status")
async def status():
    """Current engine status + state machine snapshot."""
    if not _trader or not _trader._engine:
        return {"error": "trader not running"}

    stats = _trader._engine.get_stats()
    sm_states = {}
    if _trader._state_machine:
        for sym, state in _trader._state_machine._states.items():
            sm_states[sym] = {
                "state": state.state,
                "trade_level": state.trade_level or "—",
                "base_price": state.base_price,
                "cooling_start": state.cooling_start_time,
                "current_market_level": state.current_market_level,
            }

    now = datetime.now(timezone.utc)
    uptime = (now - _start_time).total_seconds() if _start_time else 0

    return {
        "capital": stats["current_capital"],
        "initial_capital": stats["initial_capital"],
        "total_pnl": stats["total_pnl"],
        "total_pnl_pct": stats["total_pnl_pct"],
        "total_trades": stats["total_trades"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "win_rate": stats["win_rate"],
        "open_positions": stats["open_positions"],
        "pending_orders": stats["pending_orders"],
        "symbols": _trader.symbols,
        "state_machine": sm_states,
        "uptime_seconds": uptime,
        "started_at": _start_time.isoformat() if _start_time else None,
        "time": now.isoformat(),
    }


@app.get("/trades")
async def trades(limit: int = 20):
    """Recent closed trades."""
    if not _trader or not _trader._store:
        return {"error": "trader not running"}
    return {"trades": _trader._store.get_trades(limit=limit)}


@app.get("/signals")
async def signals(limit: int = 20):
    """Recent signals."""
    if not _trader or not _trader._store:
        return {"error": "trader not running"}
    return {"signals": _trader._store.get_signals(limit=limit)}


@app.get("/positions")
async def positions():
    """Current open positions and pending orders."""
    if not _trader or not _trader._engine:
        return {"error": "trader not running"}

    def _safe_iso(val) -> str | None:
        """Serialize datetime or already-string timestamps."""
        if val is None:
            return None
        return val.isoformat() if hasattr(val, "isoformat") else str(val)

    open_pos = []
    for p in _trader._engine.open_positions:
        open_pos.append({
            "symbol": p.symbol,
            "direction": p.direction,
            "level": p.level,
            "entry_price": p.entry_price,
            "tp_price": p.tp_price,
            "sl_price": p.sl_price,
            "size_usdt": p.size_usdt,
            "leverage": p.leverage,
            "entry_time": _safe_iso(p.entry_time),
        })

    pending = []
    for o in _trader._engine.pending_orders:
        pending.append({
            "symbol": o.symbol,
            "direction": o.direction,
            "level": o.level,
            "entry_price": o.entry_price,
            "size_usdt": o.size_usdt,
            "created_at": _safe_iso(o.created_at),
        })

    return {"open_positions": open_pos, "pending_orders": pending}


@app.get("/logs")
async def logs(lines: int = 100, type: str = "all"):
    """Read recent log lines.

    Args:
        lines: Number of tail lines to return (max 500)
        type: "output", "error", or "all"
    """
    lines = min(lines, 500)
    logs_dir = Path(__file__).parent / "logs"
    result = {}

    def _tail(filepath: Path, n: int) -> list[str]:
        if not filepath.exists():
            return []
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
            all_lines = text.strip().split("\n")
            return all_lines[-n:]
        except Exception:
            return []

    if type in ("output", "all"):
        result["output"] = _tail(logs_dir / "output.log", lines)

    if type in ("error", "all"):
        result["error"] = _tail(logs_dir / "error.log", lines)

    return result


# ---------------------------------------------------------------------------
# Static file serving (React SPA)
# ---------------------------------------------------------------------------

if _DIST_DIR.exists():
    # Serve static assets (JS, CSS, images)
    app.mount("/assets", StaticFiles(directory=str(_DIST_DIR / "assets")), name="static")

    # SPA fallback — serve index.html for all non-API routes
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve React SPA for any non-API route."""
        file_path = _DIST_DIR / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(_DIST_DIR / "index.html"))

