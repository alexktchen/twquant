# 東西在哪（2026-09-23，資料到 09-22；第九輪已結案、第十輪進行中）

> **給下一個 session 的第一份文件。** 讀完這頁就能動手，不必翻其他文件。
> **這一輪的來龍去脈在 [docs/HANDOFF-2026-09-23-eight-rounds-and-pipeline.md](docs/HANDOFF-2026-09-23-eight-rounds-and-pipeline.md)**——
> 動手前先讀它的「一、使用者是誰、要什麼」與「四、我犯的錯」。
> 更早的在 [docs/HANDOFF-2026-09-09-long-window-and-vault.md](docs/HANDOFF-2026-09-09-long-window-and-vault.md)。

## 0. 三十秒版本

- 最新 worktree：`D:/project/twquant/.claude/worktrees/priceless-tereshkova-839f0d`，branch `claude/d122-s-round2`
- **資料一律在主 checkout**：`D:/project/twquant/data/`（不進 git，但都在本機）
- **八輪加工已封檔（D-141）**：八輪沒有一輪改善 **E0**（年化 +27.8%／回撤 −41.4%／Calmar 0.67）。
  唯一被獨立驗證的是排序模型的金庫期 IC **+0.0816**。**不要在同一個問題上再加工。**
- **第九輪起換了問題（右尾／飆股，D-143）**：訊號通過（命中率倍率 **3.23×**、1,005 筆分布在 100 個月），
  **容器仍不行**（回撤 −39.7%~−54.1%、Calmar 0.26~0.32，低於 E0 與 V1）。交接見
  [docs/HANDOFF-2026-09-23-tail-round.md](docs/HANDOFF-2026-09-23-tail-round.md)。
  **第十輪（D-146）進行中**：右尾訊號 ＋ 每日曝險目標、分點因子入模——**不設門檻、只報數字**
- **`factor_values` 斷階已修**（2026-09-23，11 小時整批重物化 50 支 × 2,746 日）：
  單日因子數 2023-06-15 從 21 → **54**；波動類新增 8 支（T-284）
- **使用者的目標**：散戶買個股、每日選股 10~15 檔、**15~20% 是單檔停損不是組合回撤**（我誤讀三次）、
  追**每筆期望值**不追勝率（D-136：隨機進場也有 74% 勝率）。
- **研究平台**：<http://localhost:8899>（前端）、<http://localhost:8000/api>（API）
- **資料到 2026-09-22**：9/21、9/22 因每日 pipeline 卡死而缺名單，已修（T-275~T-278）並補跑
- **待合併**：PR #91（pipeline 修復＋分點因子）。合併後要重建映像，T-275／T-276 才在容器裡生效
- **未修**：T-270（`prices_daily` 1,185 列 OHLCV 全為 0，2021-01~02）

## 1. ⚠ 最容易踩的陷阱：有兩個 `data/`

| 路徑 | 內容 |
|---|---|
| `D:/project/twquant/data/` | **正本**。所有結果、模型、報告、研究登記簿、staging |
| `<worktree>/data/` | **不要用**。`configs/system.yaml` 的 `data_dir: data` 是相對路徑，在 worktree 裡跑會寫到這裡 |

**今晚已經踩過一次**：`ml train` 把重訓的模型寫進 worktree 而不是模型庫，加 `--out` 才修好。
worktree 那份的內容已在 2026-09-06 全部合併回主 checkout。

**規則：任何寫檔的指令都明確給絕對路徑**（`--out`／`--models`／`--data-dir`）。
未修的相關任務：**T-084**。

## 2. 資料在哪

