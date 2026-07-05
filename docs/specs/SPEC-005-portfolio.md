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
vol_target:
  enabled: true
  annual_vol: 0.15
  estimator: {window_days: 60, method: ewma, halflife: 20}
  max_leverage: 1.0            # 現股上限 1；波動目標只會降槓桿不會加
limits:
  max_weight_per_stock: 0.05   # 帳戶層級（跨 sleeve 合併後）
  max_weight_per_industry: 0.25
  max_daily_turnover: 0.30     # 單日 Σ|Δw|/2 上限，超過按比例縮量
```

- sleeve 內權重 × risk_budget → 加總 → 帳戶層級限額檢查 → 波動目標縮放
- 波動目標：realized vol（EWMA）> 目標 → 等比例縮小全部權重；< 目標不放大
  （max_leverage=1 時）
- 限額衝突的解法順序固定：個股上限（裁掉超額，不重分配）→ 產業上限（產業內
  等比例縮）→ 周轉率上限（新舊權重線性插值）→ 波動目標

## 2. kill-switch 狀態機（configs/risk.yaml，LOOP.md §3 的數字）

狀態：`NORMAL → REDUCED(50%) → HALTED`
- 觸發與恢復條件全部由每日權益曲線與追蹤誤差指標機器判定
- HALTED 只能由人工（DECISIONS.md 紀錄）恢復
- 狀態存 `data/signal/portfolio_state`，重啟程序後必須還原狀態（不能因為
  重開機就自動回 NORMAL）

## 3. 介面

```python
def build_targets(sleeve_signals: list[SleeveSignal], view: PitView,
                  state: PortfolioState, cfg: PortfolioConfig) -> TargetBook
# TargetBook: (stock_id, target_weight, sleeve_attribution: dict[str, float])
```

sleeve_attribution 保留每檔股票的權重來自哪些 sleeve（供歸因用）。

## 驗收條件

- AC-1：兩個 sleeve 同持一檔股票時，帳戶權重 = Σ(sleeve 權重 × budget)，
  attribution 加總 == 帳戶權重（< 1e-12）
- AC-2：注入 fixture 使已實現波動 = 2 × 目標 → 全部權重縮 0.5 倍（< 1e-9）；
  波動 = 0.5 × 目標 → 權重不變
- AC-3：限額順序：構造同時違反個股與產業限額的 fixture，輸出符合固定順序的
  預期結果（golden 比對）
- AC-4：周轉率上限：目標與現有權重差需要 40% 周轉、上限 30% → 實際輸出
  周轉率 = 30%（±1e-9），方向不變
- AC-5：kill-switch：權益序列觸發 -15% 回撤 → 狀態轉 REDUCED 且輸出權重減半；
  重建 PortfolioState（模擬重啟）後狀態仍為 REDUCED
- AC-6：HALTED 狀態下 build_targets 輸出全零權重並帶 reason

## 範圍外

融資槓桿、期貨對沖 overlay、跨帳戶配置、稅務批次選擇。
