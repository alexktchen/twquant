# 交接：2026-09-02 資料重建夜（T-079 → T-082 → T-010/D-044 → T-080 進行中）

> 前一份是 `docs/HANDOFF-2026-08-31-ml-phase2-rootcause.md`（根因與免費診斷）。
> 那份仍然有效；本文件接續它，記錄 09-01 夜～09-02 的資料層重建。
>
> 分支：`claude/handoff-2026-08-31-ml-phase-2-c618a1`
> 金庫期 2026-02-26~2026-08-28 **維持未開封**。

---

## 1. 一句話現況

價量、指數、評價三條資料主幹已修好並補齊，三個評價因子（本專案第一個評價維度）
已上線；**因子全期重物化執行中**（5 分片，ETA 約 4 小時）。重物化完成後才輪到
重訓模型 → backfill-scores → 重跑階梯，在那之前所有策略數字仍不可信。

## 2. 資料修復與稽核（T-079，已完成）

- `prices_daily` 共 **43 個汙染交易日**已重抓修復（7 天陳舊快照 + 35 天日期互相複製
  + 1 天迭代後浮現）。偵測器迭代三輪收斂：>5% 重複率的日子 **35 → 2 → 0**。
- `institutional_daily` 修 2 天，其中 **2025-01-24 是幽靈日期**（農曆年封關非交易日
  卻有 908 列），已刪除。**官方日曆確認**：該日記載為「市場無交易，僅辦理結算交割作業」。
- **官方日曆核對**（`twse.com.tw/rwd/zh/holidaySchedule`，帶年份參數）：
  2021 年起 1,374 個交易日，幽靈日期 **0**、缺交易日 **0**。
- **颱風盲區**（使用者指出）：官方假期表只含預先公告的假期，颱風臨時停市不在其中。
  實測「平日、無行情、不在假期表」共 7 天，全部對應已知颱風
  （2023-08-03 卡努、2024-07-24/25 凱米、2024-10-02/03 山陀兒、2024-10-31 康芮、
  2026-07-10），TWSE 亦回「沒有符合條件的資料」。故資料完整，但**權威日曆須為
  官方假期表 ∪ 臨時停市清單**——見 T-081。
- **品質閘門**（AC-2 已實作）：`data/quality.py` 的 `duplicate_snapshot_dates` 與
  `phantom_dates`，測試先行。真實資料驗收：對修復後資料通過、對備份的汙染資料
  攔下全部 35 天。

## 3. T-082：`index_daily` 從未進 PG（已修）

`index_daily` 不在 `MIRROR_SPECS` 中，全庫也無任何寫它進 PG 的程式。因子物化在
D-026 後改讀 PG，故 `price.rs_market_20d` 只能在 PG 恰好有的 52 天算出來——
這一個缺陷同時解釋了它在 Phase 1 模型中 **split=0／gain=0.00%**、08-29 交接文件記的
「只有 32 天覆蓋」、以及 Phase 2 的 12 特徵清單裡有一欄全空。
修復後 TAIEX **1,619 列**（與行情日數完全相符）。

## 4. T-010／D-044：FinMind 與評價因子

- **官方端點在大量回補下必被封鎖**：以 TWSE rwd 回補 1,392 天，第 41 天（約 120 請求）
  起全部回 HTTP 428/307 的 WAF 挑戰頁；瀏覽器 UA、跟隨重導、先取 cookie 皆無效。
  這與 D-015（2026-07-05）記載的完全相同。
- D-044 裁決走 FinMind（前提改變：D-015 註明「付費」，實測**免 token 可用**；
  使用者已註冊免費帳號 600 次/小時，token 存 `.env`）。
- **映射先驗證再信任**：`institutional_daily` 於 2026-08-28 全市場抽樣 **25/25 逐位元全等**。
- **PER/PBR/殖利率已抓齊**：2,064 檔、**2,921,854 列 / 2,050 檔 / 1,619 個日期**，
  已進資料湖與 PG（`stock_valuation`）。
- **三個評價因子上線**：`fundamental.earnings_yield`／`book_yield`／`dividend_yield`，
  皆 direction=+1。用**倒數**而非 PER/PBR 本身（實測 PER 最大 24,950、PBR 最大 1,442.5，
  直接用會被極端值主導）。PBR<=0 先轉 null 再取倒數——與 T-079 的 inf 缺陷同一類防護。

## 5. 效能發現：因子物化為 O(n²)

- `serving materialize`（讀 PG）實測 **~100 秒/交易日** → 1,619 天約 **45 小時**。
- 改讀本機資料湖僅降到 **53~84 秒/日**（非 08-30 交接文件所稱的 10 秒），
  1,619 天仍約 29 小時。
- **根因**：`lookback_days` 在 `@factor` 宣告了，但 `compute.py` **完全沒有引用它**
  ——每個交易日都重讀整個歷史（1,619 分區、921 萬列），而非只讀回看窗。
  修這個是核心路徑改動，**未動**（見 T-083）。
- **當前作法**：比照 08-30 的做法分 5 片並行（PK upsert 無鎖衝突），ETA 約 4 小時。
- 附帶：`scripts/export_pg_to_lake.py`（新）把 PG 匯出成湖格式，0.6 分鐘完成 5 張表。
  **時區陷阱**：psycopg 把 timestamptz 讀成 UTC，而 schema 的 `TW_DT` 是 Asia/Taipei，
  不一致會讓 PitView 的 `available_at` 比較在 polars 拋錯、**整個因子被靜默略過**
  （首次匯出即踩到，表現為「0 因子列」而非報錯）。

## 6. 待辦（依序）

1. **等重物化完成**（5 分片，`data/logs/mat_*.log`）
2. 封存舊 artifact 後**重訓 H10**（T-073：`make_model_id` 不含程式碼/資料版本，會靜默覆寫）
3. `backfill-scores` → 重跑階梯第 1~3 級與 0× 反事實
4. 與 D-038／D-043 的登記值對照

## 7. 需要使用者裁決（未自行決定）

- **D-038 是否撤銷**：其唯一依據（0× 成本 Sharpe +0.22 < 0.50）已被證明建立在壞資料上；
  修復價格後的初步重跑為 **+1.27**。但當時因子仍是髒的，確切數字要等本次重建完成。
- **D-038 §4 那 5 個事前登記的降周轉 trial 是否動用**（固定額度、不可補）。
- 是否訂閱 FinMind 付費層一個月拉歷史（**台灣還原股價**可直接解掉 T-009/T-011；
  免費層缺「減資恢復買賣參考價格」，自行推算的還原價在建構上不完整。
  另有**市值**資料——本專案目前完全沒有市值欄位，無法市值加權或控制 size 曝險）。

## 8. 環境雜項

- **相對 `data_dir` 陷阱**（今晚絆到四次，見 T-084）：`configs/system.yaml` 的
  `data_dir: data` 是相對路徑，從 worktree 執行任何指令都會讀寫 worktree 的 `data/`
  而非主資料夾；`TWQUANT_DATA_DIR` 環境變數**未被 `load_config` 採用**。
  症狀是「回補成功但主資料夾沒東西」「sync 顯示 +0 日」「research_log 落在 worktree」。
