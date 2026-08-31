# SPEC-011 — ML Phase 2：H20 成本感知長週期候選

範圍：修正 Phase 1「排名有 IC、組合無經濟價值」的目標錯位。只建立一個預先登記的
H20 候選；共用既有 PIT、purged walk-forward、成本模型與研究登記簿。裁決見 D-039。

## 1. 候選配置

`configs/ml_h20_cost_aware.yaml` 使用既有 `MlConfig`，固定：

- `horizon_days: 20`、`split.embargo_days >= 20`、vault 仍為最後六個月。
- `label.transform: cost_adjusted_return`。
- `label.adv_window_sessions`：訊號日向後看的 ADV 中位數窗口；不得使用未來金額。
- `label.position_notional_twd`：估計單一持倉交易金額，TWD。
- `label.downside_penalty`：若成本後超額報酬為負，再加重其絕對值的倍率，須 >= 0。
- 成本率來自 `configs/costs.yaml`；artifact fingerprint 必須同時綁定 ML 與成本配置。
- `features` 顯式列出全期較穩定的核心價量與營收因子；空清單的「全部因子」語意僅
  保留給 Phase 1 相容路徑，本候選禁止使用。

## 2. 標籤

對訊號日 `t` 與股票 `i`：

```text
gross_ret      = open(t+1+h) / open(t+1) - 1
market_excess  = gross_ret - 當日有效宇宙 gross_ret 平均
net_excess     = market_excess - estimated_round_trip_cost_rate
label          = net_excess - downside_penalty * max(-net_excess, 0)
```

估計股數為 `floor(position_notional_twd / open(t+1))`；買賣手續費、賣出稅與滑價公式
完全呼叫 SPEC-004 的 `trade_cost`。participation 使用
`position_notional_twd / median(amount[t-window+1:t])`。ADV 未滿窗口、價格/金額無效、
未來出場列不足或股數為零時 label 為 null，列保留且不得填 0。

`t` 的 ADV 只含 `<= t` 金額；`t+1` 與 `t+1+h` 僅存在於標籤結果，禁止進入特徵。

## 3. 訓練、識別與評估

- `ml train --config <path>` 選擇候選；未傳時仍讀 `configs/ml.yaml`，舊 H10 行為不變。
- 連續 label 仍使用決定性 LightGBM regression；不在本任務掃 objective/超參數。
- model id 的 fingerprint 與 research-log `params_hash` 同時含 ML config 及 cost config。
- 研究回測以薄因子 `ml.lgbm_net_20d` 讀 `ml_scores`；只能透過 `PitView`，並精確 pin
  本候選 model id。因子本身不讀模型、不做 IO。
- trial scope 固定 `ml:net_h20`；不得因重跑或輸出路徑不同拆分家族。
- 既有開發期可用於 walk-forward 診斷，但已被檢視，不稱為最終 OOS。vault 不得讀取、
  評估、畫圖或寫入報告。
- 本任務先檢查標籤 RankIC/分位經濟單調性；組合層另以零成本回測判 gross Sharpe。
  gross Sharpe < 0.5 即早停；通過才另立 buffer/雙週再平衡任務。

## 4. 驗收條件

- AC-1：合成兩股票資料可手算 gross、market excess、買賣成本與 downside penalty。
- AC-2：改動 `t` 之後才出現的 amount 不改變 `t` 的 ADV/cost/label；ADV 未滿窗為 null。
- AC-3：未來價格不足、價格/ADV 無效或估計股數為零時保留列且 label=null，不填 0。
- AC-4：相同輸入與配置輸出 bit-identical；公開函數具 type hints/docstring。
- AC-5：新 YAML 載入為 H20 cost-adjusted，embargo 覆蓋 horizon，核心特徵非空；非法
  window/notional/downside penalty 載入即失敗。
- AC-6：artifact fingerprint 對 ML config 或 costs config 任一變更都改變；Phase 1
  `make_model_id`/預設 CLI 相容。
- AC-7：training price query 只讀 `date, stock_id, open, amount` 並依期間 server-side
  過濾；不得直接讀 parquet。
- AC-8：walk-forward 不含 vault，run 登記在唯一 `ml:net_h20` scope；完整品質閘門全綠。

## 5. 範圍外

本任務不做 LambdaRank、H40、超參數掃描、EMA、buffer、產業/市值中性、組合最佳化、
生產推論接線或開 vault。這些只有在本候選通過 D-039 gross gate 後另立任務。
