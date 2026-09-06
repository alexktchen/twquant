# 交接：ML 避雷濾網（negative screen）實驗（2026-09-04）

> **給執行 AI 的一句話：**不要再用 ML 猜前 20 名；本任務只測一次「先按既有非 ML 策略排序，再剔除 ML 最不看好的股票」是否能改善成本後績效。
>
> 目標分支：`claude/handoff-2026-08-31-ml-phase-2-c618a1`  
> 目標 worktree：`D:/project/twquant/.claude/worktrees/strange-kalam-3ec702`  
> 狀態：**研究提案，尚未回測、不得部署、不得開 vault**。

## 1. 為什麼做這個實驗

完整根因與既有數字見 [HANDOFF-2026-09-04-optimization.md](HANDOFF-2026-09-04-optimization.md)；不要在本任務重做那些診斷。

目前最重要的觀察是：

- 原 `rank_pct` 模型較穩定的能力在**避開最低分股票**，不是找出少數大贏家。
- 純 ML top-20 會把全部資金押在模型最不可靠的右尾，而且週頻交易成本曾高達約每年 21 個百分點。
- `ml_net_h20_m` 已證明「成本感知標籤＋月頻」能留下正報酬，但最大回撤與防運氣檢定仍未過，不可上線。
- 市場常見做法是讓模型提供一項證據，再由組合層處理成本、風險與現有持股；模型分數不是直接下單名單。

因此第一個換架構實驗採用**否決式濾網**：原策略負責找候選，ML 只說「這檔太差，先不要買」。

## 2. 這次唯一允許的候選（事前寫定）

### 2.1 母體策略

沿用 `configs/strategies/reversal_revenue_m.yaml` 的經濟設定：

- 訊號：`price.reversal_5d` 0.7、`fundamental.revenue_yoy` 0.3。
- 組合：`zscore_weighted`。
- 排程：每月第一個交易日產生訊號，下一個交易日開盤成交。
- 持股：20 檔、等權重、單檔上限 5%。
- 宇宙：TWSE＋TPEX、20 日成交金額中位數至少 3,000 萬 TWD、股價至少 10 TWD，並保留既有衛生濾網。

母體策略是比較基準，不得改權重、持股數、再平衡日或宇宙門檻。

### 2.2 ML 濾網

新增一個策略 `ml_negative_screen_h20_m`，唯一的新經濟變數如下：

```yaml
trial_scope: ml_negative_screen_h20

screens:
  - type: rank_floor
    factor: ml.lgbm_net_20d
    params:
      model_id: lgbm_net_h20_20260225_9a575e97
    exclude_at_or_below: 0.10
    missing: error
```

精確語意：

1. 對和母體策略完全相同的 PIT 可投資宇宙，計算原本的反轉＋營收分數。
2. 另外透過 `PitView` 讀取指定模型的 `rank_pct`；數值越高代表模型越看好。
3. 先排除 `rank_pct <= 0.10` 的最低一成股票，再依原分數由高到低選 20 檔。
4. 被排除的股票由原分數排名下一位、且通過濾網的股票補上；最後仍為 20 檔等權重。
5. 任一訊號日 ML 分數缺失，或通過後不足 20 檔，必須帶日期、模型、缺失／剩餘檔數 `raise`；不得靜默丟列、填 0、少持幾檔或自動放寬門檻。
6. 同分時以 `stock_id` 字串升冪打破平手，保留決定性與前導零。

選最低 10% 的理由是既有診斷所說的「最低一桶」；這次只登記一個門檻。**不得看完結果再試 20%、30%、平滑、緩衝帶或正向加碼。**任何第二個門檻都是新 trial，必須另立決策。

### 2.3 這不是完整 enhanced index

本 repo 目前沒有已驗證的 PIT 指數成分權重、產業中性與 tracking-error 最佳化器。不得用「全宇宙等權」冒充市場指數，也不得為了名稱好聽臨時發明 benchmark weights。

這次是最小可歸因版本：**既有可交易策略＋ML 否決權**。若它成功，下一案才能正式做 benchmark-weighted enhanced index。

## 3. 執行前的硬性 readiness gate

先依序檢查；任何一項未滿足都不能宣告實驗通過：