- 本機 PG：docker `twquant-pg-1`（`localhost:5433`）。DSN 與 `FINMIND_TOKEN` 在
  `D:\project\twquant\.env`（gitignore，不得進 repo）。
- 重建湖：`D:/project/twquant/data/lake_rebuild`（由 PG 匯出，含全部修復）。
- 汙染資料備份：`D:/project/twquant/data/archive/corrupt_days_backup/`。

---

## 9. 09-02 中午的重建重啟（重要：前一輪 796 天全部作廢）

### 9.1 為什麼作廢

**`stock_meta` 從未被匯出到重建湖**（`export_pg_to_lake.py` 的 `_DEFAULT_TABLES` 沒有它，
且它無 `date` 欄，`partition_by("date")` 會直接 raise）。而 `price.residual_reversal_5d`
宣告 `tables=[prices_daily, stock_meta]`——缺表時 `materialize._compute_universe` 的
`except DataError` 只記 **DEBUG** 就繼續，整天仍回報成功。

後果：已跑的 796 天**全部缺這個因子**，而它同時是
- `configs/ml_h10_repaired.yaml` 25 個 pin 特徵之一，且
- `configs/serving.yaml` 僅 3 個 `composite_factors` 之一。

已修：`_DEFAULT_TABLES` 補上 `stock_meta` 與 `stock_valuation`，並讓無 `date` 欄的
緩變表寫成 `date=_all` 分區（比照 `Warehouse.write`）。修完實測三個年代皆算得出
`residual_reversal_5d`（781~1,919 檔）。

### 9.2 順帶查出的第二個汙染（T-087）

發車前清點列數分布，發現 **8 個交易日只有 TWSE、整條 TPEX 缺席**（列數 1,189~1,308
vs 鄰近 5,846~6,736）。已全數重抓並同步進湖與 PG，並新增 `quality.sparse_dates`
偵測器（測試先行；真實資料驗收：修復前正好抓 8 天、修復後 0 天）。

### 9.3 08:36 主機關機

前一輪 6 個分片在 08:36 同時消失、日誌乾淨截斷無 traceback——事件記錄顯示
**主機於 08:36:23 由 Start 選單發起關機**，11:46 才開機。不是程式崩潰，Docker 亦隨之停止。

### 9.4 本輪配置

6 個**按成本平衡**的分片（因子物化成本隨歷史長度增加，故早期分片給多天）：

| 起 | 迄 | 天數 |
|---|---|---|
| 2020-01-02 | 2022-09-16 | 661 |
| 2022-09-19 | 2023-11-06 | 274 |
| 2023-11-07 | 2024-09-16 | 210 |
| 2024-09-18 | 2025-06-16 | 177 |
| 2025-06-17 | 2026-01-28 | 156 |
| 2026-01-29 | 2026-09-01 | 141 |

以 PowerShell `Start-Process` 啟動（脫離 bash session），日誌 `data/logs/mat_<起>.log`。
CPU 85%、可用記憶體 3.4 GB——**不可再加分片**。實測 ETA 約 6~7 小時。

### 9.5 每日只有 21 個因子（30 註冊 − 2 ml − 7 籌碼）

`ml.*` 兩個要等模型訓練後 `backfill-scores` 才有，屬預期。
**7 個籌碼因子完全算不出來**：`institutional_daily` 全期只有 **227 日**、`margin_daily`
**205 日**（2020 與 2021 完全空白，2025 的 141 日還零散到整個 6 月都沒有）。
故 25 個 pin 特徵中有 **7 個（28%）在絕大多數日期為 null**。
這是既有狀態（非本次退化），但**重訓結果須據此解讀**——見 D-044 的籌碼回補優先序。

### 9.6 T-083 不能照原案做（實測）

原以為照 `lookback_days` 裁切視圖可加速 3~30 倍。實測：加速有（×2 邊際下 2.2~5.6 倍），
**但會改變因子值**，因為因子用的是**列基**位移而非日期基，稀疏個股在窗內拿不到足夠列數。
且 `volume.turnover_z_20d` 有 111~223 檔值不同，代表其 `lookback_days=25` 宣告不足。
詳見 TASKS.md T-083 的實測註記。**本輪重建仍走原路徑，不套用裁切。**

---

## 10. T-080 完成 → 逐筆診斷 → D-046 降周轉研究（09-02 晚）

### 10.1 T-080 結果（詳見 TASKS T-080 完成註記）
重訓 `lgbm_h10_20260225_b2348c46`：OOS RankIC +0.0680 → **+0.0796**、階梯 1–2 級全過（分位嚴格單調、t 9.13）。
回測 886 日：1× CAGR −14.29% → **−0.01%**、0× Sharpe +0.223 → **+1.210**、四段 0× **4/4 正**。
訊號是真的，**成本吃掉全部毛利**（6.84M TWD ＝ 初始資金 68%，年化周轉 2,824%）。

### 10.2 逐筆對帳（UI：<https://claude.ai/code/artifact/1b9e97a7-e166-4879-b07b-ef1cb3a030a6>）
FIFO 配對往返：0× 勝率 54.0%、賺賠 +4.66%/−3.68%、PF 1.36、EV **+0.829%/筆**；1× 勝率 47.1%、PF 0.99、
EV +0.092%。每趟往返成本 ≈ **0.74%**。1× 逐年勝率 49→42% 下滑而 0× 平（54–57%）：成本拖累隨時間加重。
CLI 印的「勝率」是**日勝率**，與逐筆勝率是兩回事。

### 10.3 三個免費診斷（不佔 trial 額度；腳本在對話 scratchpad `diag_turnover.py`）
1. **IC 不衰減、反而上升**：h=1 0.048 → h=5 0.054 → h=10 0.056 → **h=20 0.062**，IR 0.36→0.54。
2. **前 20 名週留存 36%**（每週換 12.8 檔）；離開者 21–30 名 15%、31–50 名 17%、**>100 名 39%**；
   排名週自相關 0.62。→ 周轉是分數重排不是邊緣抖動；對症是平滑與降頻，不是遲滯。
3. **成本 = tick 下限 + 證交稅**：模型化往返 0.645%，滑價 0.245% 中 tick 下限 0.214%、ADV 衝擊≈0；
   按 ADV 分桶完全平。→ 篩宇宙無效，只能少交易。

### 10.4 一個我先前說錯、已在 DECISIONS 更正的事
D-038 登記的「buffer exit-rank 30」是**第 30 百分位**（≈第 178 名），不是「前 30 名」——D-043 明寫它與
T-076 的 exit 0.70 是同一物，所以那個候選**已經跑過並失敗**。診斷 2 解釋了原因。

### 10.5 D-046：撤銷 D-038、重新登記 4 候選
早停 §3 三條件在修復後資料全過（0× CAGR +21.9%、Sharpe 1.21、四段 4/4）。新登記（固定、不得補）：
`ml_rank_m`（月頻）、`ml_rank_2w`（雙週，T-089）、`ml_rank_ema10`（EMA10，T-090）、`ml_rank_ema10_2w`；
scope `bt:ml_rank_h10_turnover`；晉升門檻沿用 D-038 §5。登記先 commit（50d0f5c）再回測。
`lgbm_net_h20` 依 D-040 修復重跑，不佔額度：OOS RankIC **+0.0363**（16 窗，1 窗負）。

