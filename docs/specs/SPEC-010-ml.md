# SPEC-010 — ML 排序層（橫斷面 meta-factor）

範圍：以 LightGBM 學習既有因子庫的非線性組合，產出橫斷面選股分數（meta-factor），
沿既有 IC／回測／DSR 工具驗證，通過部署裁決後接入每日 pipeline。方法路線裁決見
[D-036](../../DECISIONS.md)；研究依據見研究報告「台股 ML 研究藍圖」（artifact，
2026-08-29）。關聯：SPEC-002（因子/IC）、SPEC-004（回測/驗證）、SPEC-009（供應層）、
CLAUDE.md 黃金規則。

## 0. 定位與不變量（黃金規則保全）

- **訓練是研究軌、推論走資料層，`factors/` 不碰模型檔**：訓練與推論由邊界層 CLI
  （`ml train` / `ml score`）負責 IO；推論結果落地為 feature 層 `ml_scores` 表；
  策略層透過薄因子（`ml.*`，經 `view.table("ml_scores")` 讀取）消費分數。
  黃金規則 1/10 與 IO 邊界靜態閘門（CLAUDE.md §4）**不鬆動、不加例外**。
- 程式落點：`src/twquant/ml/`。其中 `dataset.py`（特徵×標籤組裝）、`splits.py`
  （walk-forward 切分）為**純函數**（不 import requests/不開網路/不寫檔）；
  `train.py`、`score.py` 屬邊界層，允許讀寫 `data/models/` 與 feature 層。
- **ML 不取代既有線性組合**：`zscore_weighted`／`rank_avg` 是永久基準（§7）。
  ML 版本樣本外打不贏基準 → 記錄結論、不部署；「研究失敗」是合法產出。
- **決定性**：固定 seed、LightGBM `deterministic=true` + `force_row_wise=true`；
  同 config 同輸入重跑，模型預測輸出 bit-identical（黃金規則冪等性質）。
- 訓練資料源＝本機完整資料湖（或 Drive 歸檔還原）。雲端 Supabase 滾動窗口
  **只供每日推論**，禁止當訓練源（窗口不足，D-030/T-055）。
- 缺值保留 null（LightGBM 原生處理），**禁止填 0**（黃金規則 5）。

## 1. `configs/ml.yaml`（權威欄位清單，pydantic `MlConfig`，`extra="forbid"`）

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `horizon_days` | int | `10` | 主標籤視窗（交易日）；亦為 embargo 天數下限 |
| `aux_horizons` | list[int] | `[5, 20]` | 對照視窗（僅評估用，不訓練主模型） |
| `features` | list[str] | 全註冊因子 | 特徵因子名清單（取標準化 z 值） |
| `context_features` | list[str] | `[]` | 情境特徵開關：`market_cap_bucket`、`board`、`industry`、`near_limit`、`range_20d` |
| `label.execution` | str | `next_open` | 報酬起點：t+1 開盤（與策略 `execution: next_open` 對齊） |
| `label.excess` | str | `market_mean` | 超額化基準：同日宇宙均值 |
| `label.transform` | str | `rank_pct` | 標籤轉換：當日橫斷面百分位 |
| `split.train_min_years` | float | `2.0` | 首個訓練窗最短年數（依 T-065 審計回填） |
| `split.test_months` | int | `3` | 每段測試期長度；逐段前進、擴張訓練窗 |
| `split.embargo_days` | int | `10` | 訓練/測試間清除天數，須 ≥ `horizon_days` |
| `split.vault_months` | int | `6` | 金庫期：最後 N 個月凍結，僅最終定案時評估一次 |
| `split.days_per_year` | int | `244` | 年→交易日換算基數（`train_min_days` property 用；無魔術數字） |
| `split.days_per_month` | int | `21` | 月→交易日換算基數（`test_days`/`vault_days` property 用） |
| `lgbm` | dict | 見下 | LightGBM 參數；只暴露 `num_leaves`、`learning_rate`、`min_data_in_leaf`、`feature_fraction`、`n_estimators`、`seed`（其餘鎖預設） |
| `refit.schedule` | str | `monthly` | 生產期重訓頻率 |
| `universe` | — | 沿用策略層 | 與 `strategy/universe.py` 同一套濾網配置（處置/全額交割/最低 ADV） |
| `trials_log` | str | `research_log` | 實驗登記落點（沿用 T-013 research_log） |

