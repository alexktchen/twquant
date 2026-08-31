# HANDOFF 2026-08-31 — ML Phase 2 / T-072（Claude 接手）

## 目前狀態

- Git 分支：`main`。本文件建立時 T-072 維持 `in_progress`，不可標 done。
- Phase 1 已依 D-038 正式判定不部署；完整報告：
  `docs/research/ML-TRAINING-REPORT-2026-08-30.html`。
- Phase 2 決策已記於 D-039；規格為 `docs/specs/SPEC-011-ml-cost-aware.md`。
- Vault `2026-02-26~2026-08-28` **未開封**。不得讀取、評估或畫入開發報告。

## 已完成的 T-072 工作

1. 新候選固定為 H20 cost-adjusted continuous return，不掃參數：
   `configs/ml_h20_cost_aware.yaml`。
2. 新標籤 `cost_adjusted_return_labels`：
   - open(t+1)→open(t+1+h) 報酬減同日市場均值。
   - ADV 是訊號日及以前 20 日成交金額中位數。
   - 估計進出成本直接呼叫 `backtest.costs.trade_cost`。
   - 負的 net excess 依 `downside_penalty` 加重；null 保留、不填 0。
3. `ml train --config` 已支援獨立候選配置；舊 H10 預設路徑相容。
4. Artifact fingerprint 同時綁 ML config 與 `configs/costs.yaml`。
5. 新薄因子 `ml.lgbm_net_20d` 與研究策略
   `configs/strategies/ml_net_h20.yaml` 已加入，精確 pin model id。
6. 測試先行證據：
   - 新測試最初因 `read_prices_training` 不存在而 collection error。
   - 薄因子測試最初因未註冊 `ml.lgbm_net_20d` 而失敗。
   - 實作後 ML 目標測試 29 passed；薄因子相關 16 passed。
   - 最終交接閘門：ruff、format、mypy 全綠；pytest 409 passed / 22 skipped。

## 已訓練模型（本機 artifact，data/ 已 gitignore）

- Model id：`lgbm_net_h20_20260225_9a575e97`
- 目錄：`data/models/lgbm_net_h20_20260225_9a575e97/`
- 訓練命令：

```powershell
$env:TWQUANT_PG_DSN = <從 .env 載入，勿輸出>
.\.venv\Scripts\python.exe -m twquant ml train `
  --from 2020-01-01 --to 2026-08-28 `
  --config configs/ml_h20_cost_aware.yaml
```

- 資料：1,617 交易日、12 特徵、2,829,914 列、16 個 purged walk-forward windows。
- 標籤非 null：2,755,825 列，2020-02-07~2026-07-30。
- 開發期 OOS：2022-02-08~2026-02-25（vault 不在切分中）。
- 結果：**OOS RankIC -0.0188；16/16 windows 全負**。
- Window IC：
  `[-0.0021, -0.0689, -0.0477, -0.0359, -0.0135, -0.0019, -0.0111,
  -0.0105, -0.0097, -0.0461, -0.0119, -0.0067, -0.0014, -0.0227,
  -0.0004, -0.0039]`。
- 這是一個已登記的 `ml:net_h20` trial，不得刪除失敗結果或補掃參數。

## 已停止的 OOS 分數寫入

曾嘗試把約 173 萬列 walk-forward OOS 分數 upsert 到 PG `serving.ml_scores`：

```powershell
.\.venv\Scripts\python.exe -m twquant ml backfill-scores `
  --model data/models/lgbm_net_h20_20260225_9a575e97
```

- 現有 `executemany` 逐列傳輸過慢；依使用者要求已中止，孤兒 PG backend `36830`
  亦已精確終止。
- 交易完整 rollback；`model_id = lgbm_net_h20_20260225_9a575e97` 的 committed rows
  已確認為 **0**，沒有半套分數。
- **目前沒有 writer 在跑，不要假設 backfill 已完成。** 若要續跑，先考慮把
  `upsert_ml_scores` 改為 COPY staging + `INSERT ... ON CONFLICT` 的批次路徑並另立任務；
  或接受現有逐列路徑需要很長時間。不得開第二個 trial 或重訓模型。
- `.env` 不可顯示或 commit。

## 下一步（不要改候選）

1. 先完成或另行核可大量 backfill 的批次寫入方式，再寫入本模型 OOS 分數；完成後確認
   日期應為 2022-02-08~2026-02-25。
2. 跑唯一一次固定的零成本回測：

```powershell
.\.venv\Scripts\python.exe -m twquant backtest `
  --strategy configs/strategies/ml_net_h20.yaml `
  --start 2022-07-01 --end 2026-02-28 --cost-scale 0
```

3. D-039 gross gate：Sharpe < 0.5 立即早停。以目前 16/16 負 IC 判斷，高機率失敗；
   仍須保留實際回測證據。不得改 direction、反轉分數、換特徵、調 LGBM 或增加 trial。
4. 更新 D-039 執行結果、T-072 完成註記、CHANGELOG 與 HTML 報告。若早停，策略明確
   不部署，vault 保持封存。
5. 接手前完整 `python -m twquant check` 已全綠；完成回測與文件更新後仍須再跑一次，
   所有 AC 測試、ruff、format、mypy、pytest 全綠才可把 T-072 標 done。

## Git / 工作區注意事項

- `data/models/...` 與 `data/research_log/...` 在 `/data/` gitignore，只存在本機共享工作區。
- `cloudDailyReport/` 是接手前已存在的無關未追蹤資料（約 973 MB / 36,633 檔），不要加入。
- `AGENTS.md`、舊 Gemini handoff、ML HTML 與三張報告圖已納入本次 main commit 範圍。
- 不要提交 `.claude/settings.local.json` 或 `.env`。
