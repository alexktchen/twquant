# SPEC-003 — 策略框架

範圍：策略配置 schema、宇宙過濾、訊號組合、再平衡排程、regime overlay 掛點。
策略產出「目標權重」，不產出訂單。

## 1. 策略配置（pydantic 模型 `strategy/config.py`）

```yaml
name: chip_momentum_weekly
description: 籌碼確認的 52 週高點動能
universe:
  markets: [TWSE, TPEX]
  exclude_disposition: true          # 處置股（含生效區間內）
  exclude_full_delivery: true        # 全額交割
  exclude_recent_ipo_days: 60
  min_adv_twd: 30_000_000            # 20 日平均成交值下限
  min_price: null                    # 可選
signals:
  - factor: chip.concentration_10d
    weight: 0.4
    params: {top_n: 15}              # 覆寫因子預設 params
  - factor: price.high_52w_proximity
    weight: 0.6
combine: zscore_weighted             # zscore_weighted | rank_avg | ic_weighted
selection:
  method: top_n                      # top_n | threshold
  top_n: 40
weighting: equal                     # equal | score_prop | inv_vol
rebalance:
  schedule: weekly                   # daily | weekly | monthly
  day: MON                           # weekly 用；monthly 用 day_of_month
  execution: next_open               # 訊號日的下一個交易日開盤執行
overlays: [breadth_regime_filter]    # 依序套用，見 §4
max_weight_per_stock: 0.05
```

- `extra="forbid"`；載入時驗證 factor 名存在於註冊表、weight 加總 = 1（±1e-9）
- 同一份 YAML 是回測與生產的唯一輸入（兩軌共用）

## 2. 宇宙過濾（`strategy/universe.py`）

- 以 PIT 視圖為基礎：處置股用 disposition_stocks 的生效區間、
  下市股自然消失（stock_meta.delist_date > as_of）
- ADV 用「過去 20 個交易日成交金額中位數」（中位數抗漲停無量日）
- 過濾在訊號計算「之前」執行：因子計算的股票池 = 過濾後宇宙（省算力、
  避免處置股污染橫斷面標準化）

## 3. 組合方法（`strategy/combine.py`）

- `zscore_weighted`：Σ weight_i × std_factor_i（標準化後值加權）
- `rank_avg`：各因子橫斷面百分位排名的加權平均
- `ic_weighted`：權重 ∝ 各因子過去 250 日 RankIC 均值（每次再平衡時用 PIT
  歷史重估；IC 為負的因子權重取 0，全負 → 當期空手並告警）

## 4. Overlay 掛點（`strategy/overlays.py`）

Overlay 是 `(date, weights: DataFrame, view: PitView) -> weights` 的註冊函數，
在 selection 之後、輸出之前依序套用。初始集：

- `breadth_regime_filter`：市場寬度（站上 60 日線個股比例，參數化）低於下閾值
  → 整體曝險乘以 exposure_floor（如 0.5）；高於上閾值恢復 1.0（雙閾值防抖動）
- `index_trend_filter`：加權指數收盤 < 其 200 日均線 → 曝險乘以係數
- overlay 參數在 `configs/overlays.yaml`

## 5. 輸出合約

```
(date, stock_id, target_weight)  # date = 訊號日；執行日由 rebalance.execution 決定
```
- Σ target_weight ≤ 1 + 1e-9；個股 ≤ max_weight_per_stock
- 空手日輸出空表（不是缺檔）；生產軌另存快照到 signal 層（不可變）

## 驗收條件

- AC-1：YAML 引用不存在的因子 → 載入即失敗，訊息含因子名與最相近的註冊名
- AC-2：fixture 中處置股在生效區間內不出現在任何訊號輸出
- AC-3：weekly/MON 排程在 fixture 期間產生的訊號日全部是週一或
  （週一休市時）下一交易日
- AC-4：`ic_weighted` 在 t 日使用的 IC 只由 t 日前資料計算（反前視測試覆蓋）
- AC-5：權重不變量：所有輸出日 Σw ≤ 1+1e-9、max w ≤ 上限、無負權重（多頭版）
- AC-6：`breadth_regime_filter` 在 fixture 的空頭段（寬度 < 下閾值）輸出
  曝險 = exposure_floor × 原曝險，誤差 < 1e-9
- AC-7：同一 YAML + 同一資料，重跑輸出 bit-identical

## 範圍外

日內訊號、多空對沖版（介面預留負權重但第一版不實作）、期貨對沖 overlay。