秘密規則：本 SPEC 無任何秘密需求；`configs/ml.yaml` 不得出現 DSN/token。

## 2. 資料集組裝（`ml/dataset.py`，純函數）

- **特徵矩陣**：feature 層因子標準化 z 值 pivot 成 `(date, stock_id) × features`；
  來源經 `TableSource`/`PitView`，逐日橫斷面標準化（既有 `standardize`），
  無任何全樣本 scaler。
- **標籤**：`open(t+1) → open(t+1+h)` 報酬 − 同日宇宙均值 → 當日橫斷面百分位。
  標籤計算只存在於訓練組裝路徑（研究軌）；推論路徑（`score`）禁止 import 標籤模組。
- **宇宙**：as-of 當日濾網，與策略層同套；訓練/評估/回測三處宇宙一致。
- **訓練可用期間（T-065 審計定案，2026-08-29）**：`2020-01 ~ 今`，約 6.5 年
  （prices_daily 1,592 交易日、缺日≈國定假日；labels 無虞）。`train_min_years: 2.0`
  可行，**定案**。特徵覆蓋分層（factor_values 實測，缺值留 null 由 LightGBM 原生處理）：
  - 6.5 年全覆蓋：價量核心（mom/reversal/vol/turnover/overnight/intraday）、
    fundamental.revenue_yoy/accel、volume.*
  - 2023-09 起：price.high_52w；2024-01 起：chip.foreign_streak/trust_streak；
    2025-07 起：chip.foreign_net/trust_net/inst_net_10d
  - 2026-01 起：富邦分點系（concentration/main_force/broker_count/stability/
    main_cost）——raw `broker_trades` 有 2022-01 起 4.5 年完整明細，是**物化未回跑**；
    歷史 chip 因子回補（`serving materialize` 過 2022~2025）另立任務，Phase 2 前做
  - 型態因子：2026-07 起（事件稀疏，本就非主力特徵）
- **生存者偏誤（審計實測）**：`stock_meta` 2,010 檔、`delist_date` 全空——主檔
  **只有現存股票**。緩解：prices_daily 逐年檔數含當年有交易的全部代號（後來下市者
  的行情仍在），訓練/評估宇宙一律以「as-of 當日有行情＋流動性門檻」建構，
  不依賴 stock_meta 判在市；產業/市值情境特徵對已下市股票缺值＝null。
- 其他資料事實（審計）：institutional_daily/margin_daily 原始表僅 2025 起較完整
  （200/173 日）——法人/融資券「原始表」歷史殘缺，但對 ML 無礙（特徵走 factor_values）；
  shareholding_dispersion 僅 2 日（Phase 2 用前須回補）；fubon_fetch_log 有未來日期
  異常列（2026-11-11，帳本表、不入特徵，記錄備查）。

## 3. 切分器（`ml/splits.py`，純函數）

Purged walk-forward：擴張訓練窗 → embargo（≥ `horizon_days`，清除標籤延伸與測試期
重疊的訓練樣本）→ 連續測試段（`test_months`），逐段前進。輸出
`list[WalkSplit(train_start, train_end, test_start, test_end)]`。
金庫期（`vault_months`）不出現在任何開發用切分中。

## 4. 訓練與推論 CLI

- **`twquant ml train`**：讀 `configs/ml.yaml` → walk-forward 逐窗訓練 →
  `data/models/{model_id}/` 落地：`model.txt`（LightGBM 原生格式）+ `meta.json`
  （`train_end`、config hash、seed、lightgbm 版本、特徵清單、OOS 指標摘要）。
  每次訓練寫一列實驗登記（§7）。`model_id = {strategy}_{horizon}_{train_end}_{cfg_hash8}`。