| 內容 | 路徑 | 量 |
|---|---|---|
| **回測結果包** | `data/ladder/`（80M）、`data/ladder10m/`（10M）、`data/tracks/`（雙軌）、`data/tracks_c*/`（C 軌）、`data/tracks_m*/`（M 系列）、`data/tracks_g/`（G 矩陣）、`data/tracks_mg/`（MG） | 約 60MB／104 個 JSON |
| **報告** | `data/reports/backtest_report.html`（含三張圖）、`all_backtests.json`（唯一資料源） | — |
| **模型** | `data/models/lgbm_lw_net_h20_20260225_83890036`（**現行排序**，D-110）、`lgbm_lw_dd_h60_20260225_1bd7c6a0`（風控閘門）、`lgbm_lw_rev_h15_20260225_9d4a149c`（LW-F1 池子） | 213MB（含舊模型） |
| **研究登記簿（DSR）** | `data/research_log/` | 241 檔／1,884 筆 |
| 長天期回補原始檔 | `data/staging/finmind_hist_*` | 462MB |
| **推薦快照** | `data/recommendations/<run_id>.json` | 2026-09-04 一份／1943 檔候選 |
| **紙上交易帳本** | `data/paper_trading/<軌別>/events.jsonl`（D-142，append-only ＋ SHA-256 雜湊鏈） | 每交易日一列 |
| **洞悉證據包** | `data/insights/<insight_run_id>.json` | 目前 0 份（頁面正確顯示尚未產生） |
| 執行日誌 | `data/*.log`、`data/*/run.log` | — |
| **PG 供應層** | `postgresql://twquant:twquant@localhost:5433/twquant`，schema `serving` | 約 27GB |

**歸檔但刻意保留的**（是證據，不要刪）：
- `data/ladder/_pre-T142-T149/`、`data/tracks/_pre-T142-T149/` — 舊的 10M 批次，D-070 的證據
- `data/models/_archive/…__pre-D064-data-defects/` — 汙染資料訓練的舊模型
- `data/models/_rejected/…__vault-eroded-3-sessions/` — 越過保險庫邊界的作廢模型

## 3. 文件在哪（全部在 worktree，進 git）

| 檔 | 讀它的時機 |
|---|---|
| `CLAUDE.md` | **動手前必讀**。十條黃金規則 |
| `TASKS.md` | 領任務。157 項、剩 41 項 |
| `DECISIONS.md` | **改設計前必讀**。D-001~D-076；六條事前登記不得事後修改 |
| `CHANGELOG.md` | 想知道某個決定為什麼被做 |
| `docs/HANDOFF-2026-09-06-tracks-and-gates.md` | **本輪完整交接**：進度、五個未解問題、產物清單 |
| `docs/reports/backtest-results.md` | 結果的版控版（與 HTML 同源） |
| `docs/specs/SPEC-013-two-track-strategies.md` | 雙軌策略的規格 |
| `docs/reports/volume-lead-price-ic.md` | **量先價行鏈 9 因子 11 年 IC**（2026-09-07）：連買天數正、買超÷量反向、放量 U 型 |
| `docs/research/PAPERS-KEYLEVELS.md` + DECISIONS **D-093** | 關鍵價位文獻 ＋ K 系列事前登記草稿（待裁決） |

## 4. 常用指令（都在 worktree 執行）

```bash
cd D:/project/twquant/.claude/worktrees/strange-kalam-3ec702
```

