import { useState, useEffect, useCallback, useRef } from 'react'
import './App.css'
import { api } from './api'
import type { StatusData, Trade, Signal, Position, Order } from './api'

type Tab = 'overview' | 'trades' | 'signals' | 'logs'

function formatUSD(n: number, decimals = 2): string {
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

function formatPrice(n: number): string {
  if (n >= 1000) return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (n >= 1) return n.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 })
  return n.toLocaleString('en-US', { minimumFractionDigits: 6, maximumFractionDigits: 6 })
}

function timeAgo(iso: string): string {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const h = Math.floor(diff / 3600000)
  if (h < 1) return `${Math.floor(diff / 60000)}m ago`
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

export default function App() {
  const [tab, setTab] = useState<Tab>('overview')
  const [status, setStatus] = useState<StatusData | null>(null)
  const [trades, setTrades] = useState<Trade[]>([])
  const [signals, setSignals] = useState<Signal[]>([])
  const [positions, setPositions] = useState<Position[]>([])
  const [orders, setOrders] = useState<Order[]>([])
  const [error, setError] = useState<string | null>(null)
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date())
  const [logOutput, setLogOutput] = useState<string[]>([])
  const [logError, setLogError] = useState<string[]>([])
  const [logType, setLogType] = useState<'output' | 'error'>('output')
  const logEndRef = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async () => {
    try {
      const [s, t, sig, pos] = await Promise.all([
        api.status(),
        api.trades(),
        api.signals(),
        api.positions(),
      ])
      setStatus(s)
      setTrades(t.trades)
      setSignals(sig.signals)
      setPositions(pos.open_positions)
      setOrders(pos.pending_orders)
      setError(null)
      setLastUpdate(new Date())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Connection failed')
    }
  }, [])

  const [logRefreshing, setLogRefreshing] = useState(false)

  const refreshLogs = useCallback(async () => {
    setLogRefreshing(true)
    try {
      const data = await api.logs(300)
      setLogOutput(data.output || [])
      setLogError(data.error || [])
    } catch { /* ignore */ }
    setLogRefreshing(false)
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 15000)
    return () => clearInterval(id)
  }, [refresh])

  // Fetch logs when Logs tab is active
  useEffect(() => {
    if (tab !== 'logs') return
    refreshLogs()
    const id = setInterval(refreshLogs, 10000)
    return () => clearInterval(id)
  }, [tab, refreshLogs])

  // Auto-scroll logs to bottom
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logOutput, logError, logType])

  if (!status && !error) {
    return <div className="loading"><div className="spinner" /> Connecting...</div>
  }

  if (error && !status) {
    return (
      <div className="loading">
        <div className="status-dot error" />
        <span>Unable to connect to API — {error}</span>
      </div>
    )
  }

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <h1>⚡ Hourly Paper Trading</h1>
        <div className="header-status">
          {status && <span className="uptime">🕐 {formatUptime(status.uptime_seconds)}</span>}
          <div className={`status-dot ${error ? 'error' : ''}`} />
          <span>Updated {lastUpdate.toLocaleTimeString()}</span>
        </div>
      </header>

      {/* Stats */}
      {status && (
        <div className="stats-grid">
          <div className="stat-card">
            <div className="label">Capital</div>
            <div className="value neutral">{formatUSD(status.capital, 0)}</div>
            <div className="sub">Initial: {formatUSD(status.initial_capital, 0)}</div>
          </div>
          <div className="stat-card">
            <div className="label">Total PnL</div>
            <div className={`value ${status.total_pnl >= 0 ? 'positive' : 'negative'}`}>
              {status.total_pnl >= 0 ? '+' : ''}{formatUSD(status.total_pnl)}
            </div>
            <div className={`sub ${status.total_pnl_pct >= 0 ? 'positive' : 'negative'}`}>
              {status.total_pnl_pct >= 0 ? '+' : ''}{status.total_pnl_pct.toFixed(1)}%
            </div>
          </div>
          <div className="stat-card">
            <div className="label">Win Rate</div>
            <div className="value neutral">{status.win_rate.toFixed(0)}%</div>
            <div className="sub">{status.wins}W / {status.losses}L ({status.total_trades} total)</div>
          </div>
          <div className="stat-card">
            <div className="label">Positions</div>
            <div className="value neutral">{status.open_positions}</div>
            <div className="sub">{status.pending_orders} pending orders</div>
          </div>
        </div>
      )}

      {/* State Machine */}
      {status && (
        <div className="section">
          <div className="section-header">
            <h2>🤖 State Machine</h2>
            <span className="count">{status.symbols.length} symbols</span>
          </div>
          <div className="sm-grid">
            {Object.entries(status.state_machine).map(([sym, s]) => (
              <div className="sm-card" key={sym}>
                <span className="sm-symbol">{sym.replace('USDT', '')}</span>
                <span className={`sm-badge ${s.state}`}>{s.state}</span>
                <span className="sm-meta">
                  {s.trade_level !== '—' && <span>{s.trade_level}</span>}
                  {s.base_price && <div>${formatPrice(s.base_price)}</div>}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Open Positions */}
      {positions.length > 0 && (
        <div className="section">
          <div className="section-header">
            <h2>📈 Open Positions</h2>
            <span className="count">{positions.length}</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Dir</th>
                  <th>Level</th>
                  <th>Entry</th>
                  <th>Price</th>
                  <th>TP</th>
                  <th>SL</th>
                  <th>PnL</th>
                  <th>Margin</th>
                  <th>Qty</th>
                  <th>Lev</th>
                  <th>Hold</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i}>
                    <td>{p.symbol.replace('USDT', '')}</td>
                    <td><span className={`badge ${p.direction}`}>{p.direction}</span></td>
                    <td>{p.level}</td>
                    <td>${formatPrice(p.entry_price)}</td>
                    <td>{p.current_price != null ? `$${formatPrice(p.current_price)}` : '—'}</td>
                    <td className="positive">${formatPrice(p.tp_price)}</td>
                    <td className="negative">${formatPrice(p.sl_price)}</td>
                    <td className={p.unrealized_pnl != null ? (p.unrealized_pnl >= 0 ? 'positive' : 'negative') : ''}>
                      {p.unrealized_pnl != null
                        ? `${p.unrealized_pnl >= 0 ? '+' : ''}${formatUSD(p.unrealized_pnl)} (${p.unrealized_pnl_pct! >= 0 ? '+' : ''}${p.unrealized_pnl_pct!.toFixed(1)}%)`
                        : '—'}
                    </td>
                    <td>{formatUSD(p.size_usdt, 0)}</td>
                    <td>{p.qty.toFixed(2)}</td>
                    <td>{p.leverage}x</td>
                    <td>{p.hold_hours != null ? (p.hold_hours >= 24 ? `${(p.hold_hours / 24).toFixed(1)}d` : `${p.hold_hours.toFixed(0)}h`) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="tabs">
        <button className={`tab ${tab === 'overview' ? 'active' : ''}`} onClick={() => setTab('overview')}>
          📋 Orders
        </button>
        <button className={`tab ${tab === 'trades' ? 'active' : ''}`} onClick={() => setTab('trades')}>
          💰 Trades
        </button>
        <button className={`tab ${tab === 'signals' ? 'active' : ''}`} onClick={() => setTab('signals')}>
          📡 Signals
        </button>
        <button className={`tab ${tab === 'logs' ? 'active' : ''}`} onClick={() => setTab('logs')}>
          📄 Logs
        </button>
      </div>

      {/* Pending Orders */}
      {tab === 'overview' && (
        <div className="section">
          {orders.length === 0 ? (
            <div className="empty">No pending orders</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Direction</th>
                    <th>Level</th>
                    <th>Entry Price</th>
                    <th>Size</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o, i) => (
                    <tr key={i}>
                      <td>{o.symbol.replace('USDT', '')}</td>
                      <td><span className={`badge ${o.direction}`}>{o.direction}</span></td>
                      <td>{o.level}</td>
                      <td>${formatPrice(o.entry_price)}</td>
                      <td>{formatUSD(o.size_usdt, 0)}</td>
                      <td>{o.created_at ? timeAgo(o.created_at) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Trades */}
      {tab === 'trades' && (
        <div className="section">
          {trades.length === 0 ? (
            <div className="empty">No trades yet</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Dir</th>
                    <th>Level</th>
                    <th>Entry</th>
                    <th>Exit</th>
                    <th>PnL</th>
                    <th>PnL%</th>
                    <th>Result</th>
                    <th>Hold</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t, i) => (
                    <tr key={i}>
                      <td>{t.symbol.replace('USDT', '')}</td>
                      <td><span className={`badge ${t.direction}`}>{t.direction}</span></td>
                      <td>{t.level}</td>
                      <td>${formatPrice(t.entry_price)}</td>
                      <td>${formatPrice(t.exit_price)}</td>
                      <td className={t.pnl >= 0 ? 'positive' : 'negative'}>
                        {t.pnl >= 0 ? '+' : ''}{formatUSD(t.pnl)}
                      </td>
                      <td className={t.pnl_pct >= 0 ? 'positive' : 'negative'}>
                        {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct.toFixed(1)}%
                      </td>
                      <td>{t.exit_reason}</td>
                      <td>{t.hold_hours.toFixed(0)}h</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Signals */}
      {tab === 'signals' && (
        <div className="section">
          {signals.length === 0 ? (
            <div className="empty">No signals yet</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Dir</th>
                    <th>Level</th>
                    <th>Entry</th>
                    <th>TP</th>
                    <th>SL</th>
                    <th>Lev</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {signals.map((s, i) => (
                    <tr key={i}>
                      <td>{s.symbol.replace('USDT', '')}</td>
                      <td><span className={`badge ${s.direction}`}>{s.direction}</span></td>
                      <td>{s.level}</td>
                      <td>${formatPrice(s.entry_price)}</td>
                      <td className="positive">${formatPrice(s.tp_price)}</td>
                      <td className="negative">${formatPrice(s.sl_price)}</td>
                      <td>{s.leverage}x</td>
                      <td>{s.signal_time ? timeAgo(s.signal_time) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Logs */}
      {tab === 'logs' && (
        <div className="section">
          <div className="log-toolbar">
            <button
              className={`log-type-btn ${logType === 'output' ? 'active' : ''}`}
              onClick={() => setLogType('output')}
            >
              📤 Output ({logOutput.length})
            </button>
            <button
              className={`log-type-btn ${logType === 'error' ? 'active' : ''}`}
              onClick={() => setLogType('error')}
            >
              ❌ Error ({logError.length})
            </button>
            <button className="log-type-btn" onClick={refreshLogs}>{logRefreshing ? '⏳...' : '🔄 Refresh'}</button>
          </div>
          <div className="log-viewer">
            {(logType === 'output' ? logOutput : logError).length === 0 ? (
              <div className="log-empty">No {logType} logs</div>
            ) : (
              (logType === 'output' ? logOutput : logError).map((line, i) => (
                <div
                  key={i}
                  className={`log-line ${line.includes('ERROR') || line.includes('❌') ? 'log-error' : ''} ${line.includes('WARNING') || line.includes('⚠') ? 'log-warn' : ''} ${line.includes('🎯') || line.includes('✅') || line.includes('💰') ? 'log-success' : ''}`}
                >
                  <span className="log-num">{i + 1}</span>
                  <span className="log-text">{line}</span>
                </div>
              ))
            )}
            <div ref={logEndRef} />
          </div>
        </div>
      )}
    </div>
  )
}