### 10.6 已實作
- T-089 `rebalance.schedule: biweekly`——ISO **偶數週**（不是「從 start 起每隔一週」），讓不同起日的回測
  共用同一套再平衡日曆。
- T-090 ml 因子 `smooth_days`——列基 EMA（span=N、adjust=False），缺日不補 0；`assert_no_lookahead` 接受
  `params`。閘門 484 passed。

### 10.7 候選結果與判定（四個全到）
完整表與判定見 DECISIONS D-046「結果」段。摘要：1× 淨 Sharpe 月頻 0.39 > EMA10+雙週 0.30 > 雙週 0.28 >
EMA10 0.16 > 週頻 0.09 > H20 0.13；**無一過 0.5**。候選 4（EMA10+雙週）過 4/6 門檻、唯一 MDD < 30%。
兩個假設被推翻：EMA10 平滑損毛訊號比降頻更多（重排帶資訊）；H20 標籤不降周轉（換股由選股規則決定）。
**Phase 1 不部署**（D-038 §6）；下一代候選見 D-047 待裁決（門檻選股／持有到期／月頻+top40／轉籌碼回補）。

**DSR 註記**：門檻 0.95 極高——母體 16 個 distinct trial、n_obs 886，連 0× Sharpe 1.21 的 run 也只得 0.65。
判定時 DSR 須與其他門檻分開陳述。

---

## 11. D-047：兩個候選在登記前被推導否決 → 轉特徵層（09-02 深夜）

### 11.1 使用者先選 A（門檻選股）+ B（持有到期），推導把兩者都否決（未耗額度）
- **A**：以 OOS 分數對 10 日開盤對開盤毛報酬推導（宇宙內、993 日、每日 593 檔）——
  **沒有任何 top 百分位的 E[fwd10] ≥ 往返成本 0.737%**（top 0.5% 僅 0.650%），且**第 1～30 名的邊際報酬是平的**
  （0.44–0.59%）。分數梯度分不出「值得做」的交易；登記規則是「門檻由推導得出、不掃描」，故不登記。
- **B**：週頻 0× 逐筆按持有長度分桶：5–7 日曆日（62%）**+1.03%**、11–14 日 +0.52%、15–21 日 **−0.78%**。
  報酬集中在進場後第一週；強制持有 10 日＝候選 2（雙週）已測出的結果。
- 同一份推導的正面線索：**第 21–30 名的預期報酬與前 20 名相同 → 放寬 top_n 幾乎不損毛利**。
  C1（月頻+40）／C2（週頻+40）／C3（雙週+EMA10+40）已推導、**未登記**，留待特徵層補齊後。
- 公平基準：`reversal_revenue_m` 修復重跑 1× Sharpe **+0.31**（舊登記 −0.72 是汙染資料）→ ΔSharpe ≥ 0.25 意味 ML 須 ≥ 0.56。

### 11.2 裁決：不開新 trial，轉籌碼回補（T-092）與評價因子驗證（T-093）
理由：25 個 pin 特徵中 7 個籌碼因子在 86% 的日期為 null、2 個從未有值（D-045）、3 個評價因子未進模型。
特徵層這麼薄時再掃選股規則，是用有限額度最佳化一個資訊不完整的模型。

### 11.3 因子現況（30 個；`factor_values` 1,620 日）
| 類別 | 覆蓋 | 備註 |
|---|---|---|
| price 13 | 82–100% | 全 pin25；reversal_5d／residual_reversal_5d 為 composite |
| volume 2 | 99–100% | pin25 |
| fundamental 2（營收） | 97–99% | pin25；revenue_yoy 為 composite |
| fundamental 3（評價） | 100% | T-010 新加，**未進模型、未跑 IC** → T-093 |
| chip 5（法人） | **14%** | 資料僅 227 日 → T-092 |
| chip 2（融資） | **0%** | D-045 PIT 缺陷（available_at 21:00 > 20:00 截點）|
| ml 2 | — | 策略端讀 ml_scores，不進 factor_values |

### 11.4 正在跑（若主機再被關機，從這裡續）
- **FinMind 籌碼回補**（bhbodqo2v）：`scripts/backfill_finmind.py`，法人 → 融資券串行，
  輸出 `data/staging/finmind_institutional/`、`finmind_margin/`，每檔一 parquet、**可續傳（重跑即續）**、
  402 自動等額度、進度寫 ingest_log。實測 ~6 秒/檔 → 每個 dataset 約 3.3 小時。
  完成後的步驟（T-092 AC-2）：用 `sources/finmind.py` 的 `institutional_net`／`margin_balances` 映射 →
  對既有官方資料日期抽驗一致 → 寫湖（`lake_rebuild`）與 PG → `quality.sparse_dates` → 物化 7 個籌碼因子
  （全表 7 小時；T-094 的因子子集物化未做）。**融資因子在 D-045 裁決前仍會是 0 列**。
- **評價因子 IC 階梯**（bpbmsiznv）：`ic-report --factor fundamental.{earnings,book,dividend}_yield --horizons 5,10,20
  --start 2020-01-01 --end 2026-02-25 --pg`，登記 `ic:<factor>`；research_log 落在 worktree `data/`，須併回主庫。
- **四候選逐筆統計**（bjoa22bxy）：`tradewin.py` 8 次，產出 `trades_<name>_<k>x.json`；
  彙整用 `summarize_t091.py`（T-091 AC-2）。已落地：月頻 1× 逐筆勝率 44.5%／PF 1.106／EV +0.494%（0× 48.7%／1.306／+1.155%）；
  雙週 1× 47.8%／1.044／+0.183%。

### 11.5 待使用者裁決
- **D-045**：融資因子的 PIT 修法（建議 A：因子輸出日期前移一日）——T-092 資料進來後才有意義。
- 是否訂 FinMind 付費層（還原股價、市值）——與 T-009／T-011 相關。

---

## 12. D-048 管線已建好、只等資料（09-03 凌晨）

### 12.1 已落地（閘門 494 passed）
- **T-088**：融資因子依 D-008 把值標到下一交易日（`chip._to_next_session`，join_asof forward）。
  兩個融資因子首次能產出值；`short_margin_ratio` tables 加 prices_daily 供日曆。
- **T-094**：`materialize_day(..., factors=[...])` 因子子集物化；子集含 composite 因子或未註冊名字即 raise；
  只寫 factor_values／factor_cross_stats。`scripts/backfill_factors.py` 第 4 個位置參數。
- **T-095**：`forward_returns` 擋 `close<=0`（第四次除以零）；六因子 IC 重跑後才拿到乾淨的多空價差。
- **T-088 AC-3**：`ml.dataset.assert_feature_coverage` 接在 `ml train` 的 assemble 之前——任一 pin 特徵全空即拒絕訓練，並印出覆蓋最低 5 個特徵。預設門檻 0；刻意不加 MlConfig 欄位（會改 params_hash 與已登記的 model_id，T-078）。
- 閘門：**498 passed**（ruff／format／mypy 全綠）。
- **自動管線（背景 bceuu9rub）**：等 FinMind 兩個 dataset 結束 → 載入 check/write → sparse_dates 與覆蓋 ≥1,600 日 → 6 分片只重算 7 個籌碼因子（日誌 `data/logs/chip_<起>.log`）→ verify_rebuild 印籌碼因子覆蓋。**到驗收為止就停**，訓練用 `scratchpad/run_t096.sh`（或 §12.3 手冊）手動發。
- **D-048 配置**：`configs/ml_h10_v2.yaml`（28 特徵，model_id `lgbm_h10_20260225_5526f2c2`）、
  `configs/strategies/ml_rank_v2.yaml`（只換 model_id，週頻 top-20，scope `ml_rank_h10`）。
