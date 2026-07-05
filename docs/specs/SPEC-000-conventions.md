# SPEC-000 — 全域慣例

所有模組共同遵守。與任何其他文件衝突時，以本文件與 `data/schemas.py` 為準。

## 時間

- 所有 timestamp：tz-aware `Asia/Taipei`
- `date` 欄位：交易日（`datetime.date`），非日曆日
- 交易日曆：以 TWSE 實際開市日為準，存 `data/clean/trading_calendar`；
  禁止用 `weekday()` 推算
- `available_at`：該筆資料可被使用的時點（timestamp）。日頻盤後資料預設為
  當日 18:00（保守值）；各表可覆寫，見 SPEC-001 附表

## 單位與型別

| 概念 | 型別/單位 | 備註 |
|---|---|---|
| stock_id | str | 保留前導零；上市上櫃同欄位，靠 stock_meta.market 區分 |
| 價格 | float64, TWD | 原始價 raw、還原價 adj 分開，不覆寫 |
| 成交量 | int64, 股 | 來源若是「張」在清洗層 ×1000 |
| 金額 | float64, TWD | |
| 報酬率 | float64, 小數 | 0.05 = 5% |
| 權重 | float64, 小數 | 多頭 [0,1]；預留空頭為負 |
| 比率類因子 | float64 | 統一方向：值越大越看多（direction=1 時） |

## 命名

- 檔案/函數/欄位：snake_case
- 因子名：`<category>.<name>_<window>`，如 `chip.concentration_10d`、
  `price.high_52w_proximity`、`volume.turnover_zscore_20d`
- category 固定集合：`price`、`volume`、`chip`、`fundamental`、`market`（大盤層級）
- 配置檔：`configs/strategies/<strategy_name>.yaml`、`configs/system.yaml`、
  `configs/risk.yaml`、`configs/costs.yaml`

## 資料湖分層

| 層 | 內容 | 可變性 |
|---|---|---|
| raw | 來源原樣（含原始欄名）+ 抓取 metadata | 不可變，只增不改 |
| clean | 正規化、通過 schema 驗證 | 允許重建（從 raw 重跑） |
| feature | 因子值（原始與標準化後） | 允許重建 |
| signal | 策略目標權重 | 允許重建；生產當日版本另存快照不可變 |

分區：`data/{layer}/{table}/date=YYYY-MM-DD/*.parquet`。
低頻表（月營收、股權分散）用 `date=` 該資料所屬期間的最後一日。

## 配置

- 全部 YAML，載入即用 pydantic 模型驗證（`src/twquant/config.py`）
- 配置模型欄位全部要有型別與預設值說明；未知欄位 = 驗證錯誤（`extra="forbid"`）
- 環境變數只放秘密與環境路徑（`.env`）：`FINMIND_TOKEN`、`SHIOAJI_API_KEY`、
  `SHIOAJI_SECRET`、`TWQUANT_DATA_DIR`

## 錯誤處理

- 自訂例外樹根：`TwquantError`；資料層 `DataError(table, date, detail)`、
  品質 `QualityGateError`、執行 `ExecutionError`
- 所有例外訊息含：哪張表/哪個模組、哪個日期、哪檔股票（若適用）、期望 vs 實際

## 可重現性

- 任何落地 artifact（回測報告、每日訊號）都寫入 metadata：
  git commit hash、配置內容 hash、程式版本、產生時間
- 需要隨機的地方（如 bootstrap 檢定）seed 從配置注入，預設 42

## 驗收條件

- AC-1：`from twquant.conventions import TZ, assert_tw_timestamp` 可用；
  傳入 naive datetime 會 raise
- AC-2：`configs/system.yaml` 缺少必填欄位或多出未知欄位時，載入即失敗且
  錯誤訊息指出欄位名
- AC-3：交易日曆表可回答 `next_trading_day(date)`、`is_trading_day(date)`，
  對 2025-01-01（元旦休市）回傳 False