| 做什麼 | 指令 |
|---|---|
| **閘門**（改任何程式碼後必跑） | `PYTEST_ADDOPTS="--basetemp=C:/t/pt" TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant" uv run --all-groups python -m twquant check` |
| **更新報告** | `uv run --all-groups python scripts/report_bundle.py && uv run --all-groups python scripts/build_report.py` |
| **靜默失效靜態檢查** | `uv run --all-groups python scripts/audit_silent.py` |
| **資料稽核** | `uv run --all-groups python scripts/audit_data.py --since 2022-07-01 --tables prices_daily` |
| 跑階梯 | `uv run --all-groups python scripts/run_ladder.py --capital 80000000 --out D:/project/twquant/data/ladder` |
| 跑軌別 | `uv run --all-groups python scripts/run_tracks.py --tracks C1,C3 --out D:/project/twquant/data/tracks_c` |
| 判定 | `scripts/t146_verdict.py`（D-066）、`scripts/d071_verdict.py`（D-071）、`scripts/d067_edges.py`（D-067） |
| **前端閘門**（後端 gate 不涵蓋） | `cd frontend && npm test && npm run test:browser` |
| **前端開發預覽** | `cd frontend && npm run dev -- --port 5175`（`/api` 反代到 :8000）。個股頁是儀表板：`#/stocks` 為搜尋入口、`#/stocks/<代號>` 為 18 面板，規格見 SPEC-014 §9 |
| **產生洞悉證據包** | `uv run --all-groups python -m twquant insights-run --run <run_id> --top 30 --out D:/project/twquant/data` |
| **平台部署** | `docker compose build frontend api && TWQUANT_DATA_HOST=D:/project/twquant/data docker compose up -d api frontend` |
| **把推論輸出成推薦** | `uv run --all-groups python -m twquant ml export-recommendations --date <日> --model <模型目錄> --out D:/project/twquant/data` |
| **補某一天的推薦快照** | `TWQUANT_DATA_DIR=D:/project/twquant/data ... python -m twquant ml score --date <日> --model <模型目錄> [--pg]` 再 `... ml export-recommendations --date <日> --model <模型目錄> --out D:/project/twquant/data`（同 run_id 已存在不同內容會 raise；要重出先把舊檔改名為 `.superseded-...`）|
| **紙上交易：記今天** | `TWQUANT_PG_DSN=... uv run --all-groups python -m twquant paper-trade --date <日> --out D:/project/twquant/data`（已接進每日 pipeline，平常不用手跑；append-only，重跑同一天安全，**不得回填**）|
| **紙上交易：驗鏈** | `uv run --all-groups python -m twquant paper-verify --out D:/project/twquant/data`——雜湊鏈被動過就以非零碼結束。規則與判讀門檻見 `DECISIONS.md` 的 **D-142**，配置在 `configs/paper_trading.yaml` |
| **持股出場檢查** | 平台「我的追蹤」頁；API `GET /api/platform/exit-check?ids=2330,2317`（規則清單在 `configs/exit_check.yaml`，門檻在 E 系列策略配置）|
| **跑 E 系列（出場規則）** | `uv run --all-groups python scripts/run_exit_rules.py --window long`（`--window vault` 跑舊金庫期；`--n10 <代號>` 只能帶一支）|
| **E 系列判定** | `uv run --all-groups python scripts/e_verdict.py --bundles D:/project/twquant/data/tracks_e_long_nodiv`（送檢規則寫死在程式裡）|
| **八輪總結（先讀這個）** | `DECISIONS.md` 的 **D-141**——八輪全部未改善 E0、已排除的方向清單、我犯過的八個錯。不要在開發期資料上再加工（D-141 §七）|
| **五輪研究總結報告** | `uv run --all-groups python scripts/build_rounds_report.py --out D:/project/twquant/data/reports/rounds_report.html`（只讀結果包、不重跑；每一格用它自己登記下的判定）|
| **換股進出清單** | `TWQUANT_PG_DSN=... uv run --all-groups python scripts/build_trade_journal.py --bundles <結果包或目錄> --out D:/project/twquant/data/reports/trade_journal.html`（只讀 ledger、不重跑回測） |
| **D-109 診斷計分** | `TWQUANT_PG_DSN=... uv run --all-groups python scripts/d109_scorecard.py --bundles D:/project/twquant/data/tracks_vault_nodiv`（**不是裁決**；結束日 > 2026-08-31 的結果包會被拒收） |
| **模型勝率重算**（卡片上的數字） | `uv run --all-groups python scripts/model_ic_hitrate.py --models D:/project/twquant/data/models`——印出可貼回 `configs/model_catalog.yaml` 的區塊；指紋與模型不符會以非零碼結束（D-124） |
| 多因子 IC 階梯 | `TWQUANT_PG_DSN=... uv run --all-groups python scripts/ic_ladder.py --factors a,b --data-dir D:/project/twquant/data`（**--data-dir 必給絕對路徑**） |

**所有指令都要 `uv run --all-groups`**，直接 `python` 會缺套件（polars 等）。

## 5. 現在的狀態

**2026-09-09：金庫已開，這一輪的研究結論封檔。**（D-106 登記／D-107 判讀）

金庫期＝**2026-03-02 ~ 08-31**，在開封之前一次也沒被動過。**只能開一次，已經用掉了。**