1. **T-111 完成。**目前 64 檔後來下市的公司缺 `stock_meta`，而 `ml_h20_cost_aware.yaml` 含 `price.residual_reversal_5d`。這會直接污染左尾與「避雷」結論。
2. **T-098 完成，或結果明確標為 provisional。**現有回測未計股利，絕對年化報酬約低估 3 個百分點，且不同持股可能有殖利率傾斜。未修正時可以做機制診斷，但不得做最終上線裁決。
3. **若資料修復後需重訓，先完成 T-073。**目前 model fingerprint 不含程式碼／資料修復版本，直接重訓可能讓同一 model id 指向不同內容，禁止覆寫既有 artifact。
4. `ml_scores` 必須是 purged walk-forward 的樣本外分數，並精確 pin model id；不得用最終模型回頭推論整段歷史。
5. 先跑 `python -m twquant check --fast`。基線紅燈依 `AGENTS.md` 寫入 `DECISIONS.md`，不得順手修別的模組。
6. `TASKS.md` 已有 T-111 `in_progress`。依 repo「一次只做一個任務」規則，新任務不可同時標 `in_progress`；先確認 T-111 已結束或由使用者明確重新排序。

若只完成程式與合成測試、尚未滿足 1～4，交接狀態只能寫成「implementation ready, backtest blocked」，不可消耗正式候選名額。

## 4. 接手後先建立規格與任務

這是 D-052 指定的「換架構」，不能偷偷塞進舊 T-110：

1. 確認 `SPEC-013` 尚未被使用；建立 `docs/specs/SPEC-013-ml-negative-screen.md`，引用 SPEC-000、SPEC-003、SPEC-004、SPEC-011，不複製它們全文。
2. 在 `DECISIONS.md` 新增 2026-09-04 的事前登記，逐字記下本文件 §2 的唯一候選、期間、成本情境、scope、門檻與早停規則。
3. 在 `TASKS.md` 使用當時最小的可用任務編號，列出 §6 的 AC，狀態改 `in_progress`。
4. 如果既有規格或資料狀態和本文件矛盾，依 `AGENTS.md` §6 開待裁決 D-xxx；不得自行換母體策略或門檻。

使用者已授權「把濾網寫成交接給其他 AI 跑」，但這不等於授權開 vault、部署或放寬品質閘門。

## 5. 建議的最小實作範圍

預期只動以下範圍；若需更多檔案，先在任務完成註記解釋：

- `src/twquant/strategy/config.py`
  - 新增嚴格驗證的 `ScreenConfig`。
  - `type` 第一版只允許 `rank_floor`。
  - `0 < exclude_at_or_below < 1`。
  - `factor` 與精確 `model_id` 必填；未知欄位仍 `extra="forbid"`。
  - `StrategyConfig.screens` 預設空清單，確保所有舊 YAML 行為逐位元不變。
- `src/twquant/strategy/screens.py`
  - 純函數實作 rank-floor 濾網；輸入／輸出皆為 DataFrame，不讀檔、不連網、不寫檔。
- `src/twquant/strategy/build.py`
  - 流程固定為：宇宙 → 母體訊號 → 母體 score → screen 因子 → screen → selection → weighting → overlay。
  - screen 因子也只能由同一個 `PitView` 計算。
- `configs/strategies/ml_negative_screen_h20_m.yaml`
  - 複製 `reversal_revenue_m.yaml` 的經濟設定，只新增 `trial_scope` 與 §2.2 的 screen。
- `tests/test_strategy_config.py`、`tests/test_strategy_build.py`，必要時新增 `tests/test_strategy_screens.py`。
- `docs/specs/SPEC-003-strategy.md`：介面有變更時同步配置 schema；新架構的實驗規則留在 SPEC-013。

不要修改 `tests/golden/`，不要碰因子、模型超參數、標籤、成本模型或生產 pipeline。

## 6. 驗收條件（每條測試名稱都含 AC 編號）

- **AC-1 配置驗證**：合法 screen 可載入；門檻 0／1、缺 factor、缺 model id、未知欄位都在載入時失敗，訊息指出欄位。
- **AC-2 手算選股**：合成 25 檔，原策略前 20 中有 3 檔落在 ML 最低一成；三檔被移除，原排名第 21～23 且通過者依序補入。
- **AC-3 執行順序**：證明濾網在母體分數完成後、top-n 之前執行；不得把 ML 分數混成第三個正向 alpha 權重。
- **AC-4 缺值拒絕**：任一宇宙股票缺指定模型分數即 `raise DataError`，訊息含表名 `ml_scores`、訊號日、model id、缺失檔數與範例 stock id。
- **AC-5 反前視**：在 t 日之後追加／修改價格或 ML 分數，不改變 t 日輸出。
- **AC-6 舊策略相容**：`screens: []` 或欄位省略時，`reversal_revenue_m` 與 `ml_net_h20_m` 的輸出 bit-identical。
- **AC-7 權重不變量**：每次恰好 20 檔；權重和 ≤ 1+1e-9；無負權重；單檔 ≤ 5%；stock id 保持字串。
- **AC-8 決定性**：相同輸入跑兩次，目標權重與整份 `BacktestResult` bit-identical；同分時順序固定。
- **AC-9 模型 pin**：同表存在其他 `lgbm_net_h20_*` 分數時，只能讀配置指定的 model id。
- **AC-10 research log**：策略經濟設定、screen 門檻、model id、cost scale、期間或 participation 任一改變，canonical hash 必須改變；純 name／description 不改變。
- **AC-11 機制診斷**：輸出每次再平衡被 veto 的檔數、被 veto 與通過股票後續 20 日 gross excess；資料不足不得以 0 代替。

