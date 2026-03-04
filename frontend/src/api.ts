const API_BASE = import.meta.env.VITE_API_URL || '';

export interface StatusData {
    capital: number;
    initial_capital: number;
    total_pnl: number;
    total_pnl_pct: number;
    total_trades: number;
    wins: number;
    losses: number;
    win_rate: number;
    open_positions: number;
    pending_orders: number;
    symbols: string[];
    state_machine: Record<string, {
        state: string;
        trade_level: string;
        base_price: number | null;
        cooling_start: string | null;
        current_market_level: string;
    }>;
    uptime_seconds: number;
    started_at: string | null;
    time: string;
}

export interface Trade {
    symbol: string;
    direction: string;
    level: string;
    entry_price: number;
    exit_price: number;
    entry_time: string;
    exit_time: string;
    pnl: number;
    pnl_pct: number;
    exit_reason: string;
    hold_hours: number;
    size_usdt: number;
    leverage: number;
}

export interface Signal {
    symbol: string;
    direction: string;
    level: string;
    base_price: number;
    entry_price: number;
    tp_price: number;
    sl_price: number;
    leverage: number;
    signal_time: string;
}

export interface Position {
    symbol: string;
    direction: string;
    level: string;
    entry_price: number;
    tp_price: number;
    sl_price: number;
    size_usdt: number;
    leverage: number;
    entry_time: string | null;
}

export interface Order {
    symbol: string;
    direction: string;
    level: string;
    entry_price: number;
    size_usdt: number;
    created_at: string | null;
}

async function fetchJSON<T>(path: string): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

export const api = {
    status: () => fetchJSON<StatusData>('/status'),
    trades: (limit = 20) => fetchJSON<{ trades: Trade[] }>(`/trades?limit=${limit}`),
    signals: (limit = 20) => fetchJSON<{ signals: Signal[] }>(`/signals?limit=${limit}`),
    positions: () => fetchJSON<{ open_positions: Position[]; pending_orders: Order[] }>('/positions'),
    logs: (lines = 200, type = 'all') => fetchJSON<{ output?: string[]; error?: string[] }>(`/logs?lines=${lines}&type=${type}`),
};