| | 金庫期 6 個月 | 七月 | 金庫回撤 |
|---|---:|---:|---:|
| **0050（基準、價格報酬）** | **+32.2%** | −4.6% | **−15.9%** |
| LW-B2 全市場市值加權（最好的一支）| +25.8% | −3.7% | −15.4% |
| LW-M1 ML 訊號 | +22.7% | −27.1% | −35.1% |
| LW-C1 四因子精選 | +5.2% | −37.3% | −42.7% |
| LW-R2 剔除高波動（長窗口最好的一支）| **−5.5%** | −31.5% | −37.9% |

**兩句話總結：訊號是真的，容器是壞的。**

- **訊號通過**：基礎 H20 模型金庫期 RankIC **+0.0816**（門檻 +0.03）、73% 訊號日為正。
  排除「最終模型標籤可及」的頭一段後仍 +0.0624，通過不是污染撐起來的。
- **策略 8／8 全輸 0050**，且金庫裡剛好有一個體制事件（2026-07，TAIEX −15.1%），
  集中型策略把它放大成 −36~−43%——**D-105 在歷史上量到的 −37% 地板，在未見資料上重現**。
- **風控層與擇時 overlay 都沒接住**：`LW-M1` 與 `LW-M1-faber` 每一位數字相同
  （200 日閘門在崩盤前一次都沒關過）。

**要怎樣才算通過（D-114 修訂 D-109，驗收裡沒有任何指數）**：第二保留期最大回撤 ≤ 20%、成本後年化報酬 > 0；最早 2027-03-01 開一次。已知結構缺口：引擎只在月頻再平衡日做決定，「看壞就賣」沒被每日檢查過。**E 系列每日出場規則已測完：不送檢（D-117）**——七支沒有一支長窗口回撤 ≤ 20%，六支反而更深，沒有一支贏過不出場；唯一壓低回撤的是回撤模型 E7（−30.1%）。結果包在 `data/tracks_e_long_nodiv/`、`data/tracks_e_vault_nodiv/`。**下一步是 S 系列（D-120）＝使用者自己的策略**：每日補位、買進模型（絕對上漲機率）＋賣出模型，T-247~T-250。**引擎的每日補位模式已完成（T-247）；買進模型 SB 已訓練但診斷兩關都沒過（D-121），S 系列不進回測、待使用者裁決下一步**——校準在 0.5 以上全部高估，且崩盤時全面喊買（模型 56% 是波動度）。診斷指令：`uv run --all-groups python scripts/sb_diagnostics.py --model D:/project/twquant/data/models/lgbm_lw_tb_h20_20260225_a7cee0de`。E 系列否定的只是「月頻整批換股＋機械式出場」；D-115／D-119 已由 D-120 取代。

**部署裁決：沒有任何一支策略可部署。** ML 判定仍為 D-066 乙層——保留為零件，
**明文禁止重啟特徵工程或調參**。改投組建構屬架構，允許，但見下面第 ⑧ 條。

完整報告（含 118 格盤點、圖表、八次事前登記的戰績）：
`scripts/refresh_report.sh` 重新產生，發佈於 Artifact「886 天與 2227 天」。

**2026-09-22：研究改以紙上交易向前累積（D-142 事前登記）。**

八輪加工沒有一輪改善 E0（D-141，在 `claude/d122-s-round2` 分支上）。收尾決定是停止在開發期
資料上加工，改記新資料。D-142 把它變成制度：帳本 append-only ＋ 雜湊鏈、**拒絕回填**、
選股事件補記超過 3 天即拒絕，判讀門檻與時程在看到任何數字之前就寫死。

- 規則與門檻：`DECISIONS.md` 的 **D-142**；配置 `configs/paper_trading.yaml`
- 程式：`src/twquant/reporting/paper_ledger.py`（帳本）、`paper_trading.py`（每日一步）
- **執行語意與回測同源**：引擎公開 `execute_at_open` 等名字，並有一條逐筆比對
  `run_backtest` 的測試——否則「前向 vs 回測」的比較是假的
- **production 帳本尚未開軌**：D-142 仍在待裁決；開軌日與資本一旦寫入就不能改，
  要改只能另開新軌。開軌只需跑一次 `paper-trade`

## 6. 動手前一定要知道的三件事

