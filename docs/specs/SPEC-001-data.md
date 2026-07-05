# SPEC-001 — 資料層

範圍：來源轉接器、原始落地、清洗管線、倉儲與 point-in-time 視圖、品質閘門、回補。

## 1. 資料表清單（clean 層）

| 表名 | 主鍵 | 頻率 | 來源 | available_at 規則 |
|---|---|---|---|---|
| prices_daily | date, stock_id | 日 | TWSE rwd / TPEX / twstock | 當日 18:00 |
| institutional_daily | date, stock_id | 日 | TWSE T86 / TPEX | 當日 18:00 |
| margin_daily | date, stock_id | 日 | TWSE MI_MARGN / TPEX | 當日 21:00 |
| broker_trades | date, stock_id, broker_id | 日 | FinMind（付費） | 次日 08:00（保守） |
| shareholding_dispersion | date, stock_id, level | 週 | TDCC opendata | 公布日（週六）12:00 |
| monthly_revenue | year_month, stock_id | 月 | MOPS | 實際公布日 22:00；未知時取次月 10 日 |
| corporate_events | date, stock_id, event_type | 不定 | TWSE/TPEX 公告 | 公告日 18:00 |
| disposition_stocks | date, stock_id | 日 | TWSE/TPEX 處置公告 | 公告日 18:00，**生效區間另有欄位** |
| taifex_institutional | date, product, side | 日 | TAIFEX | 當日 15:30 |
| stock_meta | stock_id | 緩變 | TWSE/TPEX isin 清單 | 快照制，含上市日、下市日、產業、market |
| trading_calendar | date | 年 | 由 prices_daily 推導+人工核對 | — |
| ingest_log | source, table, date | 每次抓取 | 系統自產 | — |

欄位定義的唯一真相來源是 `src/twquant/data/schemas.py`（T-004 建立）。

## 2. 來源轉接器

```python
class Source(Protocol):
    name: str                       # "twse_rwd", "tpex", "tdcc", "mops", "taifex", "finmind"
    tables: list[str]
    def fetch(self, table: str, date: dt.date) -> pl.DataFrame:
        """抓一天/一期的原始資料。回傳原始欄名，不做轉換。失敗 raise DataError。"""
```

- 共用基礎設施（`data/http.py`）：session、UA、限流器（token bucket，
  TWSE 5 秒 3 次；TPEX 帶瀏覽器 headers；各來源速率在 `configs/system.yaml`）、
  重試（3 次指數退避）、原始 response 落地到 raw 層
- TPEX 有 anti-bot：requests 需完整 headers + cookie session；若持續 403/redirect，
  raise DataError 並在 note 標記 `anti_bot_suspected`，禁止自動升級成無頭瀏覽器
  （是否引入 Playwright 由 DECISIONS.md 裁決）
- FinMind：`data/sources/finmind.py`，token 從 env；免費/付費層級錯誤要能區分
  （HTTP 400 + level 訊息 → raise DataError("finmind_level_insufficient")）

## 3. 清洗管線

每張表一支 `data/pipelines/<table>.py`，職責：raw → clean。
規則：單位統一（張→股）、欄名統一、型別統一、民國年→西元、全形→半角、
數字欄位的 "--"/"" → null（不是 0）、加 `available_at` 與 `schema_version`。

## 4. 倉儲與 PIT 視圖

```python
class Warehouse:
    def __init__(self, data_dir: Path): ...
    def view(self, as_of: dt.date | dt.datetime) -> PitView: ...
    def write(self, layer: str, table: str, df: pl.DataFrame) -> None: ...
    def latest_date(self, table: str) -> dt.date | None: ...

class PitView:
    def table(self, name: str) -> pl.LazyFrame:
        """已過濾 available_at <= as_of。as_of 為 date 時取當日 23:59。"""
    def prices(self, adjusted: bool = True) -> pl.LazyFrame:
        """還原價：用 corporate_events 的除權息因子即時計算（不落地 adj 表），
        還原基準日 = as_of（anchor at as_of）。"""
```

- 還原價演算法：後復權，以 as_of 當日價格為錨。禁止提前用未來的除權息事件
  （PIT 視圖天然保證）。
- `write` 前自動跑 pandera 驗證，失敗 raise QualityGateError。

## 5. 品質閘門（每日 pipeline 的第 2 步）

檢查項（初始集，配置於 `configs/system.yaml`）：
1. 每張日頻表當日列數 ≥ 過去 20 日中位數的 80%
2. prices_daily 與 institutional_daily 的 stock_id 交集覆蓋率 ≥ 99%
3. 三大法人：全市場合計買超 vs TWSE 公布市場總表誤差 ≤ 0.5%
4. 價格跳動檢查：|close/prev_close - 1| > 0.11 且非恢復交易 → 標記異常
5. broker_trades：單股買賣總量 vs prices_daily 成交量比值 ∈ [0, 2.2]
   （分點含買+賣兩邊，理論上限 2；留 10% 餘裕）

## 6. 回補（backfill）

`python -m twquant ingest --table X --from 2015-01-01 --to 2026-07-01`
- 按日期正序、斷點續傳（查 ingest_log 跳過已成功日期）
- 支援 `--dry-run` 顯示將抓的日期數與預估時間（依限流速率）

## 驗收條件

- AC-1（PIT 核心）：對 monthly_revenue，`view(as_of=公布日-1).table()` 看不到
  該月資料，`view(as_of=公布日)` 看得到。用 fixture 驗證
- AC-2（raw 不可變）：對同一 (source, table, date) 重複 ingest，raw 層產生新
  版本檔而非覆寫；clean 層為最新版本
- AC-3（單位）：fixture 中 TWSE 原始「張」數據清洗後為股數（×1000），
  schema 驗證通過
- AC-4（限流）：mock clock 下，TWSE 來源 10 個連續請求耗時 ≥ 15 秒
- AC-5（品質閘門）：注入「列數腰斬」的 fixture 日，閘門 raise QualityGateError
  且訊息含表名與實際/期望列數
- AC-6（還原價）：fixture 含一次除息事件，還原價在事件前日的 adj close 
  等於 raw close × 還原因子，誤差 < 1e-9；as_of 在事件前時 adj == raw
- AC-7（斷點續傳）：中斷後重跑 backfill，已成功日期不重複請求（以 http
  fixture 呼叫計數驗證）
- AC-8（隔日可得）：broker_trades 的 available_at 為次日 08:00，
  `view(as_of=當日)` 看不到當日分點資料

## 範圍外

即時行情串流、tick 資料、bsr.twse.com.tw 驗證碼站點的自動化抓取（明確禁止）。
