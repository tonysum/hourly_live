# Hourly Paper Trading System

Amplitude Hourly 策略的模拟交易系统。复用回测引擎 `engine_v2` 的 `AmplitudeStrategy`，通过 Binance REST API 获取实时 1H K 线数据，在内存中运行状态机、模拟限价单成交和 TP/SL 退出逻辑。

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                  HourlyPaperTrader                      │
│                 (trader.py · 主编排器)                   │
│                                                         │
│  ┌───────────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │ DataCollector │  │ StateMachine │  │  PaperEngine  │ │
│  │ (1H K线采集)   │→ │ (状态机决策)   │→ │ (模拟撮合)     │ │
│  └───────────────┘  └──────────────┘  └───────────────┘ │
│         ↑                  ↑                  ↓         │
│    Binance API       coins.json         PaperStore      │
│                    AmplitudeStrategy    (PostgreSQL)    │
└─────────────────────────────────────────────────────────┘
```

## 模块说明

| 模块 | 职责 |
|------|------|
| `data_collector.py` | 从 Binance REST API 拉取 1H K 线，转换为 `engine_v2.Candle` |
| `state_machine.py` | 逐 bar 驱动状态机（searching → cooling → consolidating → monitoring），复用 `AmplitudeStrategy` |
| `paper_engine.py` | 模拟限价单挂单/成交、TP/SL/超时平仓、杠杆 PnL 计算 |
| `store.py` | PostgreSQL 持久化（引擎状态、状态机快照、信号、交易、权益曲线） |
| `trader.py` | asyncio 主循环，每小时整点 +5s 执行一次 tick |
| `__main__.py` | CLI 入口（start / status / trades / signals） |

## 快速开始

```bash
# 启动（所有 active hourly 币种）
duo amp paper start

# 指定币种 + 立即执行一次 tick
duo amp paper start --symbols ETHUSDT XRPUSDT DOGEUSDT --now

# 自定义资金
duo amp paper start --symbols ETHUSDT --capital 5000 --now

# 查看状态
duo amp paper status

# 查看交易记录
duo amp paper trades

# 查看信号历史
duo amp paper signals

# 也可以直接用 python -m
python -m backend.hourly_live start --now
python -m backend.hourly_live status
```

## 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--symbols` | 全部 active hourly | 空格或逗号分隔的币种列表 |
| `--capital` | 10000 | 模拟初始资金 (USDT) |
| `--max-positions` | 1 | 最大同时持仓数 |
| `--now` | false | 启动后立即执行一次 tick |
| `--debug` | false | 开启 DEBUG 日志 |

## 状态机流程

```
searching ──(检测到振幅)──→ cooling ──(冷却期结束)──→ consolidating
                                                         │
    ↑                                        (盘整确认)   ↓
    │                                                monitoring
    │                                                    │
    └───────────(信号发出 / 超时)──────────────────────────┘
```

- **searching**: 在 72H 滚动窗口中检测价格摆动
- **cooling**: 检测到摆动后等待冷却期（默认 10H）
- **consolidating**: 检查最近 N 根 K 线是否形成窄幅盘整
- **monitoring**: 盘整确认后监控突破，触发则发出交易信号

## 交易模拟

1. **入场**: 信号触发 → 创建限价挂单（按 confirm% 偏移的价格）
2. **成交**: 下一根 1H K 线的 high/low 触及挂单价 → 模拟成交
3. **出场**: TP（止盈价触及）/ SL（止损价触及）/ Timeout（超时平仓）
4. **防护**: 同根 K 线 TP+SL 同时触及 → 保守取 SL；开仓当根 K 线跳过 TP/SL 检查
5. **过期**: 挂单超过 48H 未成交自动取消

## 数据存储

使用 `.env` 中的 PostgreSQL 配置（`PG_HOST`, `PG_PORT`, `PG_DB` 等），创建以下表：

| 表名 | 用途 |
|------|------|
| `hourly_paper_kv` | 引擎状态 + 状态机快照 (KV store) |
| `hourly_paper_orders` | 挂单记录 |
| `hourly_paper_positions` | 持仓记录 |
| `hourly_paper_trades` | 成交历史 |
| `hourly_paper_signals` | 信号历史 |
| `hourly_paper_equity` | 权益快照 |

## 策略配置

策略参数来自 `engine_v2/coins.json`，只选取 `model: "hourly"` 且 `active: true` 的币种。每个币种的振幅级别（micro/small/medium/large/huge）定义了不同的入场确认、止盈止损和杠杆参数。