**① DSR 試驗計數已達 175**（實測自 `data/research_log/` 241 檔、1,884 筆的 distinct `params_hash`，非估算）。試驗數一多，「最好那格」有很大
一部分是選擇偏誤。**不得挑冠軍**；任一格要晉升須另立事前登記、獨立重跑。

**② 事前登記不得事後修改。** D-066／D-067／D-071~D-075 的門檻都是在看到結果前寫死的。
結果難看就改門檻，等於沒有登記。

**③ 稀疏閘門相乘會歸零**（D-076）。每日 0~5 檔的事件乘上任何 30% 的池子，
交集期望 0~1.5 檔。**疊閘門前先算交集大小**——本輪已因此作廢兩批實驗（M2/M3 曝險 3%、GALL 0%）。

**④ `ml score` 挑錯模型——已修（T-224）。** 現有 8 個模型的 train_end **全部是 2026-02-25**，
舊版 `latest_model_dir` 同分時取排序最前者＝`lgbm_dd_h60`（D-075 的**回撤風控**模型）當選股器。
現在候選不唯一會 **raise 並列出候選**，並可用 `--purpose` 過濾。每日推論用哪個模型由
`configs/ml.yaml` 的 **`production_ranking_model`** 明寫（目前 `lgbm_lw_net_h20_20260225_83890036`，D-110 自 2026-09-09 換成長窗口版；每個模型的人話名稱、描述與勝率登記在 `configs/model_catalog.yaml`）
——換模型改這一行。L0 產生鏈已掛進每日 pipeline（T-225）。

**⑤ 重跑「已擷取過的某一天」的 pipeline 會 OOM（2026-09-07 實測）。** 正常的每日前進式
執行沒問題——排程器 2026-09-04 那天跑完整條線（供應層鏡射同步＋物化 28,000 因子列、
選股池 381 列）。但把同一天再跑一次，**供應層步驟會被 OOM killer 砍掉**（容器 exit 137），
訊號生成後 RSS 已 1.7 GB，鏡射同步再推一次大 delta 就爆。這不是新步驟造成的：
ML 推薦名單與洞悉分析排在供應層之後，根本沒被執行到。

**更危險的是 `docker compose run` 會回報 exit 0**，即使容器其實是被 SIGKILL（137）殺掉的。
用 `docker run` 才看得到真正的 137。任何靠 exit code 判斷成功的包裝都會被騙。未修，見 T-226。

**⑥ 映像少了 `libgomp1`，容器內完全不能推論（2026-09-07 實測，已修）。** `python:3.12-slim`
沒帶 OpenMP runtime，`import lightgbm` 會炸在 `libgomp.so.1`。這代表每日 pipeline 的 ML 步驟
在 Docker 裡**永遠失敗**——而它是 best-effort，所以只會安靜地什麼都不產出。
Dockerfile 已加 `libgomp1`。教訓：先前「新步驟在部署映像裡驗過了」其實只驗到匯出，
因為 scores parquet 是在**主機**上先產生的，推論那段根本沒被走到。

**⑦ 籌碼資料的三個硬邊界**（2026-09-07 實查）：`broker_trades.avg_price` 2022–2026 **全空**（主力成本只能用「淨買股數 × 日 VWAP」近似）；
`shareholding_dispersion` 只有 **3 個週快照**（07-03／07-09／08-28，週抓沒穩定跑）；FinMind `TaiwanStockHoldingSharesPer`
（2010 起）**只限 backer/sponsor 付費層**。分點 top-15 淨買超偏散戶（T-016 三因子皆負），「有人先買」要用**連續買超天數**定義。

**⑧ 金庫用掉了，不得依它的結果調參（D-106 §5／D-107 §7）。**
七月那一段的教訓**不能**變成「加一個 X 濾網」然後回頭重跑長窗口——那會把金庫變成訓練集，
而且沒有第二個金庫可以驗證它。任何後續架構（多空、事件驅動、降 beta）
**必須另立新的保留期，且在動手前登記**。金庫期分數已寫進 `serving.ml_scores`
（`available_at` 為訊號日 19:00，非 `now`——否則 PitView 在每個金庫日都看不到自己的分數）。

