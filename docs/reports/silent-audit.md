# 靜默失效靜態稽核

## noop-config — no-op 配置欄位（15 處）

- `src/twquant/config.py|strategy/config.py:37` — QualityGateConfig.min_rows_ratio 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:38` — QualityGateConfig.inst_cross_check_tol 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:39` — QualityGateConfig.disk_min_gb 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:47` — SystemConfig.quality_gate 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:80` — ServingConfig.pool_min 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:81` — ServingConfig.pool_max 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:82` — ServingConfig.factor_history_days 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:125` — MlLabelConfig.excess 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:188` — MlConfig.aux_horizons 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:190` — MlConfig.context_features 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:194` — MlConfig.refit 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:195` — MlConfig.trials_log 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:49` — UniverseConfig.exclude_disposition 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:50` — UniverseConfig.exclude_full_delivery 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了
- `src/twquant/config.py|strategy/config.py:51` — UniverseConfig.exclude_recent_ipo_days 宣告了但全 repo 找不到讀取點——配置照樣驗證通過，使用者以為設定生效了

## bare-except — 裸 except（吞例外）（10 處）

- `src/twquant/cli.py:869` — except Exception 未 re-raise——例外被吞掉後呼叫端會拿到一個「成功但不完整」的結果
- `src/twquant/cli.py:663` — except Exception 未 re-raise——例外被吞掉後呼叫端會拿到一個「成功但不完整」的結果
- `src/twquant/data/http.py:147` — except Exception 未 re-raise——例外被吞掉後呼叫端會拿到一個「成功但不完整」的結果
- `src/twquant/data/serving/materialize.py:81` — except Exception 未 re-raise——例外被吞掉後呼叫端會拿到一個「成功但不完整」的結果
- `src/twquant/insight/client.py:85` — except Exception 未 re-raise——例外被吞掉後呼叫端會拿到一個「成功但不完整」的結果
- `src/twquant/monitoring/snapshot.py:709` — except Exception 未 re-raise——例外被吞掉後呼叫端會拿到一個「成功但不完整」的結果
- `src/twquant/monitoring/snapshot.py:188` — except Exception 未 re-raise——例外被吞掉後呼叫端會拿到一個「成功但不完整」的結果
- `src/twquant/monitoring/snapshot.py:298` — except Exception 未 re-raise——例外被吞掉後呼叫端會拿到一個「成功但不完整」的結果
- `src/twquant/pipeline.py:63` — except Exception 未 re-raise——例外被吞掉後呼叫端會拿到一個「成功但不完整」的結果
- `scripts/report_bundle.py:298` — except Exception 未 re-raise——例外被吞掉後呼叫端會拿到一個「成功但不完整」的結果

## default-fill — 危險預設填補（24 處）

- `src/twquant/backtest/capacity.py:29` — 對真實世界量套用預設值：adv = adv_twd.get(sid, 0.0)——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/backtest/diagnostics.py:47` — 對真實世界量套用預設值：current = positions.get(trade.stock_id, 0)——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/backtest/diagnostics.py:232` — 對真實世界量套用預設值：pos[t.stock_id] = pos.get(t.stock_id, 0) + t.shares——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/backtest/diagnostics.py:233` — 對真實世界量套用預設值：cost[t.stock_id] = cost.get(t.stock_id, 0.0) + t.shares * t.price + fees——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/backtest/diagnostics.py:235` — 對真實世界量套用預設值：pos[t.stock_id] = pos.get(t.stock_id, 0) - t.shares——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/backtest/diagnostics.py:236` — 對真實世界量套用預設值：proceeds[t.stock_id] = proceeds.get(t.stock_id, 0.0) + t.shares * t.price - fees——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/backtest/diagnostics.py:325` — 對真實世界量套用預設值：pos[t.stock_id] = pos.get(t.stock_id, 0) + t.shares——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/backtest/diagnostics.py:326` — 對真實世界量套用預設值：cost[t.stock_id] = cost.get(t.stock_id, 0.0) + t.shares * t.price + fees——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/backtest/diagnostics.py:328` — 對真實世界量套用預設值：pos[t.stock_id] = pos.get(t.stock_id, 0) - t.shares——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/backtest/diagnostics.py:329` — 對真實世界量套用預設值：proc[t.stock_id] = proc.get(t.stock_id, 0.0) + t.shares * t.price - fees——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/backtest/engine.py:140` — 對真實世界量套用預設值：ref = float(px) if px is not None else state.last_close.get(sid, 0.0)——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/backtest/engine.py:221` — 對真實世界量套用預設值：state.holdings[sid] = state.holdings.get(sid, 0) + qty——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/backtest/engine.py:235` — 對真實世界量套用預設值：new = state.holdings.get(sid, 0) - qty——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/execution/broker.py:131` — 對真實世界量套用預設值：self._holdings[order.stock_id] = self._holdings.get(order.stock_id, 0) + filled——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/execution/broker.py:134` — 對真實世界量套用預設值：new = self._holdings.get(order.stock_id, 0) - filled——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/execution/daily.py:65` — 對真實世界量套用預設值：delta = desired - current_positions.get(sid, 0)——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/factors/price.py:58` — 對真實世界量套用預設值：raw = params.get("window", 20)——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/factors/price.py:82` — 對真實世界量套用預設值：raw = params.get("window", 252)——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/factors/price.py:131` — 對真實世界量套用預設值：raw = params.get("window", 60)——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/factors/volume.py:57` — 對真實世界量套用預設值：raw = params.get("window", 20)——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/ml/dataset.py:52` — 對真實世界量套用預設值：.with_columns(adj=pl.col("adj").fill_null(1.0))——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/monitoring/screener.py:52` — 對真實世界量套用預設值：.with_columns(pl.col("net").fill_null(0), pl.col("yoy").forward_fill().over("stock_id"))——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/portfolio/core.py:72` — 對真實世界量套用預設值：return sum(abs(target.get(k, 0.0) - current.get(k, 0.0)) for k in keys) / 2.0——缺資料被偽裝成一個具體數值，下游看不出差別
- `src/twquant/portfolio/core.py:159` — 對真實世界量套用預設值：s: min(target.get(s, 0.0), current.get(s, 0.0))——缺資料被偽裝成一個具體數值，下游看不出差別

## unguarded-shift — 未防護的列位移（20 處）

- `src/twquant/backtest/engine.py:104` — _price_panel() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/data/clean.py:476` — inconsistent_events() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/chip.py:134` — margin_velocity_5d() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/fundamental.py:71` — revenue_accel() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/ic.py:36` — forward_returns() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/ic.py:121` — event_starts() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/industry.py:32` — _industry_mean_return() 有 2 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/industry.py:79` — industry_rs_20d() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/patterns.py:458` — box_breakout_60d() 有 2 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/price.py:153` — vol_20d() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/price.py:181` — rs_market_20d() 有 2 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/price.py:221` — overnight_ret_20d() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/price.py:294` — residual_reversal_5d() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/quality.py:66` — _consecutive() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/quality.py:71` — _lag() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/quality.py:76` — _ttm() 有 3 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/quality.py:86` — _quarterly() 有 3 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/factors/volume.py:28` — vol_ratio_5d() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/monitoring/screener.py:36` — daily_metrics() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記
- `src/twquant/strategy/overlays.py:101` — vol_target_scaling() 有 1 處列位移但無跨距防護——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記

## raw-division — 裸除法（4 處）

- `src/twquant/factors/broker.py:84` — 裸除法（未用 safe_ratio）：.with_columns(ratio=pl.col("n_flip") / pl.col("n_buys"))——分母為 0 得 inf，而 inf 不是 null，會通過 drop_nulls
- `src/twquant/factors/industry.py:37` — 裸除法（未用 safe_ratio）：/ pl.col("close").shift(window).over("stock_id", order_by="date")——分母為 0 得 inf，而 inf 不是 null，會通過 drop_nulls
- `src/twquant/factors/industry.py:88` — 裸除法（未用 safe_ratio）：.with_columns(_mkt=pl.col("close") / pl.col("close").shift(window) - 1.0)——分母為 0 得 inf，而 inf 不是 null，會通過 drop_nulls
- `src/twquant/factors/patterns.py:478` — 裸除法（未用 safe_ratio）：& ((pl.col("_hi") - pl.col("_lo")) / pl.col("_lo") <= box_width)——分母為 0 得 inf，而 inf 不是 null，會通過 drop_nulls

## truthy-numeric — 真值判斷數值（1 處）

- `src/twquant/backtest/engine.py:208` — 以真值判斷數值：adv=float(adv) if adv else None,——0 是合法的量測值，被當成「未知」會靜默關掉相關約束