- **載入器**：`scripts/load_finmind_chip.py institutional|margin <staging> --check|--write`——重疊日期只比對、
  任何值不一致即 raise、只寫湖與 PG 都沒有的日期。

### 12.2 T-093 結果（全樣本 1,471–1,486 日；OOS 期 2022-01-14 起同號同量級）
| 因子 | RankIC 5/10/20 日 | t | 五分位多空價差 5/10/20 日 |
|---|---|---|---|
| earnings_yield | +.034/+.036/+.036 | 15/15/13 | **−0.10/−0.25/−0.60%** |
| book_yield | +.038/+.048/**+.061** | 14/18/**22** | +0.07/+0.15/+0.32% |
| dividend_yield | +.040/+.039/+.034 | 17/15/12 | **−0.41/−0.82/−1.70%** |
| residual_reversal_5d（對照，−1） | −.047/−.033/−.029 | −20/−15/−14 | −0.23/−0.16/−0.11% |
| reversal_5d（對照，−1） | −.045/−.026/−.023 | −14/−9/−8 | −0.08/+0.13/+0.28% |
| revenue_yoy（對照） | +.022/+.025/+.024 | 11/13/12 | **+0.51/+0.89/+1.47%** |

**E/P 與殖利率：RankIC 正、價差負**——最便宜一組跑輸最貴一組（價值陷阱形狀）。可進 LightGBM（非線性），
不得進線性 composite 或單獨當訊號。B/P 是唯一 IC 與價差同向且隨 horizon 上升的。

### 12.3 籌碼落地後的執行手冊（依序；每步的失敗都要停）
```
# 0. 確認兩個 staging 都完成（ingest_log 有「完成」；finmind_institutional 與 finmind_margin 各 ~2,064 檔）
# 1. 載入（先 --check 看重疊一致性，再 --write）
uv run --all-groups python scripts/load_finmind_chip.py institutional D:/project/twquant/data/staging/finmind_institutional --check
uv run --all-groups python scripts/load_finmind_chip.py institutional D:/project/twquant/data/staging/finmind_institutional --write
uv run --all-groups python scripts/load_finmind_chip.py margin        D:/project/twquant/data/staging/finmind_margin --check
uv run --all-groups python scripts/load_finmind_chip.py margin        D:/project/twquant/data/staging/finmind_margin --write
# 2. 品質：quality.sparse_dates 對兩表；覆蓋 ≥ 1,600 日
# 3. 只重算 7 個籌碼因子（T-094；可照成本分 6 片並行，分片見 §9.4）
uv run --all-groups python scripts/backfill_factors.py D:/project/twquant/data/lake_rebuild 2020-01-02 2026-09-01   chip.foreign_net_10d,chip.inst_net_10d,chip.trust_net_10d,chip.foreign_streak,chip.trust_streak,chip.margin_velocity_5d,chip.short_margin_ratio
# 4. 驗收：verify_rebuild.py（7 個籌碼因子覆蓋 ≥ 90%；融資兩個 > 0）
# 5. T-096：訓練 → backfill-scores → ml eval → 回測 1×/0×（scope bt:ml_rank_h10）
uv run --all-groups python -m twquant ml train --from 2020-01-01 --to 2026-08-28 --config configs/ml_h10_v2.yaml --out D:/project/twquant/data/models
uv run --all-groups python -m twquant ml backfill-scores --model D:/project/twquant/data/models/lgbm_h10_20260225_5526f2c2
uv run --all-groups python -m twquant ml eval --model D:/project/twquant/data/models/lgbm_h10_20260225_5526f2c2
uv run --all-groups python -m twquant backtest --strategy configs/strategies/ml_rank_v2.yaml --start 2022-07-01 --end 2026-02-28 --pg
uv run --all-groups python -m twquant backtest --strategy configs/strategies/ml_rank_v2.yaml --start 2022-07-01 --end 2026-02-28 --pg --cost-scale 0
# 6. research_log 從 worktree data/ 併回主庫；對照 ml_rank_repaired 記入 D-048
```
全部指令都需要 `TWQUANT_PG_DSN` 與 `PYTHONIOENCODING=utf-8`；訓練與回測的 research_log 會落在 worktree `data/`（T-084）。

---

## 13. T-097 財報品質因子（程式已落地，資料排隊中；09-03 凌晨）

### 13.1 使用者決定
「先做這 6 個」：GP/A、ROE、Accruals(−)、資產成長(−)、SUE、負債比(−) + size 控制。FinMind 免費層三表實測可取；
`減資恢復買賣參考價格` 為付費（T-098 只做除權息層）。三表 + 除權息結果表的回補（約 14 小時額度）排在籌碼之後
自動接續（背景 byn0gq80k）。

### 13.2 實測決定的三個公式細節（不憑名字猜）
- 損益表是**單季值**（2330 2024 四季營收加總 = 全年 2.894 兆 ✓）→ TTM = 近四季相加。
- 現金流量表是**累計 YTD**（營業現金流 436B → 814B → 1,206B → 1,826B）→ 單季 = 本期 − 上期，Q1 照抄，上期須同年前一季。
- 損益表與資產負債表都有 `EquityAttributableToOwnersOfParent`：前者是歸屬母公司**淨利**、後者是**權益**。schema 以 `statement` 欄區分。
- EPS 原始值有小數（8.7／9.56／12.55／14.45 對照公告 8.70／9.56／12.54／14.45）；`_per` 後綴列是百分比，映射層剔除。

### 13.3 已落地（閘門 522 passed）
- `financial_statements` 長表：clean 19 表 + PG 鏡射，PK (date, stock_id, statement, type)；`available_at` = 法定申報期限 22:00
  （Q1 5/15、Q2 8/14、Q3 11/14、年報次年 3/31），estimated=True。
- `finmind.statement_rows`；`scripts/load_finmind_statements.py --check|--write`（重疊鍵只比對不覆寫）。
- `factors/quality.py` 七因子；17 條測試：PIT 邊界（年報 3/31 20:00 不可見、4/1 可見）、YTD 拆季、TTM 四季須連續、
  **缺季不得回退到舊季**（`_map` 映射後才 drop null——否則過期數字冒充當期）、SUE 前 8 個同比差不足即 null、七因子反前視。

### 13.4 財報資料到了以後的驗證清單（未過不進 IC）
載入器自動：pandera 型別／時區、非季底日期 raise、重疊鍵一致。手動：全市場「值恰為 0」比例（FinMind 以 0 表缺值）、
資產＝負債＋權益容差、Q4 累計現金流 vs 年度、EPS×股數 ≈ 淨利、每家季數覆蓋與缺季分布、非曆年制公司、抽 10 家對 MOPS。
然後：`backfill_factors.py … <七因子子集>`（T-094）→ `ic-report` 七因子（全樣本 + 2022-01-14 起）→ 對照 D-020 子集。

### 13.5 下一個：T-099 產業因子
`price.industry_mom_6m`（Moskowitz–Grinblatt）、`price.industry_rs_20d` 資料現成；`chip.industry_foreign_flow_10d`（Choi–Sias）待 T-092。
**更正**：富邦 `broker_trades` 在 PG 有 **2022-01-03～2026-09-02、1,130 個交易日、5,690 萬列**（逐年 246/239/241/243/161 天，近乎完整；最後一次抓取 2026-09-02 19:12）。T-086 殘留的 concentration／main_force 等 127 天是當時**實驗性物化的範圍**，不是資料範圍；分點因子任務 T-016／T-036 從未正式完成。富邦來源是**母公司層級** top-15 買賣超（無分點、無均價），`backfill-fubon-chip` 為專屬指令、不在每日迴圈。`broker_flow_daily` 衍生表只從 2025-07-01 起（287 日）。FinMind 分點表（真正分點層）為付費。

### 13.6 富邦分點資料稽核（09-03 02:30）
全期對照行情交易日：只缺 **2024-02-29**；**2026-06-30 只有 4 檔**（抓取中斷）。7／8 月逐日無缺（分點檔數 ≥ 行情四碼股數；07-10 颱風兩邊皆無）。
兩天已補齊（02:14~02:37，各約 11 分鐘、2,055 次請求；需 `printf 'y
' |` 餵互動確認），重跑稽核 **1,131 日、缺日 0、稀疏日 0**。
稽核方法＝`quality.sparse_dates` 對每日檔數 + 行情交易日左外連接找缺日——與 T-087 同一套。
T-016 設計收斂為三因子 + 隔日沖分點屬性表（清潔劑，非因子）。

### 13.7 分點因子落地、雲端排除、方向變更、備份（09-03 02:40）
- **T-016 程式已落地**（`factors/broker.py`，11 條測試，閘門 542 passed）：集中度／家數差／持續性三因子 + `daytrade_ratio` 屬性表
  （expanding、事件記在賣出日）。IC 階梯以 PG 5,690 萬列執行中（背景 bm14p5y52）。
- **雲端不算這三個因子**：`cloud.exclude_factors`（`CloudConfig` 新欄位）+ 反向測試「清單裡每個都真的超窗」。
  執行期強制（GHA 對雲端 DSN 跑 `pipeline` 時跳過）**未做**——T-100。
- **方向變更（待確認範圍）**：使用者 02:30「應該都要改地端了」。事實基礎：本機 PG **27 GB**（factor_values 14 GB、broker_trades 9.2 GB、
  prices 1.5 GB、ml_scores 1.1 GB）是唯一的研究資料庫；雲端只有滾動窗口；`data/` 無任何 pg_dump。
- **備份已完成**（02:26→02:34，**2.40 GB**，18 張表，`pg_restore --list` 驗過）：`pg_dump -n serving -Fc -Z 6` → `D:/project/twquant/data/backup/twquant_serving_20260903.dump`
  （用同一 postgres 映像、`--network container:twquant-pg-1`）。還原：`pg_restore -d twquant -n serving <dump>`。
  **建議之後**：每日增量或每週全量，並 rclone 到 Drive（docs/OPS-archive.md 已有 raw 歸檔流程）。
- 富邦缺的兩天（2024-02-29、2026-06-30）補抓中（背景 b491vpu2c，互動確認以管線餵 y）。

### 13.8 夜間自動化（09-03 03:00；使用者：「訓練完模型再跑回測」）
兩條背景等待鏈（Claude Code 背景任務；主機關機即中斷，重跑指令見下）：
1. **籌碼 → 訓練 → 回測**（byefkb9om）：等籌碼自動鏈印「管線到驗收為止完成」→ 覆蓋門檻：7 個 `chip.*`（非 broker）因子各 ≥ 1,300 日
   （寫 `scratchpad/chip_coverage.txt`）→ 過則 `bash scratchpad/run_t096.sh`（訓練 `ml_h10_v2` → backfill-scores → ml eval →
   回測 1×/0× → research_log 併回 → 特徵重要度），日誌 `scratchpad/run_t096.log`。任一門檻未過即停、不訓練。
2. **財報載入與驗證**（bb3u4q6v8）：等三表＋除權息回補結束 → `load_finmind_statements.py --check → --write` → `verify_statements.py`
   → **停**（物化與 IC 待人工看驗證結果）。
其他仍在跑：PG 備份（bf7028337）、分點三因子 IC（bm14p5y52）→ 產業兩因子 IC（bjw8q18n0）、富邦缺日補抓（b491vpu2c）。

**早上要看的**（依序）：`chip_coverage.txt`／`run_t096.log`（v2 vs `ml_rank_repaired` 逐項）→ 分點與產業 IC → `data/backup/` 檔案大小
→ 財報驗證六項 → 若都過：`bash scratchpad/run_newfactors.sh materialize <因子清單>`（6 分片子集物化 + PG 覆蓋表；`broker_trades` 已於 02:40 起匯出到 lake_rebuild，背景 b0vscu55f）→ `bash scratchpad/run_newfactors.sh ic <因子清單>`（全樣本 + OOS 期，併回 research_log）→ `factor_corr.py <新因子清單>` 剔重複（|ρ|>0.7）→ 登記 v3。分點三因子與產業兩因子的 IC 已於 02:20 起自動跑（bm14p5y52 → bjw8q18n0）。
若主機曾關機：`docker start`、重跑對應指令即可，所有回補與物化皆可續傳。
- **分點 IC 第一個結果**：`broker_concentration_20d` RankIC −0.008／−0.011／−0.010（t −4.1／−6.0／−5.0）、五分位價差 +0.22／+0.35／+0.65%。
  |t| 過門檻但**方向與先驗相反、IC 與價差不同號（U 型）**——富邦 top-15 買超偏散戶，20 日集中買超在 5~20 日是反向。
  可進 LightGBM，不得以 +1 進線性組合。


### 13.9 02:43 記憶體耗盡 → Docker 引擎倒地 → 03:37 復原、夜間鏈重組（09-03 03:50）
- **事故**：我同時開了分點 IC（5.9 GB）＋全套閘門＋pg_dump＋FinMind 抓取＋Docker VM → 實體記憶體用盡。後果：FinMind 法人抓到 2,040/2,064 檔時
  `MemoryError`；融資抓取程序掛住（0 檔、無 ingest_log）；Docker Desktop 渲染程序崩潰、WSL 引擎停止；閘門 532 passed／1 failed（PG 連線逾時，非程式錯）；
  分點 count_diff／persistence 與產業兩因子的 IC 全部在 PG 逾時上失敗。
- **根因（引擎起不來）**：`com.docker.backend.exe.log`：`running OTel manager: removing stale socket: remove %LOCALAPPDATA%\Docker\run\userAnalyticsOtlpHttp.sock:
  The file cannot be accessed by the system`——崩潰留下的 AF_UNIX socket reparse point（0 B、Windows 錯誤 1920），`Remove-Item`／`fsutil reparsepoint delete` 都碰不到。
  **不是**磁碟損毀（`e2fsck` 乾淨；`disk/docker_data.vhdx` 72.5 GB 完好）、不是埠衝突、不是設定檔。
- **修法**：停光 Docker 程序 + `wsl --shutdown` → 把整個 `%LOCALAPPDATA%\Docker\run` **改名**為 `run.stale-<時間>`、新建空 `run` → 啟動 Docker Desktop，10 秒內五個容器全起。
  PG 自動復原（redo 僅數 KB）；18 張表俱在；`twquant_pgdata` 卷無損。舊 `run.stale-033639` 之後可刪（若仍刪不掉就留著，無害）。
- **夜間鏈重組**：舊的四條等待鏈全部殺掉（它們的觸發條件是「沒有 backfill_finmind 程序」，殺掉卡住的融資程序會讓它們拿半套資料去物化）。
  改成兩支腳本、以**標記檔**串接，不再看程序有無：
  1. `scratchpad/overnight_finmind.sh`（b9cetv77t）：法人續抓 24 檔 → 融資全抓（2,064 檔，≈3.6 h）→ 兩者各 ≥ 2,000 檔才寫 `chip_staging_done`
     → 財報三表＋除權息（各 2,064 檔，≈14 h）→ `load_finmind_statements.py --check/--write` → `verify_statements.py` → `statements_done`。
  2. `scratchpad/overnight_compute.sh`（bbmifrpkc）：等 `chip_staging_done` → 載入法人／融資（check→write）→ `check_chip_lake.py`（覆蓋 ≥ 1,600 日、sparse_dates）
     → 7 個籌碼因子 6 分片子集物化 → 覆蓋門檻**逐名** ≥ 1,300 日（舊鏈用 `LIKE 'chip.%'` 會被 127 日的殘留舊因子誤擋）→ `run_t096.sh`
     → 全套閘門（`gate_<時間>.log`）→ 分點 count_diff／persistence ＋ 產業兩因子 IC 重跑（`ic_rerun.log`）→ `compute_done`。
  任何一步失敗即停在該步，輸出在 `tasks/<id>.output`；FinMind 逐檔日誌在 `scratchpad/finmind_<dir>.log`。
- **教訓**（已記憶）：本機 32 GB 只能容納「一個重運算 + 抓取」；IC（5.9 GB）、閘門、pg_dump、物化 6 分片不可同時開。
- **07:34 鏈 B 第一次停在法人載入**：`institutional_net` 在 2023-04-13 遇到未知類別 `'Dealer'`（護欄正確地 raise）。實測 staging：27,562 列、280 檔、2020–2026
  全期都有，這些 (日, 股) 只有 `Dealer`＋外資＋投信三列（興櫃／部分上櫃日的法人格式：自營商不拆自行買賣／避險、無外資自營），
  **從未**與拆分類別同鍵並存。修法（478e268）：`Dealer` 映射進 `dealer_net`；同鍵合併與拆分並存即 raise（防重複計算）。加 2 個測試、19/19 過。
  鏈 B 07:50 重啟（bi64akx9e）。融資映射已在 03:52 用 14 檔乾跑：重疊 2,667 列 0 差異。
- **07:36 鏈 B 第二次停在法人載入：重疊 235,138 列有 508 列值不一致**。全部對照 TWSE T86 原始 JSON（41 個交易日逐日抓）：
  **FinMind 508/508 與原始一致、湖 508/508 全錯**；508 列全是 TWSE 上市股（TPEx 0 列）、`available_at` 全為 18:00（我們的擷取路徑）。
  錯法的簽名：湖 `foreign_net` = 外陸資＋投信、`trust_net` = 自營商賣出(自行)、`dealer_net` = 自行買賣超——像 T86 列在「外陸資買賣超」之後
  多出 3 個欄位（名稱對位錯 3 格）。散在 2022-01-13～2026-09-01 共 41 日、每日 1～55 檔，**2026-08-31 仍有 55 列** → 每日擷取現在還在產壞列，開 T-102 根治。
  今晚處置（可稽核、可逆）：`scratchpad/repair_inst_rows.py` 以 FinMind 值覆寫湖（41 個分區檔）與 PG 各 508 列；修正前值與原始對照存
  `scratchpad/inst_mismatch_verified.parquet`。鏈 B 07:58 第三次啟動（bgcjubvu5）。
- **07:45～09:00 鏈 B 通過**：法人載入 +1,393 日／2,276,480 列、融資 +1,415 日／2,349,115 列（重疊比對皆 0 差異），兩表 1,620 日、`sparse_dates` 0 天；
  7 個籌碼因子 6 分片子集物化 51～72 分鐘；覆蓋 **1,611～1,620 日**（原 219～228）、平均每日 1,462～1,542 檔。09:01 啟動 T-096（訓練 v2）。

### 13.10 T-096 結果：籌碼補齊後的 v2——IC 升、組合反而差（09-03 09:20）
`lgbm_h10_20260225_5526f2c2`（28 特徵；覆蓋閘門：最低 5 個特徵皆 100%）：OOS RankIC **+0.0887**（16 窗 15 正，窗 4 −0.019）、
eval RankIC +0.0888／IR 0.748／勝率 79.6%／t(NW,10) 9.10、分位嚴格單調、對最強基準（D-020 三因子 +0.0339）勝出 +0.0549 → 階梯第 1 級過。
回測 886 日（`ml_rank_v2.yaml`，只換 model_id）：**1× CAGR −4.76%、Sharpe −0.18、MDD −41.3%、周轉 2,883%、成本 6.37M（初始資金 64%）**；
**0× CAGR +13.47%、Sharpe +0.77、MDD −33.5%**。對照 `ml_rank_repaired`：IC +0.0796 → +0.0887，但 1× −0.01% → −4.76%、0× +21.9%／1.21 → +13.5%／0.77。
特徵重要度（gain）：籌碼 7 個合計 13.8%（`short_margin_ratio` 6.6%、外資／融資動能各 2%、**投信兩個 0.0%**）；評價 3 個 21.1%（book_yield 8.2%）。
**判讀**：多的資訊提高了橫斷面排序力，卻沒有轉成前段組合報酬——連無成本都輸，表示新特徵改變的是「選到誰」而非「排得更準的同一批」，
且周轉再升（籌碼 10 日窗本身就快）。依 D-048 §5：1× CAGR<0、Sharpe<0.5、周轉>1500%、ΔSharpe 對修復基準為負 → **v2 不晉升**。
research_log 已併回主庫（5 個 part）。逐筆統計與四段報酬待鏈 B（閘門＋IC）跑完再補（避免同時佔記憶體）。

### 13.11 閘門與 4 個因子 IC（鏈 B 尾段，09:20～10:20）
- 全套閘門 **545 passed、ruff／format／mypy 全 OK**（含 Dealer 修法的 2 個新測試）。
- `chip.broker_count_diff_5d`：RankIC −0.0143／−0.0153／−0.0173（h=5/10/20）、t −11.1／−13.4／−15.6、價差 −0.10／−0.15／−0.24%（全樣本與 OOS 幾乎相同——broker_trades 本來就從 2022 起）。
  **方向與先驗相反且 IC 與價差同號**：賣方分點多於買方（「少數人吸貨」）的股票之後是跌的。這是三個分點因子裡唯一「乾淨」的訊號，以 −1 方向可過 D-006。
- `chip.broker_persistence_10d`：−0.008／−0.007／−0.011、t −2.6／−2.5／−3.7、價差 +0.17／+0.30／+0.46%——弱且 U 型（同 concentration_20d）。
- `price.industry_mom_6m`：全樣本 t −1.4／−1.5／−0.9、OOS t −2.1／−2.4／−1.5；`price.industry_rs_20d`：t 1.1／0.8／−1.8。**在 5～20 日 horizon 無訊號**，不進 v3。
- research_log 併回主庫 8 個 part（4 因子 × 全樣本／OOS）。
- v3 候選清單因此縮成：3 個分點因子（皆反向、供 LightGBM）+ 財報 7 個（T-097，待鏈 A 抓完、載入、驗證後物化與 IC）。產業兩個剔除。
- **v2 逐筆（10:30）**：1× 往返 2,472、勝率 46.8%、PF 0.923、EV −0.028%、四段 1/4 正；0× 2,588、52.8%、1.194、+0.576%、四段 3/4（2025 −0.8%）。
  repaired 對照：1× 47.1%／0.994／+0.092%，0× 54.0%／1.361／+0.829%。JSON：`scratchpad/trades_ml_rank_v2_{1x,0x}.json`（對帳 UI 仍是 repaired 單模型版）。

### 13.12 鏈 C：財報 → 物化 → IC（使用者 09-03「財報抓完驗證過就物化跟跑 IC」）
啟動前先用 workflow（wf_c636a637-01a，4 視角 × 對抗式驗證）審查整條無人值守鏈，26 項發現中處理了這些：

**資料正確性（改程式，已有測試與反向驗證）**
- **9.1% 的股票季拿不到「歸屬母公司」淨利**（5,102 個股票季、287 檔；金融業＋無少數股權的單一實體公司），
  母公司權益同樣缺 9.3%。這些股票季**100% 沒有少數股權列**，且三者並存時「合併數 − 少數股權 = 母公司數」
  誤差 <1% 佔 99.3%（損益）／99.7%（權益）。`quality.py` 因此加合併數回退（`IncomeAfterTaxes` → 銀行用語
  `IncomeAfterTax` → `NetIncome`，皆減少數股權；權益用 `Equity` − 少數股權）。**淨利覆蓋 90.9% → 100%、
  權益 90.7% → 100%**。反向驗證：移除回退後兩條新測試立刻變紅。
- **綜合損益冒充淨利**：損益表有 235 個股票季（41 檔）的 `EquityAttributableToOwnersOfParent` 其實是
  「綜合損益總額歸屬於母公司業主」，且同季沒有正常淨利列。`statement_rows` 改名為
  `ComprehensiveIncomeAttributableToOwnersOfParent`（不丟列、不撞鍵），不讓它進 ROE／應計的分子。
- **反前視測試空轉**：`test_no_lookahead` 用 12 季夾具，SUE 需 13 季 → 兩邊都空也會通過。改用 16 季夾具，
  並先斷言該因子在切點真的有值。

**閘門與鏈（scratchpad）**
- `gate_statements.py` 重寫：門檻全部以實測值校準（G2 誤差>0.5% 實測 0.67% → 門檻 3%；G4 誤差>15% 實測 5.3% → 門檻 15%），
  新增 **G1b 有效覆蓋**（每個因子輸入在股票季的可得率，淨利／權益算回退後）——這是擋「缺科目→因子靜默消失」的那一關；
  G3 的現金流量表稅前淨利科目名改成**候選逐一試、取最吻合者**，全部不吻合才失敗（並印出科目清單）。
  已用現有 staging（損益＋資產負債，缺現金流）建臨時湖乾跑：income/balance 全過、三項現金流檢查如預期大聲失敗。
- `overnight_statements.sh` 改寫：分片**逐一檢查退出碼與「完成：」字樣**（原本 `wait` 會吞掉失敗）；
  IC 逐因子獨立執行（原本 grep 無匹配會讓整個迴圈中止並跳過 research_log 併回）；
  覆蓋門檻**逐因子**設天數下限（leverage／log_market_cap 1550、TTM 三個 1500、asset_growth 1470、sue 950，平均檔數 ≥1000）；
  等待有 15 小時上限與存活檢查（看 FinMind 逐檔日誌的 mtime，不是每 3.5 小時才寫一行的 tasks 輸出）；
  **只有全部成功才寫 `v3_ready`**。相關性步驟明確聲明不含分點三因子（它們不在 `factor_values`，IC 是即時計算）。

**記為後續（未阻擋今晚）**：restatement 只有最新版本（載入器對變動值會硬失敗，屬 PIT 假設未文件化）、
`lookback_days` 宣告值短於實際需求（T-083 落地後會變成靜默 null）、SUE 前 8 季與面額 10 元是 .py 內的字面值（規則 8）、
`ic-report` 不翻方向（負向因子顯示負 t 屬預期）。
- **物化前的冒煙測試（12:5x）**：用 staging（損益 2,055 檔＋資產負債 820 檔）建臨時湖、接真實價格，對 2026-09-01 算 7 個因子——
  6 個都產出有限且合理的值（毛利率中位 0.111、ROE 0.078、負債比 0.441、log 市值中位 22.66 ≈ 69 億元、資產成長 8.6%、SUE 中位 0.95）；
  `accruals` 如預期落空（現金流量表尚未抓）。SUE 檔數（1,759）多於其他（786）是因為它只需損益表，而資產負債表當時只抓到 820 檔。
- 後續任務：**T-103**（財報只有最新版本＝重編前視，且載入器對重疊值變動硬失敗）、**T-104**（`lookback_days` 宣告 460／850 短於實際需要，
  T-083 落地後會讓因子靜默變 null）。

### 13.13 D-050／鏈 D：IC 跑完就自動登記 v3 並訓練（使用者 09-03 13:20）
- **D-050 在 IC 結果產生前寫定**（這是重點：規則先固定，才不會事後挑因子）。納入規則 R1–R4：
  OOS h=10 |t| ≥ 3、RankIC 符號與註冊 direction 一致、與既有特徵及彼此的逐日橫斷面 |ρ| ≤ 0.7、覆蓋門檻（鏈 C 已強制）。
  **只加不減**（v2 的 28 個全留），使 v3 − v2 可完全歸因於財報因子。無因子通過 → 不訓練、不消耗 trial。
- `scratchpad/select_v3_features.py` 機械執行規則並產生 `configs/ml_h10_v3.yaml`／`ml_rank_v3.yaml`（除特徵與 model_id 外逐字複製 v2）。
  已用合成 IC 日誌乾跑驗證：符號相反與 |t| < 3 各自被擋、缺席因子有明確原因、無人通過時退出碼 2。
  再用已物化的因子跑通相關性與配置產生路徑。
- `scratchpad/overnight_v3.sh`（鏈 D，bk2yr4weg）：等 `v3_ready` → 選因子 → 訓練 → **守門：`meta.json` 的 features 必須與配置逐字相同**
  → 填 model_id → backfill-scores → ml eval → 回測 1×／0×／1.5× → 逐筆與四段（與 v2、repaired 併排）→ research_log 併回
  → 特徵重要度 → `compare_v3.py`（D-038 §5 逐條 + DSR，母體含所有 `bt:ml_rank*`）。
- **順帶發現（T-106）**：`price.mom_20d` 與 `price.rs_market_20d` 的逐日橫斷面 Spearman = **1.000**——相對強弱只是減去當日大盤報酬
  （對所有股票是同一個常數），排序完全不變。兩者在 v2 各拿 4.0%／3.9% gain，等於同一份資訊被拆成兩半。
  依 D-050「只加不減」本次不動，另立裁決處理。
- **15:47 鏈 D 誤判鏈 C 已死**（我自己的 bug，與稍早修掉的是同一類）：鏈 D 的存活檢查看鏈 C 的**輸出檔** mtime，
  但鏈 C 在等待階段會安靜數小時（12:43 印完就沉默），180 分鐘後被判定死亡而自行停止。
  修法：改用**程序探測**（`Get-CimInstance Win32_Process` 比對命令列含 `overnight_statements`），
  且**探測失敗一律視為活著**——誤判死亡的代價是整晚白等，誤判活著只是多等到 20 小時上限。
  鏈 D 15:5x 以新版重啟（bf30p7et1）。**通則：等待型程序的存活訊號要看程序本身或它的上游進度檔，永遠不要看它自己的日誌。**
- **18:40 三表齊全後的閘門預跑：全部通過**（income／balance／cashflow 各 2,055 檔、4.51M 列、2,050 檔、30 個季底）。
  G3 用 `NetIncomeBeforeTax` 對 12,428 檔年吻合率 **98.4%** → 現金流量表確為累計 YTD，`accruals` 拆季邏輯成立；
  cfo 覆蓋 100%、淨利／權益回退後 100%、恆等式誤差 >0.5% 僅 0.37%、EPS×股數誤差 >15% 為 5.4%。
  鏈 A 18:38 起抓除權息結果（最後一個資料集），預計 22:15 抓完、22:30 載入驗證完成後鏈 C 接手。

### 13.14 v2 模型報告（使用者 09-03 19:00「可以先產 V2 的模型報告嗎」）
報告 artifact：https://claude.ai/code/artifact/4a22c1a4-a9cf-4c49-9564-3cbe5b68ce40（私有；`scratchpad/v2_report_final.html`）。
**產法可重現**：`build_v2_bundle.py`（把四份 `report_v2_*.verified.json` 壓成 `report_v2_bundle.json`）
→ `inject_bundle.py`（注入 `v2_report.html` 的佔位符）→ `v2_report_final.html`。頁面不連外部資料，數字全部內嵌。
**數字來源與驗證**：以 workflow（wf_3411a298-589）跑 4 個抽取代理（模型／回測／逐筆／重要度）各自標註出處，
再由 4 個驗證代理**從另一個來源重算**比對——視窗 IC 由 `oos_scores.parquet` 重算、回測指標與 research_log 交叉核對、
逐筆統計由淨值與報酬陣列重算、重要度重跑 LightGBM。共 **1,750 項檢查、0 項不符**（240＋141＋660＋709）。
一處修正：第一代的視窗日期範圍原為 null，驗證時以重算確認與 v2 的 16 個視窗邊界一致後補上。
**報告新增的兩個發現**：
- 籌碼因子的 gain 從第一代的 **4.2% 升到 13.8%**（+9.7pp）——覆蓋補齊後模型才真的用它們；價量被壓下 22.4pp、營收 7.1pp。
- 兩代模型**訓練資料量不同**（v2 樣本外 1,834,571 列／2,049 檔 vs 第一代 1,738,793 列／2,034 檔，第一代訓練於籌碼回補前），
  故「v2 vs 第一代」是特徵集與資料量的合併效果，不是純特徵效果。這一點已寫進報告的注意事項。
- **報告口徑修正（19:4x）**：使用者問「為什麼日勝率可以到 79.6%，可以直接用嗎」——這正是欄名撞名造成的誤讀。
  `ml eval` 的 79.6% 是 `evaluate.py:44` 的 `hit_rate`（**IC > 0 的日數占比**），衡量全市場約 1,700 檔的排序方向；
  回測的 51.8% 是 `metrics.py:62` 的 `win_rate`（**日報酬 > 0 的日數占比**）。報告原本兩者都寫「日勝率」。
  已改為「IC 為正的日數」／「日報酬為正的日數」／「逐筆勝率（往返為正）」，並在階梯表下加一段明說前者不是賺錢比率。
  **通則**：同一份報告裡不得有兩個不同定義共用一個中文欄名；`hit_rate` 與 `win_rate` 在本 repo 是兩件事。

### 13.15 鏈 C／D 完成：v3 不晉升，並在途中挖出 D-051（09-04 00:39）
- **鏈 C**（20:14→23:41）：財報載入後閘門全過 → 7 個品質因子 6 分片物化（各約 166 分鐘）→ 覆蓋門檻通過
  （1,046～1,619 日、每日 1,654～1,739 檔）→ IC 全部完成。
- **鏈 D**（00:01→00:39）：規則自動納入 `fundamental.sue`／`roe` → 30 特徵 → 訓練 `lgbm_h10_20260225_a48b262c`
  → 回測 1×／0×／1.5× → 逐筆與四段 → §5 與 DSR。**裁定不晉升**，詳見 D-050 結果段。
- **途中攔下三個缺陷（都會讓結論失真）**
  1. **相關性全 NaN**：當日整欄缺值或方差為零時 `pl.corr` 回 NaN（不是 null），`drop_nulls` 濾不掉，
     一顆就把平均值污染成 NaN；在選因子腳本裡更糟——NaN 比較恆為假，R3 去重會**靜默失效**。
     已改為逐對去 null、共同子集內排名、只採計有限值與檔數足夠的日子。
  2. **D-051**：`winsorize_mad` 在 MAD=0 時把稀疏因子整條歸零，28 個 pin 特徵中 7 個受害
     （投信兩個 100% 的日子失效）。**這是今晚最重要的發現**，見 D-051／T-107。
  3. **`compare_v3.py` 成本檔位**：原本「非零成本」一律當 1×，D-050 新增的 1.5× run 蓋掉 1×，
     一度把 v3 的 1× 顯示成 CAGR −11.85%（實為 −4.73%）。已改為依成本大小分 0×／1×／1.5×。
- **D-050 補述**（都在再次執行**之前**寫定，只填補登記未涵蓋的情況，未動 |ρ| ≤ 0.7 門檻）：
  (a) 基準特徵 z 為常數 → 無法判定，不視為重複；(b) 候選自身退化 → 排除；(c) 逐一列出不得靜默；
  (d) 事件型稀疏基準特徵（每日 < 30 檔，如 `flag_hold_50d` 僅 4 檔）→ 同 (a)；
  (e) 實作參數：逐日共同樣本 ≥ 10 檔、可用日數 ≥ 250 天。
- **早上要看的**：D-050 的結果段（三代對照表）、D-051（要不要照建議 B 修 `standardize.py`）、
  T-107（修正後三代重跑，確認「特徵層飽和」是否仍成立）。