**⑨ Docker 錯誤對話框的「Reset to factory defaults」會刪掉整個 PG（2026-09-10 實際發生）。**
那一下刪的是 `docker_data.vhdx`——所有容器、映像、資料卷，含 27 GB 的 PG；C: 是 NVMe＋TRIM，救不回。
遇到 Docker 起不來**一律按 Quit**，再照 `docs/HANDOFF-2026-09-04-optimization.md` 的殘留 socket 修法處理。
事故起點是**重運算併發**（兩個 materialize 同時跑把 PG 壓垮）——一次只跑一個。
大型 PG 工作做完**先 pg_dump 再做下一件事**；還原程序與重放腳本見
`docs/INCIDENT-2026-09-10-docker-reset.md`、`scripts/run_recovery.sh`。

**⑩ `/api/stocks/{sid}/factors` 對 ETF 會掃整張 `factor_values`（2026-09-19 實測，未修，T-244）。**
該表主鍵以 `factor` 開頭，端點卻用 `WHERE stock_id = ?` 找最新日：因子宇宙內的股票約 5 秒，
**不在宇宙的代號（0050 等）150 秒還沒回**（表上只有 `(factor, stock_id, date)` 主鍵與 `(factor, date)` 索引）。個股儀表板已改用
`/factors/history?days=1` 逐因子查（走主鍵，0050 為 0.1 秒），但「因子」頁籤仍打舊端點——
**不要在 ETF 頁面開「因子」頁籤**，也不要拿這個端點做批次查詢。

**⑪ 直接對 `prices_daily` 算標籤會得到三倍的基礎率——因為六碼權證（2026-09-22 實測）。**
2023 年的 `prices_daily` 有 **21,268 檔六碼代號（權證／ETF）、97.3 萬列**，佔全部列數的 73%，
而它們「60 日內漲 ≥ 50%」的基礎率是 **38.6%**（四碼普通股只有 7.65%，再排除公司行動視窗是 5.62%）。
把兩者混在一起算會得到 19.9%，看起來像「標籤很容易達成」。
**訓練本身是安全的**——`ml.dataset.assemble` 是從特徵 left join 到標籤，而特徵只來自
`factor_values`（只含四碼普通股），權證進不了訓練。但**任何直接對價表做的標籤健檢、
基礎率計算、事件研究都必須自己加 `stock_id ~ '^[1-9][0-9]{3}$'`**，否則數字會被權證帶走。


**⑫ 回測引擎原本完全不處理股票分割——已修（T-168，2026-09-23），但修正有四層。**
1:20 分割當天價格掉 95%，跳價防護會把部位當成資料異常強制出清、記成 −94% 的**真實虧損**
（D-143 實測 5314 與 6919 各賠掉一格）。**而且不對稱**：標籤那一側早就還原了，
所以模型用乾淨資料訓練、回測吃假虧損。修正含引擎、結果包 `splits` 明細、
`backtest/diagnostics.py` 與 `reporting/trade_journal.py` 的部位重建——
**只修引擎會立刻炸**（`ledger 賣超持倉：持有 11000、賣出 205000`）。
**已知未修**：宇宙衛生濾網（`max_jump_ratio`）也把分割當異常，會把該檔踢出候選 60 個交易日。

**⑬ `pg_dump` 在 Git Bash 下會被 MSYS 路徑轉換弄壞。** `docker exec ... -f /tmp/x.dump`
的 `/tmp/x.dump` 會被翻成 `C:/Users/.../Temp/x.dump`，pg_dump 在容器裡找不到那個目錄而失敗。
要用 `MSYS_NO_PATHCONV=1` 或雙斜線 `//tmp/x.dump`。


## 7. 還在跑的東西（2026-09-19 部署後）

四個容器，全部在 compose 專案 `twquant`、映像來源＝`main`（`392d00e`，含 #69／#70）：

| 容器 | 做什麼 | 位址 |
|---|---|---|
| `twquant-pg-1` | 供應層 PG | `localhost:5433` |
| `twquant-api-1` | 唯讀 JSON API | `http://localhost:8000/api` |
| `twquant-frontend-1` | 平台（含「ML 選股」「策略持股」）| `http://localhost:8899` |
| `twquant-scheduler-1` | **每日 19:00 跑完整 pipeline** | — |

