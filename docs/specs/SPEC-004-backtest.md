# SPEC-004 — 回測引擎

範圍：日頻向量化回測、成本模型、台股交易限制、驗證協議工具。
核心原則：回測的成交假設必須與 PaperBroker（SPEC-006）完全一致——同一套程式碼。

## 1. 引擎

```python
def run_backtest(strategy_cfg: StrategyConfig, cost_cfg: CostConfig,
                 start: dt.date, end: dt.date, warehouse: Warehouse,
                 initial_capital: float = 10_000_000.0) -> BacktestResult
```

執行模型（第一版固定，參數化留待後續）：
- 訊號日 t 收盤後產生目標權重 → t+1 開盤價成交（`execution: next_open`）
- 整數張約束：目標金額 / (開盤價 × 1000) 取 floor；剩餘現金留存。
  盤中零股不做（第一版）
- 部分成交規則見 §3

## 2. 成本模型（`backtest/costs/`，configs/costs.yaml）

```yaml
commission_rate: 0.001425
commission_discount: 0.35        # 折讓後 = rate × discount
commission_min_twd: 20
tax_rate_sell: 0.003             # 現股賣出證交稅
slippage:
  model: adv_impact              # cost = spread_half + k * sqrt(participation)
  spread_half_bps: 5             # 半價差近似（bps）
  k_bps: 25                      # 衝擊係數
```

- 買進成本 = 手續費；賣出成本 = 手續費 + 證交稅；雙邊都加滑價
- participation = 成交金額 / 當日該股成交金額；> max_participation（預設 5%）
  的部分視為未成交（見 §3）

## 3. 台股交易限制模擬

1. **漲跌停**：t+1 開盤即漲停（open ≥ prev_close × 1.0995 且 open == high 的
   proxy 判定，或直接用漲停價旗標欄位）→ 買單不成交；開盤跌停 → 賣單不成交。
   未成交部分「不順延」（與 LOOP.md 營運規則一致），記錄進 unfilled ledger
2. **流動性上限**：單股單日參與率 ≤ max_participation，超出部分未成交
3. **處置股**：進場前已在宇宙過濾剔除；持有中被列處置 → 下一次再平衡強制出場
   （分盤交易仍可賣，用保守滑價 ×2 計）
4. **停牌**：無價格日凍結持倉，市值以最後價計，恢復交易日才可交易

## 4. 輸出（BacktestResult）

- equity curve（日）、持倉快照（日）、逐筆 ledger（date, stock_id, side,
  shares, price, commission, tax, slippage, reason）
- unfilled ledger（漲跌停/流動性造成的未成交）
- 指標：CAGR、年化 Sharpe（rf=0，√252）、Sortino、MDD、Calmar、年化周轉率
  （單邊，Σ|Δw|/2）、勝率、平均持有天數、成本佔比分解（費/稅/滑價）
- HTML 報告 + parquet 落地 + metadata（git hash、config hash）
- 自動 append 到 research_log.parquet

## 5. 驗證協議工具（`backtest/validation.py`）

- `walk_forward(cfg, train_years, test_years, step)`：滾動樣本外串接報告
- `deflated_sharpe(sharpe, trials, T, skew, kurt)`：trials 從 research_log 讀
- `param_plateau(cfg, param_grid)`：鄰域參數 Sharpe 熱圖
- `bootstrap_ci(returns, n=2000, seed from config)`：績效信賴帶（供 LOOP.md
  實盤比對用）

## 驗收條件

- AC-1（golden 端到端）：`tests/golden/` fixture + `momentum_20d_golden.yaml`
  → 全部指標與 golden 預存值差 < 1e-9
- AC-2（成本）：手算三筆交易的費、稅、滑價與 ledger 完全一致（fixture 內含
  買、賣、最低手續費三情境）
- AC-3（漲跌停）：fixture 含開盤漲停日，該日買單出現在 unfilled ledger 且
  現金未減少
- AC-4（整數張）：所有 ledger 的 shares % 1000 == 0
- AC-5（參與率）：任何 ledger 列的成交金額 / 當日成交金額 ≤ max_participation + 1e-12
- AC-6（會計恆等）：每日 現金 + 持倉市值 - 累計成本 == 權益曲線，誤差 < 1e-6；
  全程現金 ≥ 0
- AC-7（處置強制出場）：fixture 中持有中被處置的股票在下一再平衡日全數出場，
  滑價為 2 倍
- AC-8（決定性）：同輸入重跑，BacktestResult 全欄位 bit-identical
- AC-9（walk-forward）：切割邊界無重疊、無遺漏，train/test 日期集合互斥

## 範圍外

事件驅動引擎、日內模擬、融資融券做空、當沖。介面預留 side 欄位但第一版只做現股多頭。