新公開函數須有 type hints 與一行 docstring；所有測試先紅後綠，保留先行失敗證據。

## 7. 唯一允許的回測順序

期間與資金沿用既有比較，避免換樣本：

- 期間：`2022-07-01`～`2026-02-28`。
- 初始資金：10,000,000 TWD。
- 單股 ADV 參與率：5%。
- 資料源：本機完整 PG；所有資料仍經 `PitView`。

先跑 **1× 真實成本**：

```powershell
uv run --all-groups python -m twquant backtest `
  --strategy configs/strategies/ml_negative_screen_h20_m.yaml `
  --start 2022-07-01 --end 2026-02-28 --cost-scale 1 `
  --capital 10000000 --participation 0.05 --pg
```

若 1× Sharpe < 0.5，立刻早停並關閉本候選；只保留這筆失敗，不跑其他 screen 門檻。

若 Sharpe ≥ 0.5，再用**完全相同策略**跑 `--cost-scale 0` 與 `--cost-scale 1.5`。三種成本倍率是同一候選的敏感度情境，不得趁機改 YAML。

比較表至少同列：

1. `reversal_revenue_m`（母體基準）。
2. `ml_negative_screen_h20_m`（本候選）。
3. `ml_net_h20_m`（目前最佳純 ML 月頻候選）。

三者必須同期間、同資金、同 participation、同資料修復版本與同成本倍率；否則比較無效。

## 8. 通過門檻與停止條件

沿用 D-038 §5，不因結果調整：

- 1× CAGR > 0。
- 1× Sharpe ≥ 0.5。
- MDD ≤ 30%。
- 年化單邊周轉 ≤ 1500%。
- 相對最強線性月頻基準的 Sharpe 提升 ≥ 0.25。
- 1.5× 成本後 CAGR > 0、Sharpe > 0。
- 四段期間至少三段正報酬，median fold Sharpe > 0。
- DSR ≥ 0.95；母體必須包含同 scope 的全部 distinct trials，不能只留贏家。

另外要回答機制是否成立：被最低一成濾掉的股票，其後 20 日 gross excess 平均必須低於通過組，且四段中至少三段方向一致。這是診斷條件，不可取代上述上線門檻。

任一上線門檻失敗：結論寫「濾網未通過」，不得試下一個 percentile、改成 bottom 20%、加遲滯、改持股數或打開 vault。若機制成立但組合門檻失敗，下一案可另立「benchmark-weighted enhanced index」規格；本案仍算失敗。

## 9. 報告與收尾

所有結果集中更新到唯一的投資者版文件：

`D:/project/twquant/data/reports/台股ML選股實驗總覽.html`

不要在 worktree 另生第二份 HTML。更新時：

- 用白話說明：「原策略先選股，ML 只把最不看好的最低一成擋掉」。
- 同時列成功與失敗情境、三種成本、四段結果、周轉、最大回撤、DSR、逐筆賺賠與 veto 診斷。
- 保留「未含股息／下市樣本」警語，直到對應修復有實測證據；不能因本候選好看就刪除。
- 新結果必須來自 `data/research_log` 或可追溯 artifact，不把數字硬寫進正文。
- 若 readiness gate 未全過，標題須含「暫定／資料修復前」，不能寫成已通過。

最後依 `AGENTS.md` 完成：

1. `python -m twquant check` 全綠。
2. `git diff --stat` 確認沒有範圍外變更，也沒有 `tests/golden/` 變動。
3. `TASKS.md` 寫 AC 對應測試與閘門摘要並標 done。
4. `CHANGELOG.md` 加日期、任務編號與一句話。
5. `DECISIONS.md` 記實際結果、是否早停，以及下一步是否正式關閉。

## 10. 明確禁止

- 不開 vault，不讀、不畫、不評估 vault 期間。
- 不重訓模型，除非資料修復要求且 T-073 已先解決。
- 不把未來資料、最終模型回溯分數或直接 parquet／DuckDB 查詢傳入策略。
- 不把 ML 分數加成正向訊號；本案只給否決權。
- 不掃門檻、持股數、頻率、特徵、模型或成本參數。
- 不把 NaN 填 0、不靜默 drop、不用例外後繼續跑。
- 不直接改 golden、不放寬測試、不修改成本模型。
- 不把研究失敗從 HTML 或 research log 移除。

