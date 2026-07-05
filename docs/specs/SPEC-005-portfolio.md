# SPEC-005 — 投組與風控

範圍：多策略（sleeve）組合、波動目標、限額檢查、kill-switch 狀態機。
輸入：各策略的目標權重；輸出：帳戶層級的最終目標部位。

## 1. 組合

```yaml
# configs/portfolio.yaml
sleeves:
  - strategy: chip_momentum_weekly
    risk_budget: 0.5
  - strategy: volume_breakout
    risk_budget: 0.3
  - strategy: st_reversal
    risk_budget: 0.2
vol_target:                    # 條件式，見 D-004
  enabled: true
  annual_vol: 0.15
  estimator: {window_days: 60, method: ewma, halflife: 20}
  conditional:                 # 只在波動極端時調整，避免高周轉（Bongaerts et al.）
    percentile_window_days: 252
    reduce_above_pct: 90       # 已實現波動落在 252 日 >90 百分位才降曝險
  max_leverage: 1.0            # 現股上限 1；波動目標只會降槓桿不會加
limits:
  max_weight_per_stock: 0.05   # 帳戶層級（跨 sleeve 合併後）
  max_weight_per_industry: 0.25
  max_daily_turnover: 0.30     # 單日 Σ|Δw|/2 上限，超過按比例縮量
```

- sleeve 內權重 × risk_budget → 加總 → 帳戶層級限額檢查 → 波動目標縮放
- **波動縮放只在此模組發生（D-004）**：strategy overlay（T-017）不得做波動縮放，
  否則會雙重縮減曝險。跨模組 AC-7 驗證全 pipeline 只套用一次。
- **條件式波動目標**：僅當 EWMA 已實現波動落在過去 252 日的 >90 百分位時，
  按比例縮小全部權重至 annual_vol 對應水準；其餘狀態維持 1x（max_leverage=1
  下不放大）。這與 STRATEGY §2 Overlay-2 一致。
- 限額衝突的解法順序固定：個股上限（裁掉超額，不重分配）→ 產業上限（產業內
  等比例縮）→ 周轉率上限（新舊權重線性插值）→ 波動目標

## 2. kill-switch 四態狀態機（configs/risk.yaml；數字為初始值，見 D-009）

狀態：`NORMAL → NO_NEW_ENTRY → REDUCED(50%) → HALTED`（可跳態）

| 觸發器 | 目標狀態 | 判定者 | 恢復條件 |
|---|---|---|---|
| 單日虧損 > 帳戶淨值 3% | NO_NEW_ENTRY | portfolio（讀每日權益） | 次一交易日自動回 NORMAL |
| 回撤自高點 > 15% | REDUCED(50%) | portfolio | 回撤收斂至 < 10%（遲滯）自動回 NORMAL |
| 回撤自高點 > 20% | HALTED | portfolio | 僅人工（DECISIONS.md） |
| 品質閘門連 3 交易日不過 | HALTED | pipeline→state | 僅人工 |
| 漂移連 5 日超標 | HALTED | monitoring→state | 僅人工 |

- 回撤「高點」基準 = 策略上線以來的權益高水位（不是滾動窗）
- NO_NEW_ENTRY：持倉不動，build_targets 不得產生使任一股票權重上升的目標
- REDUCED：全部目標權重 × 0.5
- **狀態持久化（D-010）**：狀態與 SPEC-006 的「次日凍結」旗標存 `data/state/`
  專層（**不在** signal 層——signal 允許重建會抹掉狀態）。單一 state manager
  序列化寫入 + 原子寫（temp+rename+fsync）。狀態檔缺失或損毀 → fail-safe 進
  HALTED 並發 CRIT，**禁止靜默回 NORMAL**。

## 3. 介面

```python
def build_targets(sleeve_signals: list[SleeveSignal], view: PitView,
                  state: PortfolioState, cfg: PortfolioConfig) -> TargetBook
```

型別定義（本 SPEC 為介面權威來源，見 D-007）：
```python
@dataclass(frozen=True)
class SleeveSignal:
    strategy: str
    date: dt.date
    weights: pl.DataFrame          # (stock_id, target_weight)

@dataclass
class PortfolioState:
    status: Literal["NORMAL", "NO_NEW_ENTRY", "REDUCED", "HALTED"]
    entered_at: dt.datetime
    trigger_reason: str
    freeze_next_day: bool          # SPEC-006 對帳異常時寫入
    equity_high_water: float
    schema_version: int
    # 持久化：data/state/portfolio_state.json（單檔、含 schema_version）

class TargetBook:                  # (stock_id, target_weight, sleeve_attribution)
    positions: pl.DataFrame        # 欄位 stock_id, target_weight
    sleeve_attribution: dict[str, dict[str, float]]  # stock_id -> {sleeve: weight}
```
sleeve_attribution 保留每檔股票的權重來自哪些 sleeve（供歸因用）。

## 驗收條件

- AC-1：兩個 sleeve 同持一檔股票時，帳戶權重 = Σ(sleeve 權重 × budget)，
  attribution 加總 == 帳戶權重（< 1e-12）
- AC-2（條件式波動目標，D-004）：波動 = 2×目標**且**落在 252 日 >90 百分位
  → 全部權重縮至目標對應水準（< 1e-9）；波動 = 1.2×目標但 < 90 百分位 → 權重
  不變（驗證「條件式」而非無條件）
- AC-3：限額順序：構造同時違反個股與產業限額的 fixture，輸出符合固定順序的
  預期結果（golden 比對）
- AC-4：周轉率上限：目標與現有權重差需要 40% 周轉、上限 30% → 實際輸出
  周轉率 = 30%（±1e-9），方向不變
- AC-5：四態機（D-009）：分別注入四個觸發器 fixture → 狀態轉對應值且輸出符合
  （NO_NEW_ENTRY 無權重上升、REDUCED 減半、HALTED 全零帶 reason）；重建
  PortfolioState（模擬重啟）後狀態保持；狀態檔損毀 → 讀取後狀態為 HALTED
- AC-6：HALTED 狀態下 build_targets 輸出全零權重並帶 reason
- AC-7（跨模組，D-004）：golden 端到端下，波動縮放係數在整條 pipeline 只被
  套用一次（strategy overlay 不縮、僅投組層縮）

## 範圍外

融資槓桿、期貨對沖 overlay、跨帳戶配置、稅務批次選擇。
