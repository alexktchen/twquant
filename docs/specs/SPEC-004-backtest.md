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
  model: adv_impact              # cost = half_spread + k * sqrt(participation)
  half_spread_bps: 5             # 半價差下限（bps），實際取 max(檔位半價差, 此值)
  k_bps: 25                      # 衝擊係數
```

- 買進成本 = 手續費；賣出成本 = 手續費 + 證交稅；雙邊都加滑價
- **半價差須價格相依（REVIEW M1）**：half_spread = max(0.5 × tick(price)/price,
  half_spread_bps)。低價股 tick 造成的最小價差遠大於 5bps（15 元股 tick=0.05 →
  ≥16.7bps），固定 5bps 會系統性低估中小型股成本、讓高周轉 sleeve 假性過閘。
  tick(price) 依 §3 檔位表。
- **participation 分母 = 20 日成交金額中位數（ADV，D-005）**，回測與實盤同一
  定義；回測另以「當日實際成交金額」做事後上限截斷並記入 unfilled。
  > max_participation（Sleeve C 為 3%、其餘 5%）的部分視為未成交（見 §3）

## 3. 台股交易限制模擬

1. **漲跌停**：一律以 prices_daily 的 `limit_up_price`/`limit_down_price` 欄位
   （SPEC-001 §1 定義）判定：open ≥ limit_up_price → 買單不成交；open ≤
   limit_down_price → 賣單不成交。**旗標欄位缺失時才用 proxy**，且 proxy 必須
   依檔位精算漲跌停價（不可用固定比率 1.0995——台股漲停價 = 前收 × 1.10 後
   **向下取至檔位**，實際比率可低至約 +9.90%，固定門檻會漏判、讓回測在買不到
   的價位成交，高估動能/籌碼策略報酬，REVIEW H1）。
   檔位表（tick）：<10 → 0.01、10–50 → 0.05、50–100 → 0.1、100–500 → 0.5、
   500–1000 → 1、≥1000 → 5。未成交部分「不順延」，記錄進 unfilled ledger。
   注意 2015-06-01 漲跌幅由 7% 改 10%，跨此日的回測需標記結構斷點（REVIEW gap 6）。
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
- `deflated_sharpe(sharpe, trial_sharpes: list[float], T, skew, kurt)`：直接吃
  research_log 的全部試驗 SR（DSR 需要試驗 SR 的變異數 Var[SR_n] 才能算期望
  最大 SR，只給 trials 數無法計算，REVIEW M11）。trials = 同一 trial_scope 下
  distinct params_hash 數（research_log schema 見下）
- `param_plateau(cfg, param_grid)`：鄰域參數 Sharpe 熱圖
- `bootstrap_ci(returns, n=2000, seed from config)`：績效信賴帶（供 LOOP.md
  實盤比對用）

## 驗收條件

- AC-1（golden 端到端）：`tests/golden/` fixture + `momentum_20d_golden.yaml`
  （策略只用 `price.mom_20d`，定義見 SPEC-002 §1a）→ 全部指標與 golden 預存值
  差 < 1e-9。golden 預期輸出於本任務首次通過複審後才凍結（見 CLAUDE.md §4）
- AC-2（成本）：手算三筆交易的費、稅、滑價與 ledger 完全一致（fixture 內含
  買、賣、最低手續費三情境）
- AC-3（漲跌停）：fixture 含開盤漲停日，該日買單出現在 unfilled ledger 且
  現金未減少
- AC-4（整數張）：所有 ledger 的 shares % 1000 == 0
- AC-5（參與率）：任何 ledger 列的成交金額 / 當日成交金額 ≤ max_participation + 1e-12
- AC-6（會計恆等，D-012）：權益(t) = 現金(t) + 持倉市值(t)；且
  權益(t) − 權益(t-1) == 當日持倉損益 − 當日成本（費/稅/滑價），誤差 < 1e-6；
  全程現金 ≥ 0。成本於成交時點自現金扣除，equity curve 為含成本淨值
  （不可再減一次累計成本——那會重複扣成本，REVIEW H7）
- AC-7（處置強制出場）：fixture 中持有中被處置的股票在下一再平衡日全數出場，
  滑價為 2 倍
- AC-8（決定性）：同輸入重跑，BacktestResult 全欄位 bit-identical
- AC-9（walk-forward）：切割邊界無重疊、無遺漏，train/test 日期集合互斥

## 範圍外

事件驅動引擎、日內模擬、融資融券做空、當沖。介面預留 side 欄位但第一版只做現股多頭。
