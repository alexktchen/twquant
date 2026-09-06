# SPEC-012 — 雲端 daily-pipeline 排程日期解析

範圍：修正 GitHub scheduled run 大幅延遲後把 runner 當下日期誤當交易日的問題，並把
cron 移離每小時起始尖峰。裁決見 D-040；每日資料流程本身仍依 D-030 / T-056。

## 1. 排程

- `.github/workflows/daily-pipeline.yml` 兩班為 UTC `11:23`、`14:43`（Asia/Taipei
  `19:23`、`22:43`），週一至週五。
- 班次時間必須由 workflow 傳給 resolver；Python 函數只接受配置化的 `slots`，不得
  寫死 19:23 / 22:43。
- `workflow_dispatch.inputs.date` 非空時直接採用該日；不得再做自動改寫。

## 2. 最近預定班次

`latest_scheduled_day(now, slots, lookback_days)`：

1. `now` 必須 tz-aware，先轉 Asia/Taipei。
2. 在 `[now-lookback_days, now]` 內建立週一至週五的所有候選班次。
3. 取 `candidate <= now` 的最大者，回傳其日期。
4. 無候選、slots 為空、slot 帶 timezone 或 lookback 無效時 raise，禁止猜測。

這裡的週一至週五是重建 workflow cron，不是台股交易日曆；平日國定假日仍交由官方
來源與品質閘門判斷。`FROM7` 是 DAY 往前七個日曆日，供融資券冪等補抓。

## 3. 驗收條件

- AC-1：2026-08-28 04:58（台北）解析為 2026-08-27。
- AC-2：2026-08-29 07:31（週六）解析為 2026-08-28。
- AC-3：週一首班前的多日延遲解析為前一個週五；首班後解析為週一。
- AC-4：naive now、空 slots、tz-aware slot、無候選皆 raise；同輸入決定性。
- AC-5：workflow cron 為第 23/43 分，scheduled 路徑呼叫 resolver，手動 date 路徑優先。
- AC-6：手動 dispatch `2026-08-28` 完整 pipeline 成功；run URL/結論寫入 T-073 註記。

## 4. 範圍外

不放寬 `--fail-on-degraded`、不把平日國定假日改成成功、不改擷取／品質／歸檔邏輯，
也不建立第二套排程服務。GitHub schedule 若被平台直接丟棄，另以缺跑監控處理。
