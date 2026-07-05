# ARCHITECTURE — 系統架構書

版本：1.0（2026-07-04）
本文件定義模組邊界、資料流、資料合約與擴充點。任何實作不得跨越此處定義的邊界；
若邊界不合理，先在 DECISIONS.md 提案，裁決後才改本文件，再改程式。

## 1. 設計原則

1. **配置驅動**：程式碼實作「機制」，參數與策略組合放 `configs/` 的 YAML。
   換一個策略、改一個參數，不應該改任何 `.py` 檔。
2. **Point-in-time 是憲法**：策略與因子存取資料的唯一途徑是 `Warehouse.view(as_of)`，
   該視圖以資料的 `available_at`（可得時點）過濾，前視偏誤在 API 層面就不可能發生。
3. **兩軌共用一套定義**：研究軌（向量化回測）與生產軌（每日 pipeline）共用同一份
   因子定義、成本模型、交易約束。回測與實盤的差異必須可度量（見漂移偵測）。
4. **純函數核心，IO 在邊界**：`factors/`、`strategy/`、`backtest/`、`portfolio/`
   不得有任何網路或磁碟 IO；只有 `data/` 與 `execution/` 可以碰外部世界。
5. **插件式擴充**：資料來源、因子、策略、成本模型、券商，全部走註冊表
   （registry）模式。新增一個實作 = 新增一個檔案 + 一筆註冊，不改核心。
6. **失敗要大聲**：資料品質閘門不過就停止 pipeline 並告警，禁止靜默填補或跳過。

## 2. 資料流總圖

```
外部來源(TWSE/TPEX/TDCC/MOPS/TAIFEX/FinMind)
   │  data/sources/*  (轉接器，限流、重試、原始落地)
   ▼
data/raw/        原始層：抓回來什麼存什麼，不可變 (immutable)
   ▼  清洗、正規化、單位統一 (data/pipelines/*)
data/clean/      清潔層：通過資料合約 (pandera schema) 驗證後才能寫入
   ▼  Warehouse.view(as_of) ← 唯一讀取入口
factors/         因子層：純函數，輸出 (date, stock_id, factor, value)
   ▼
data/feature/    因子值落地（含標準化後版本）
   ▼
strategy/        策略層：YAML 配置 → 組合因子 → (date, stock_id, target_weight)
   ▼
data/signal/     訊號層：目標權重落地，回測與實盤都從這裡讀
   ├──► backtest/   研究軌：歷史模擬 → 績效報告
   └──► portfolio/ → execution/   生產軌：風控調整 → 下單 → 成交回報
                        ▼
                   monitoring/   對帳、歸因、漂移偵測 → 告警/降風險
```

## 3. 模組邊界與介面（合約摘要，細節見各 SPEC）

### data/（SPEC-001）
```python
class Source(Protocol):
    name: str
    tables: list[str]
    def fetch(self, table: str, date: dt.date) -> pl.DataFrame: ...

class Warehouse:
    def view(self, as_of: dt.date) -> PitView: ...   # 唯一對外讀取入口
    def write(self, layer: str, table: str, df: pl.DataFrame) -> None: ...

class PitView:
    def table(self, name: str) -> pl.LazyFrame: ...  # 已按 available_at <= as_of 過濾
    def prices(self, adjusted: bool = True) -> pl.LazyFrame: ...
```

### factors/（SPEC-002）
```python
@factor(name="chip.main_force_net_5d", category="chip", direction=1,
        tables=["broker_trades", "prices_daily"], lookback_days=10)
def main_force_net_5d(view: PitView, params: dict) -> pl.DataFrame:
    """回傳欄位固定為 (date, stock_id, value) 的 DataFrame"""
```

### strategy/（SPEC-003）
策略不是 class 繼承，是 YAML 配置。程式只提供組合機制：
```yaml
name: chip_momentum_weekly
universe: {exclude_disposition: true, min_adv_twd: 30_000_000, exclude_full_delivery: true}
signals:
  - factor: chip.concentration_10d
    weight: 0.4
  - factor: price.high_52w_proximity
    weight: 0.6
combine: zscore_weighted        # 或 rank_avg / ic_weighted
selection: {top_n: 40}
rebalance: {schedule: weekly, day: MON, execution: next_open}
overlays: [breadth_regime_filter]
```

