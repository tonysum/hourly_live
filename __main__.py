"""CLI entry point for hourly paper trading.

Usage:
    python -m backend.hourly_live                   # start paper trading
    python -m backend.hourly_live start             # same
    python -m backend.hourly_live status            # show current status
    python -m backend.hourly_live trades            # show recent trades
    python -m backend.hourly_live signals           # show recent signals
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from rich.console import Console
from rich.table import Table

console = Console()


def _setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    # Quieten noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def cmd_start(args) -> None:
    """Start the hourly paper trading loop."""
    _setup_logging(args.debug)

    from .trader import HourlyPaperTrader

    # Accept both space-separated (--symbols A B C) and comma-separated (--symbols A,B,C)
    symbols = None
    if args.symbols:
        raw = args.symbols if isinstance(args.symbols, list) else [args.symbols]
        symbols = [s.strip().upper() for item in raw for s in item.split(",") if s.strip()]
    run_now = getattr(args, 'now', False)
    api_port = getattr(args, 'port', 0)
    trader = HourlyPaperTrader(
        symbols=symbols,
        initial_capital=args.capital,
        max_positions=args.max_positions,
        run_now=run_now,
        api_port=api_port,
    )

    # Graceful shutdown on SIGINT / SIGTERM
    loop = asyncio.new_event_loop()

    def _shutdown(sig, frame):
        console.print("\n[yellow]Shutting down...[/yellow]")
        trader._running = False

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    console.print(f"[bold blue]🚀 Hourly Paper Trader[/bold blue]")
    console.print(f"  Capital: [green]${args.capital:,.0f}[/green]")
    console.print(f"  Symbols: [cyan]{len(trader.symbols)}[/cyan] active hourly coins")
    console.print(f"  Max positions: [cyan]{args.max_positions}[/cyan]")
    console.print()

    loop.run_until_complete(trader.start())


def cmd_status(args) -> None:
    """Show current paper trading status."""
    _setup_logging(debug=False)
    logging.getLogger().setLevel(logging.WARNING)

    from .store import HourlyPaperStore

    store = HourlyPaperStore()
    store.connect()

    # Load engine state
    engine_data = store.load_engine_state()
    sm_data = store.load_state_machine()

    if not engine_data:
        console.print("[yellow]No paper trading state found. Start with: python -m backend.hourly_live[/yellow]")
        store.close()
        return

    capital = engine_data.get("capital", 0)
    positions = engine_data.get("open_positions", [])
    orders = engine_data.get("pending_orders", [])
    trades = engine_data.get("trade_history", [])

    # Summary
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    wins = [t for t in trades if t.get("pnl", 0) > 0]

    console.print("[bold blue]📊 Hourly Paper Trading Status[/bold blue]\n")
    console.print(f"  Capital: [green]${capital:,.2f}[/green]")
    console.print(f"  Total PnL: [{'green' if total_pnl >= 0 else 'red'}]${total_pnl:+,.2f}[/]")
    console.print(f"  Trades: {len(trades)} (W:{len(wins)} L:{len(trades)-len(wins)})")
    console.print(f"  Open positions: {len(positions)}")
    console.print(f"  Pending orders: {len(orders)}")

    # State machine overview
    if sm_data:
        console.print(f"\n[bold]State Machine[/bold] ({len(sm_data)} symbols)")
        table = Table(show_header=True)
        table.add_column("Symbol", style="cyan")
        table.add_column("State", style="bold")
        table.add_column("Level")
        table.add_column("Base Price")

        for sym, state in sorted(sm_data.items()):
            st = state.get("state", "?")
            style = {
                "searching": "dim", "cooling": "yellow",
                "consolidating": "blue", "monitoring": "green",
            }.get(st, "")
            bp = f"${state.get('base_price', 0):,.4f}" if state.get("base_price") else "—"
            table.add_row(
                sym,
                f"[{style}]{st}[/]",
                state.get("current_market_level", "—"),
                bp,
            )
        console.print(table)

    # Open positions
    if positions:
        console.print(f"\n[bold]Open Positions[/bold]")
        pt = Table(show_header=True)
        pt.add_column("Symbol", style="cyan")
        pt.add_column("Dir", style="bold")
        pt.add_column("Entry")
        pt.add_column("TP")
        pt.add_column("SL")
        pt.add_column("Size")
        pt.add_column("Lev")
        for p in positions:
            pt.add_row(
                p["symbol"],
                p["direction"].upper(),
                f"${p['entry_price']:,.4f}",
                f"${p['tp_price']:,.4f}",
                f"${p['sl_price']:,.4f}",
                f"${p['size_usdt']:,.0f}",
                f"{p['leverage']:.0f}x",
            )
        console.print(pt)

    store.close()


def cmd_trades(args) -> None:
    """Show recent closed trades."""
    _setup_logging(debug=False)
    logging.getLogger().setLevel(logging.WARNING)

    from .store import HourlyPaperStore

    store = HourlyPaperStore()
    store.connect()
    trades = store.get_trades(limit=args.limit)
    store.close()

    if not trades:
        console.print("[yellow]No trades found.[/yellow]")
        return

    console.print(f"[bold blue]📈 Recent Trades[/bold blue] (last {len(trades)})\n")
    table = Table(show_header=True)
    table.add_column("Symbol", style="cyan")
    table.add_column("Dir")
    table.add_column("Level")
    table.add_column("Entry")
    table.add_column("Exit")
    table.add_column("PnL", justify="right")
    table.add_column("PnL%", justify="right")
    table.add_column("Reason")
    table.add_column("Hours", justify="right")

    for t in trades:
        pnl = t.get("pnl", 0)
        pnl_color = "green" if pnl > 0 else "red"
        table.add_row(
            t["symbol"],
            t["direction"].upper(),
            t.get("level", "—"),
            f"${t['entry_price']:,.4f}",
            f"${t['exit_price']:,.4f}",
            f"[{pnl_color}]${pnl:+,.2f}[/]",
            f"[{pnl_color}]{t.get('pnl_pct', 0):+.1f}%[/]",
            t.get("exit_reason", "—"),
            f"{t.get('hold_hours', 0):.1f}",
        )
    console.print(table)


def cmd_signals(args) -> None:
    """Show recent signals."""
    _setup_logging(debug=False)
    logging.getLogger().setLevel(logging.WARNING)

    from .store import HourlyPaperStore

    store = HourlyPaperStore()
    store.connect()
    signals = store.get_signals(limit=args.limit)
    store.close()

    if not signals:
        console.print("[yellow]No signals found.[/yellow]")
        return

    console.print(f"[bold blue]📡 Recent Signals[/bold blue] (last {len(signals)})\n")
    table = Table(show_header=True)
    table.add_column("Time")
    table.add_column("Symbol", style="cyan")
    table.add_column("Dir")
    table.add_column("Level")
    table.add_column("Base")
    table.add_column("Entry")
    table.add_column("TP")
    table.add_column("SL")

    for s in signals:
        table.add_row(
            s.get("signal_time", "—")[:16],
            s["symbol"],
            s["direction"].upper(),
            s.get("level", "—"),
            f"${s['base_price']:,.4f}",
            f"${s['entry_price']:,.4f}",
            f"${s['tp_price']:,.4f}",
            f"${s['sl_price']:,.4f}",
        )
    console.print(table)


def main():
    parser = argparse.ArgumentParser(
        description="Hourly Paper Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # -- start --
    p_start = sub.add_parser("start", help="Start paper trading")
    p_start.add_argument("--symbols", nargs="+", default=None,
                         help="Symbols (space or comma separated, default: all active hourly)")
    p_start.add_argument("--capital", type=float, default=10000.0,
                         help="Initial capital (default: 10000)")
    p_start.add_argument("--max-positions", type=int, default=1,
                         help="Max concurrent positions (default: 1)")
    p_start.add_argument("--debug", action="store_true",
                         help="Enable debug logging")
    p_start.add_argument("--now", action="store_true",
                         help="Run one tick immediately on startup")
    p_start.add_argument("--port", type=int, default=0,
                         help="Start API server on this port (e.g. 8080)")

    # -- status --
    sub.add_parser("status", help="Show current status")

    # -- trades --
    p_trades = sub.add_parser("trades", help="Show recent trades")
    p_trades.add_argument("--limit", type=int, default=20, help="Number of trades")

    # -- signals --
    p_signals = sub.add_parser("signals", help="Show recent signals")
    p_signals.add_argument("--limit", type=int, default=20, help="Number of signals")

    args = parser.parse_args()

    if args.command is None or args.command == "start":
        if args.command is None:
            # Parse with defaults
            args = parser.parse_args(["start"])
        cmd_start(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "trades":
        cmd_trades(args)
    elif args.command == "signals":
        cmd_signals(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