- **`twquant ml score --date D`**：載入 `train_end < D` 的最新模型（**斷言**，違反
  raise；`meta.json` 缺 `train_end` 亦 raise）→ 對 D 日宇宙推論 → 寫 `ml_scores`。
- 雲端推論的模型檔遞送：模型檔隨 Drive 歸檔鏈上傳，pipeline 開跑時 `rclone copy`
  取回（細節屬 T-070，本 SPEC 只約定 `data/models/` 佈局為同步單位）。

## 5. `ml_scores` 資料合約

`(date, stock_id, model_id, score, rank_pct, available_at)`，pandera schema 進
`data/schemas.py`（feature 層）；`stock_id` text 保前導零、`available_at` tz-aware
Asia/Taipei（寫入時刻）。PG 鏡射比照 SPEC-009 §2.1（PK `(model_id, stock_id, date)`、
附 `(date)` 索引），滾動窗口天數入 `configs/serving.yaml`（不寫死）。

## 6. 因子註冊與評估

- 薄因子 `ml.lgbm_rank_10d`（`@factor` 註冊，`tables=["ml_scores"]`）：
  `view.table("ml_scores")` 取指定 model 系列的 `rank_pct`。純讀、無 IO、PIT-safe。
- 該因子必過 `assert_no_lookahead`（SPEC-002 §5），並可直接餵 `compute_ic_report`。

## 7. 評估階梯與部署閘門（每級過了才進下一級）

1. **OOS RankIC**：vs 各單因子、vs `zscore_weighted` 基準組合；同期間、同宇宙。
2. **分位單調性**：十分位組合報酬單調，非僅頭尾有差。
3. **成本後回測**：既有引擎（漲跌停/跳價拒單/價格相依成本）；監控換手率，必要時
   分數平滑（EMA 天數入 configs）或 buffer-zone 換股。
4. **deflated Sharpe**：trials 誠實計入所有登記過的配置；DSR 不顯著即止步。

**實驗登記簿**：每次訓練/評估寫 research_log 一列（config hash、資料期間、seed、
OOS 指標）；未登記的實驗視同不存在，trials 計數以登記簿為準。
**部署閘門**：階梯全過 + 金庫期最終評估 → DECISIONS.md 裁決（T-069）通過後，
才允許 T-070 接入每日 pipeline。

## 驗收條件

- AC-1：切分器屬性測試（hypothesis）——任意配置下訓練/測試日期零重疊、embargo ≥
  `horizon_days`、輸出決定性；金庫期不出現在任何切分。
- AC-2：標籤正確性——小 fixture 手算對齊 open-to-open 超額報酬與 rank_pct 轉換。
- AC-3：特徵矩陣缺值保留 null，全程無填 0；無任何全樣本 scaler。
- AC-4：訓練決定性——同 config 同輸入跑兩次，模型對同一測試集的預測 bit-identical。
- AC-5：`ml score` 對 `train_end >= as_of` 的模型 raise（含 meta 缺欄位情形）。
- AC-6：`ml.lgbm_rank_10d` 通過 `assert_no_lookahead`。
- AC-7：`ml_scores` schema 驗證失敗即 raise（不靜默丟列）。
- AC-8：每次 `ml train` 在 research_log 產生一列登記，config hash 可回溯到 yaml。
- AC-9：IC 對照報告可產出：模型分數 vs 基準組合，同期間同宇宙。
- AC-10：DSR 報告含 trials 數且 trials ＝ 登記簿列數。

## 範圍外

- 神經網路／序列模型／RL、日內策略、LLM 直接產訊號（insight 層維持無寫入權）、
  自動特徵探勘。Phase 2（特徵擴充＋SHAP 迴饋）、Phase 3（模型多樣化／regime
  gating）待 Phase 1 部署裁決通過後增修本 SPEC 或另立任務。
- 本 SPEC 不改動既有策略 YAML 的預設組合方式；ML 因子的實際採用由策略配置決定。
