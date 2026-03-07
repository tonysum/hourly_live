# Hourly Live TODO

## Dashboard 改进
- [x] **实时价格刷新**：当前 `current_price` 来自 1H K线缓存，一小时才更新一次。改为调用 Binance ticker API（如 `GET /api/v3/ticker/price`），实现分钟级价格刷新，使 PnL 显示更及时。
