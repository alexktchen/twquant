# 發動前訊號研究：資料準備度盤點

2026-09-07，T-469；對應 [SPEC-015](../specs/SPEC-015-prelaunch-research.md)。
本輪只查本機 research PG 的資料統計／schema，不查報酬標籤、不訓練、不改資料庫。
讀取會話設 `default_transaction_read_only=on`、`statement_timeout=20000`。
連線資料只從既有 `.env` 或環境讀取，未記入報告／命令輸出。

## 結論

不能直接用目前物化表訓練完整第一版：**三個分點因子各僅 5 日期**。
原始分點端點涵蓋 2022 年至今，可進一步盤點與補算，但尚未證明每天每股完整。
集保持股只有 **3 期**，維持第二期；不得把三期 forward-fill 當成多年持股歷史。
法人資料已超過舊 SPEC-010 盤點描述，應以本輪查詢為準，但仍不是訓練品質認證。

## 原始表端點與數量

| 表 | 最早 date | 最晚 date | 日期數 | 列數 | distinct 股票代號 |
|---|---|---|---|---|---|
| prices_daily | 2015-06-01 | 2026-09-07 | 未完成 | 未完成 | 未完成 |
| institutional_daily | 2015-06-01 | 2026-09-07 | 2,751 | 4,025,753 | 2,560 |
| broker_trades | 2022-01-03 | 2026-09-07 | 未完成 | 未完成 | 未完成 |
| shareholding_dispersion | 2026-07-03 | 2026-08-28 | 3 | 151,110 | 3,381 |

prices_daily、broker_trades 的完整 `count + distinct` 聚合超過 20 秒而取消；
改走 date 索引查端點成功，**未用推估補上數量**。日期數不是按交易日曆核對的覆蓋率，
股票數也未套普通股宇宙。連線期間可能有其他排程寫入，本報告不是不可變訓練快照。

## 已物化特徵

| 因子 | 最早 date | 最晚 date | 日期數 | 列數 |
|---|---|---|---|---|
| chip.broker_concentration_20d | 2023-02-20 | 2024-03-06 | 5 | 8,902 |
| chip.broker_count_diff_5d | 2023-02-20 | 2024-03-06 | 5 | 8,927 |
| chip.broker_persistence_10d | 2023-02-20 | 2024-03-06 | 5 | 8,927 |
| chip.trust_streak | 2015-06-01 | 2026-09-07 | 2,751 | 3,956,778 |
| volume.vol_ratio_5d | 2015-06-08 | 2026-09-07 | 2,741 | 4,659,047 |

`broker_concentration_20d` 的五日是：2023-02-20（1,739 股）、2024-03-01（1,792）、
2024-03-04（1,790）、2024-03-05（1,791）、2024-03-06（1,790）。
本表 count 包含 null value；有效值、常數、非有限值与逐股缺日仍待 T-472 查驗。

集保三期（available_at 以下換算為 Asia/Taipei）：

| date | 股票数 | available_at |
|---|---|---|
| 2026-07-03 | 3,350 | 2026-07-04 12:00:00+08:00 |
| 2026-07-09 | 3,350 | 2026-07-11 12:00:00+08:00 |
| 2026-08-28 | 3,374 | 2026-08-29 12:00:00+08:00 |

時間是**資料庫現值**，尚未外部核驗來源真實公布時間；不能把 date 當成已知日。

## 已確認的語意與缺口

- `volume.turnover_z_20d` 實為成交量時序 z，尚非流通股周轉率。
- `chip.broker_persistence_10d` 為合計淨買為正日數比例，沒有追蹤同一群分點身份。
- `factor_values` 欄位為 factor／stock_id／date／value／z／pct，沒有 available_at。
  必须確認它由來源的逐日 PIT 快照算出，而不是用最新修訂資料回填歷史。
- 現有因子有以列數 rolling 的實作；窗口跨缺日需先對市場交易日曆驗證。
- T-150 已列 broker 大表讀取效能修正；補算前先處理，避免直接啟動昂貴全期工作。
- 14 個基礎特徵本輪只抽查其中 5 個；完整共同期間／宇宙／公司行動／標籤成熟性仍未知。

## 查詢與重現

使用本機研究 DSN（禁止把 DSN 輸入文件或命令列），在唯讀 SQL 會話執行：

```sql
-- 各表完整數量；超時須報未知，不以 0 代替。
SELECT count(*), min(date), max(date), count(DISTINCT date), count(DISTINCT stock_id)
FROM serving.institutional_daily;

-- 大表端點可分別使用 date 索引，不能據此推論完整性。
SELECT date FROM serving.broker_trades ORDER BY date ASC LIMIT 1;
SELECT date FROM serving.broker_trades ORDER BY date DESC LIMIT 1;

SELECT factor, min(date), max(date), count(DISTINCT date), count(*)
FROM serving.factor_values
WHERE factor IN ('volume.vol_ratio_5d', 'chip.trust_streak',
 'chip.broker_concentration_20d', 'chip.broker_count_diff_5d',
 'chip.broker_persistence_10d')
GROUP BY factor ORDER BY factor;

SELECT date, count(DISTINCT stock_id), min(available_at), max(available_at)
FROM serving.shareholding_dispersion GROUP BY date ORDER BY date;
```

這些查詢只用於資料盤點；特徵與策略計算必須經 `PitView`。

## 閘門

修改前：`.venv/Scripts/python.exe -m twquant check --fast`，ruff 通過，
pytest **728 passed／23 skipped**（84.26 秒）。23 個 skipped 為原有，未增刪測試。
系統 PATH 的 Python 無安裝 twquant；使用已存在的專案 `.venv` 即可，未修改環境依賴。
交付後完整閘門結果記於 TASKS.md 的 T-469 完成註記。