### backtest/（SPEC-004）
```python
def run_backtest(strategy_cfg: StrategyConfig, cost_cfg: CostConfig,
                 start: dt.date, end: dt.date, warehouse: Warehouse) -> BacktestResult
```

### portfolio/（SPEC-005）
本節簽名為示意；權威定義以 SPEC-005 §3 為準（文件優先權見 SPEC-000 / D-007）。
```python
def build_targets(sleeve_signals: list[SleeveSignal], view: PitView,
                  state: PortfolioState, cfg: PortfolioConfig) -> TargetBook
```

### execution/（SPEC-006）
```python
class Broker(Protocol):
    def place(self, order: Order) -> OrderAck: ...
    def positions(self) -> list[Position]: ...
    def fills(self, date: dt.date) -> list[Fill]: ...
# 實作：ShioajiBroker（實盤）、PaperBroker（模擬，成交假設 == 回測引擎）
```

### monitoring/（SPEC-007）
每日產出 DailyReport；核心指標是 live-vs-sim 漂移（同一訊號在回測引擎模擬的
當日損益 vs 實盤實際損益的差），超過閾值觸發告警與自動降風險。

### insight/（SPEC-008）
LLM 洞悉層：讀 monitoring 與研究產出物，生成每日摘要、異常解釋與「策略調整
提案」。只能寫 `proposals/`，對 configs 與執行層無寫入路徑；提案經回測閘門
與人工核准才生效（LOOP.md §4）。模型路由見 MODEL-POLICY.md。可離線停用。

## 4. 資料合約

- 每張表在 `src/twquant/data/schemas.py` 有一份 pandera schema，是唯一真相來源。
  文件中的表格描述若與 schema 衝突，以 schema 為準並回頭修文件。
- 寫入 clean 層前必須通過 schema 驗證；驗證失敗 = pipeline 中止 + 告警。
- 每張表帶 `schema_version`（int）。schema 變更只允許加欄位（append-only）；
  破壞性變更需要新表名（如 `broker_trades_v2`）與 DECISIONS.md 紀錄。
- 分區佈局：`data/{layer}/{table}/date=YYYY-MM-DD/*.parquet`，
  由 DuckDB 以 hive partitioning 建視圖查詢。

## 5. 擴充點（新增 X 要動哪裡）

| 要新增 | 動哪裡 | 不准動 |
|---|---|---|
| 資料來源 | `data/sources/<name>.py` + 註冊 | 倉儲核心、其他來源 |
| 因子 | `factors/<category>/<name>.py` + `@factor` 註冊 | 標準化管線、其他因子 |
| 策略 | `configs/strategies/<name>.yaml` | 任何 .py |
| 成本模型 | `backtest/costs/<name>.py` + 註冊 | 引擎核心 |
| 券商 | `execution/brokers/<name>.py` 實作 Broker Protocol | 下單流程核心 |
| 風控規則 | `portfolio/rules/<name>.py` + 註冊 | 其他規則 |
| 洞悉功能 | `insight/<name>.py` + prompts/ 模板 | configs/strategies/（無寫入權） |

## 6. 技術選型

- Python ≥3.12，套件管理 `uv`
- DataFrame：polars（主）；pandas 僅限與外部庫介接處
- 儲存：Parquet + DuckDB（單機）；抽象層允許之後換 PostgreSQL 不動上層
- 驗證：pandera（資料）、pydantic v2（配置）
- CLI：typer（`python -m twquant <cmd>`）
- 排程：APScheduler（開發期）→ Windows 工作排程器/系統 cron（生產）
- 測試：pytest + hypothesis（屬性測試）
- Lint/型別：ruff + mypy（strict）
- 券商：Shioaji（永豐）
- LLM：anthropic Python SDK（模型與參數規範見 MODEL-POLICY.md、SPEC-008）

## 7. 非目標（明確不做，避免範圍蔓延）

- 不做 tick 級/高頻；日頻與週頻為主，最細到日內 VWAP 分批
- 不做多帳戶、多使用者權限
- 不自建網頁前端；監控用 Streamlit 單頁 + 訊息推播
- 不做選擇權造市、期貨跨式等衍生品策略（期貨資料只當擇時輸入）
