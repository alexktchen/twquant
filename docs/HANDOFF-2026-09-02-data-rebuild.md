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
兩天以 `backfill-fubon-chip --from D --to D --data-dir D:/project/twquant/data` 補抓中（帳本無該兩日記錄，續傳不會跳過）。
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
→ 財報驗證六項 → 若都過：`backfill_factors.py … <13 個新因子子集>`（分點三因子需先把 `broker_trades` 匯出到 lake_rebuild）→ `ic-report` 品質七因子
→ `factor_corr.py` 剔重複 → 登記 v3。
若主機曾關機：`docker start`、重跑對應指令即可，所有回補與物化皆可續傳。