每日 pipeline 的步驟（依序）：擷取 → 品質閘門 → 券商買賣超 → 訊號生成 → **指數** → 供應層 →
選股池快照 → **ML 推薦名單**（三個模型推論並寫進 PG，匯出前 20／觀察／風控剔除／**流動性剔除** D-113，尚未重建映像）→ **策略持股**（8 支）→ 洞悉分析。
粗體是 D-111／D-112／T-227 新增或改動的。**第一次由排程觸發的完整執行是 2026-09-21（週一）19:00**，
在那之前新步驟只被逐一驗過、沒有整條一起跑過——那天晚上值得看一眼
`docker logs twquant-scheduler-1`、`/api/platform/strategy-targets` 與 ML 選股頁的日期。

**重建映像的正確做法**（兩個坑都踩過）：

```bash
# 在一個 HEAD＝要部署的 commit 的目錄裡跑；主 checkout D:/project/twquant 曾落後 main 83 個提交
git fetch origin && git diff --stat origin/main HEAD    # 必須為空
docker compose --env-file D:/project/twquant/.env build api frontend
docker compose --env-file D:/project/twquant/.env up -d --no-deps api frontend scheduler
```

`--env-file` 不可省：少了它 `TWQUANT_DATA_HOST` 會退回 `./data`，容器就掛到 worktree 自己的空資料夾（§1 的兩個 `data/` 陷阱）。
`--no-deps` 是為了不碰 PG 容器。**不要跑 `docker compose config`**——它會把 `.env` 的密碼印出來。

洞悉分析（LLM）步驟目前是離線降級：`.env` 沒有 `ANTHROPIC_API_KEY`。

## 8. 備份（事故後）
- **2026-09-22 22:10：`twquant_serving_20260922-2210_post-crash-verify.dump`（5.5 GB）**——分點因子整批計算把 PG 連同 Docker 壓垮，WAL 當機復原完整、所有表列數與崩潰前一致之後做的。**目前最新的完整備份。**

- `data/backup/twquant_serving_20260903.dump`（2.40 GB，`pg_dump -n serving -Fc`）——**2026-09-10 事故的還原底**。
- 重放完成後 `scripts/run_recovery.sh` 最後一步會自動再做一份 `twquant_serving_<日期>_post_recovery.dump`。
- **仍然沒有排程式備份，也沒有異地副本**——兩份 dump 都在同一顆 D:。每週全量＋rclone 到 Drive 需使用者核可。
- `data/` 下的研究產出（結果包、模型、報告、快照）只存在 D:，同樣沒有異地副本。
- **2026-09-19 19:46：`twquant_serving_20260919-1936_complete.dump`（4.53 GB）** ——資料補到 09-18、
  09-02~09-18 已物化之後的第一份完整備份（`pg_restore --list` 驗到 19 張表）。同日 15:53 的
  `..._post_recovery.dump` 是**補資料進行到一半**的快照（分點只到 09-14 上下、估值全缺、
  因子停在 09-02），留著當事故還原點，不要當成現況。

## 9. 2026-09-19 資料補完（補到 09-18）

09-10 的 Docker reset 之後資料停在 09-09、衍生層停在 09-02。這一輪全部補齊：

| 到 2026-09-18 | prices_daily、institutional_daily、margin_daily、broker_trades、stock_valuation、index_daily、shareholding_dispersion、factor_values、composite_daily、factor_cross_stats、screener_daily、broker_flow_daily |
|---|---|
| **不是缺口、是正常** | corporate_events 09-07（最後一個實際事件）、monthly_revenue 2026-08-31（9 月營收 10/10 才公告）、financial_statements 2026-06-30（Q3 法定申報期限 11/14） |

