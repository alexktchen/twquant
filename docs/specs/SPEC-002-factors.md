# SPEC-002 — 因子註冊表

範圍：因子定義框架、標準化管線、IC 檢驗報告、反前視護欄。
因子的金融內容（要做哪些因子）見 `docs/STRATEGY.md`；本 SPEC 只定義機制。

## 1. 因子定義

```python
from twquant.factors import factor, PitView

@factor(
    name="chip.concentration_10d",
    category="chip",
    direction=1,                    # 1: 值大看多; -1: 值大看空
    tables=["broker_trades", "prices_daily"],
    lookback_days=15,               # 需要的最長回看（含緩衝），框架據此裁剪資料
    params={"window": 10, "top_n": 15},
)
def concentration_10d(view: PitView, params: dict) -> pl.DataFrame:
    """10 日主力買賣超佔成交量比。回傳 (date, stock_id, value)。"""
```

規則：
- 因子函數是純函數：輸入只有 PitView 與 params，無 IO、無全域狀態
- 回傳 schema 固定：`date: date, stock_id: str, value: float64`；
  無值就不回傳該列（不准填 0）
- 註冊表（`factors/registry.py`）：`get(name)`、`list(category=...)`、
  重複註冊同名 → raise
- params 的預設值寫在裝飾器；策略 YAML 可覆寫；實際使用值進 artifact metadata

## 2. 計算引擎

```python
def compute(names: list[str], view: PitView, dates: list[dt.date],
            overrides: dict[str, dict] | None = None) -> pl.DataFrame
    # 回傳 long format: (date, stock_id, factor, value)
```

- 批次模式：研究軌一次算整段歷史（內部仍逐 as_of 或用等價的向量化實作，
  但必須通過反前視屬性測試）
- 增量模式：生產軌每日只算最新一天
- 向量化捷徑允許：若因子可證明只依賴「t 日及以前」的列（如 rolling 視窗），
  可以整段計算；`assert_no_lookahead` 會自動抽查驗證等價性

## 3. 標準化管線（`factors/standardize.py`）

順序固定：
1. 去極值：MAD winsorize，界限 = median ± 5 × 1.4826 × MAD（橫斷面、逐日）
2. z-score（橫斷面、逐日）
3. 可選中性化：對 log 市值與產業 dummy 做橫斷面回歸取殘差
   （產業用 stock_meta.industry；配置決定開關）

輸出與原始值分開落地：`feature/factor_raw`、`feature/factor_std`。

## 4. IC 檢驗報告（`factors/ic_report.py`）

輸入：因子名、期間、前向報酬天數（預設 5, 10, 20）。輸出（HTML + parquet）：
- RankIC 時序、均值、t-stat（Newey-West 修正，lag = 前向天數）
- 分 5 組（quintile）等權組合的累積報酬與多空價差
- 因子自相關（衰退曲線）、換手率含義
- 與既有因子庫全成員的橫斷面相關矩陣（|ρ| > 0.7 高亮）

每次產出自動 append 到 `research_log.parquet`（見 LOOP.md 嘗試次數登記）。

## 5. 反前視護欄（`factors/testing.py`）

```python
def assert_no_lookahead(factor_name: str, fixture_view: PitView) -> None:
    """對 golden fixture 抽 3 個切點 t：
    compute([f], view_truncated_at(t)) 的結果必須等於
    compute([f], full_view) 過濾到 <= t 的結果（值誤差 < 1e-12）。
    不等 → AssertionError 並輸出首個不一致的 (date, stock_id)。"""
```

每個因子的測試檔必須呼叫它（CLAUDE.md §4）。CI 額外對全註冊表跑一輪。

## 驗收條件

- AC-1：註冊重複因子名 raise ValueError 且訊息含既有定義位置
- AC-2：因子函數回傳多餘欄位或錯誤型別 → compute 階段 raise，訊息含因子名
- AC-3：`assert_no_lookahead` 對「故意用 shift(-1) 偷看未來」的壞因子
  （tests 裡的 `_bad_lookahead_factor`）能偵測失敗
- AC-4：標準化後逐日橫斷面 mean ≈ 0（|mean| < 1e-9）、std ≈ 1（誤差 < 1e-6）；
  全 NaN 日不產生輸出而非產生 NaN
- AC-5：IC 報告對 golden fixture 的 `price.mom_20d` 產出，RankIC 數值與
  golden 預存值一致（< 1e-9）
- AC-6：compute 增量模式與批次模式對同一天輸出 bit-identical
- AC-7：中性化開啟時，因子值與 log 市值的橫斷面相關 |ρ| < 0.01

## 範圍外

因子挖掘自動化（genetic programming 等）、跨市場因子、日內因子。