**⚠ `factor_values` 在 2026-09-01／09-02 之間有斷階：09-01 以前每天 26 個因子，09-02 起 37 個。**
不是資料不夠——那些因子現在都算得出來，是當初物化那幾天時來源表還沒進 PG，而
`materialize._compute_universe` 把 `DataError` 記成 DEBUG 就丟掉（黃金規則 5 的漏洞）。
更早的歷史更破：2020-04-16~2026-08 只有 **21** 個因子，2015-06~2020-04-15 有 31~37 個
（那段是 09-19 被停掉的重物化做的）。**要消掉斷階＝重物化 2020-04-16~2026-09-01**，
實測 **10.6 秒/日**（不是 `materialize_long_history.py` docstring 那個 3.7 秒/日，那是 2015-2019
的資料量），1,570 個交易日約 **4.6 小時**。未做。

**物化一律用 `scripts/materialize_long_history.py`，不要用 `serving materialize`。**
後者含 3 個 `chip.broker_*` 因子，實測單一個因子單日就跑超過 10 分鐘（每天無條件掃 5,700 萬列，
T-150 未修）。前者排除 broker 與 ml 共 12 個因子、剩 37 個，13 天只要 4.9 分鐘。
那 3 個 broker 因子本來就不在任何一天的 `factor_values` 裡，排除不改變現況。

**這一輪修掉的兩個會靜默產生錯數字的缺陷**（`scripts/audit_data.py` 抓到，現在 critical 0）：
- `6949` 沛爾生醫 2026-09-07 **面額變更 1490 → 74.5（1:20）沒有進 `corporate_events`**
  ——那天會被當成 −95% 的真實報酬。分割清單 staging（`data/staging/finmind_splits.json`）
  停在事件發生前，**每次補資料都要重抓它**（FinMind `TaiwanStockSplitPrice` 不帶 `data_id`
  ＝全市場，1 個請求、全史只有 35 筆）。
- `2938` 床的世界（09-16 上市）在 `stock_meta` 沒有 `market`，會被市場濾網靜默排除。
  跑 `ingest-meta` 修好。**順帶治好** `price.industry_mom_6m` 與 `price.industry_rs_20d`
  ——它們本來每天丟 `ComputeError` 被 `_compute_universe` 吞掉，meta 一更新就正常。

**`stock_valuation` 不在任何每日擷取路徑裡。** `ingest/builtins.py` 只註冊 twse／tpex（出
prices／institutional／margin）與 tdcc（週）。估值只能靠 `scripts/backfill_finmind.py
TaiwanStockPER` **逐檔**回補：2,067 檔 × 6.42 秒 ＝ **3.6 小時**只為了 13 個交易日。
FinMind 免費層**不支援**全市場單日查詢（實測回 `Your level is register`）。官方 TWSE BWIBBU
端點一天一個請求就有全市場，做成正式來源並接進 `_run_daily_pipeline` 是待辦。
近期估值 staging **沒有現成載入器**（`load_finmind_chip.py` 只支援 institutional／margin，
`load_finmind_history_tables.py` 刻意只寫 2020 之前），這輪是照 `load_finmind_chip.py`
的規則臨時寫一支（重疊不覆寫＋零值分布比對）。

**⑩ 「回報成功」不等於資料進去了——請直接查 PG 的逐年天數。** 2026-09-20 實際發生：`load_finmind_chip.py` 回報
「已寫入湖與 PG：266 萬列」，但 2020~2024 的籌碼資料一列都沒進 PG——它只用「資料湖有沒有這一天」判斷，
而當時湖有 5,329 日、PG 只有 1,620 日。已修（湖與 PG 各自算）。**任何補資料之後都要自己查一次**：
```sql
select extract(year from date)::int y, count(distinct date) from serving.<表> group by 1 order by 1;
```
拿它跟 `prices_daily` 的同一查詢逐年對，數字不一樣就是有缺口。

**⚠ 多個 session 同時操作同一個 PG。** 這一輪一開始有另一個 session 在跑
`scripts/.run_recovery.running.sh`（09-10 事故的 15 步重放）與 `materialize_long_history.py`，
兩邊同時下 `serving sync` 直接吃到 PG `DeadlockDetected`、整個 transaction rollback。
**動手前先看有沒有別人在跑**：
```bash
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*twquant*' } | Select-Object ProcessId,CreationDate,CommandLine | Format-Table -AutoSize"
```
recovery 重放本身有 `logs/recovery.lock` 互斥鎖，但那只擋它自己的第二份，擋不住別的指令。
