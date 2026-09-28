# 東西在哪（2026-09-23，資料到 09-22；第十輪已結案、TAIL-B 已上架）

> **給下一個 session 的第一份文件。** 讀完這頁就能動手，不必翻其他文件。
> **這一輪的來龍去脈在 [docs/HANDOFF-2026-09-23-eight-rounds-and-pipeline.md](docs/HANDOFF-2026-09-23-eight-rounds-and-pipeline.md)**——
> 動手前先讀它的「一、使用者是誰、要什麼」與「四、我犯的錯」。
> 更早的在 [docs/HANDOFF-2026-09-09-long-window-and-vault.md](docs/HANDOFF-2026-09-09-long-window-and-vault.md)。

## 0. 三十秒版本

- **只留兩條線（D-163，2026-09-28）**：**大盤精選**（`CORE`＝E0，`lw_e0_ctrl`、排序模型 `lgbm_lw_net_h20_20260225_83890036`，紙上交易中）
  與 **潛伏飆股**（`QUIET`＝A20，`lgbm_quietA20_tu_h20_20260430_655df6d7`，每日名單 `scripts/a20_picks.py`，出場待第二保留期）。
  名字只是別名，檔名與指紋不改；不再開新的選股或出場輪次。
- **D-162 事後檢驗（A20 重訓到 2026-02、考 03-02 ~ 09-08）**：選股力 1.95×（前 15 名 26.1% vs 池內 13.4%）；
  抱滿 S0 **+43.7%**／−25.6%、T-ATR +19.1%／−29.3%、T-ATR-F15 +33.5%／−25.3%（0050 +38.8%／−15.4%）→ **兩個 T 臂被推翻**。
  腳本 `scripts/d162_score_window.py`、配置 `configs/strategies/*_2602.yaml`、結果包 `data/tracks_d162/`。不取代第二保留期。
- 最新 worktree：`D:/project/twquant/.claude/worktrees/quant-trading-strategies-268d12`，branch `claude/round11-conditional-vol`（第十一輪結果＋第十二輪進行中）
- **資料一律在主 checkout**：`D:/project/twquant/data/`（不進 git，但都在本機）
- **八輪加工已封檔（D-141）**：八輪沒有一輪改善 **E0**（年化 +27.8%／回撤 −41.4%／Calmar 0.67）。
  唯一被獨立驗證的是排序模型的金庫期 IC **+0.0816**。**不要在同一個問題上再加工。**
- **第九輪起換了問題（右尾／飆股，D-143）**：訊號通過（命中率倍率 **3.23×**、1,005 筆分布在 100 個月），
  **容器仍不行**（回撤 −39.7%~−54.1%、Calmar 0.26~0.32，低於 E0 與 V1）。交接見
  [docs/HANDOFF-2026-09-23-tail-round.md](docs/HANDOFF-2026-09-23-tail-round.md)。
  **第十輪（D-146 §七）已結案**：把 V1 的減碼容器套到右尾策略上**更差**（Calmar 0.32 → 0.17）——
  V1 瞄準絕對波動 16%，右尾策略專挑高波動股，76% 的日子曝險不到一半。分點因子在右尾上有增益（倍率 +0.32×）
- **⚠ 第九輪「訊號通過」已撤回（D-143 §十二）**：那 3 倍命中率幾乎全是「已經在飆」——TAIL-B 進場時過去 60 日
  中位已漲 +65%，對**同日同波動×同漲幅**的配對基準只有 **1.08×**（`scripts/d143_matched_lift.py`）。
  **任何命中率倍率一律附配對基準**，只贏全市場的不算模型優勢。平台上的 TAIL-B 名單＝動能熱門股清單。
- **第十一輪（D-149 §七）已結案**：「只在波動異常時減碼」沒有用——F1（看持股）Calmar 0.29、F2（看大盤）0.17，
  都不如 tail_b 的 0.32；唯一改善的是 F3「波動越大買越少」0.41。D-004 的估計器首次實作（`portfolio.core.vol_context`）。
- **第十二輪（D-151 §十五、§十六）已結案——十二輪來第一個在配對基準與保留段都站得住的選股訊號**：
  模型 A（**只看安靜 ∩ 流動股**，預測 20 日內碰到 +30%）每日前 20 名的命中率是同波動×同漲幅股票的 **1.34×**
  （walk-forward 十年每年 ≥ 1.22×、t +8.52），沒看過的保留段 2026-05~08 **1.39×**。**是機率優勢不是報酬優勢**
  ——九成五不會飆、中位數為負。模型 B（已飆股會不會再跌）九年平均沒用、只在含 2026-07 下跌的保留段有用
  （學的是大盤狀態）。模型：`data/models/lgbm_quietA_tu_h20_20260430_8bdcd508` 等六個；保留段已用掉。
- **第十二輪補充（D-152 §七）已結案——門檻梯度**：A20（20 日 +20%，`lgbm_quietA20_tu_h20_20260430_655df6d7`）對配對組
  超額報酬 +0.27pp（t +2.73）略高於 A 的 +0.22pp、A25 +0.11pp；A20 抓 +30% 飆股反而更準（1.40×）。**依事前規則第十三輪
  進場模型＝A20**（與 A 差 0.05pp 在雜訊內、沒有保留段驗證）。仍是機率優勢不是報酬優勢。
- **D-153 §〇 使用者裁決（2026-09-24 晚）：賣出模型照診斷重做**——標籤改結果不改路徑、樣本改「持有中的部位」、
  特徵加持有狀態與 A 的當日再打分、評估看決策價值。順序：**先算出場天花板 → 第十三輪規則臂（抱滿／移動停利／訊號消失）
  → 第十四輪才建模型出場**。第十三輪登記主體（D-153 §一 起）於任何第十三輪程式與結果之前寫定。
- **第十三輪（D-153 §六~§七）**：A20 進場＋四種規則出場，Calmar 0.19~0.34，**遠低於 E0 0.67 與 0050 同窗 0.87**；模型排序本身有用
  （同池亂數抱 20 日年化只有 +2.3%，模型 +8.3%）。**四種出場的排名隨資金規模整個翻轉**（8,000 萬 S1 最好、1,000 萬 S2 最好）——差距小於執行雜訊。
  **攫取率被持有天數灌水（§六 更正）**：S1 平均抱 46 日，換算每持有日反而最差；之後一律報「每持有日」並用同一最長持有期。
  零股組（`--lot-size 1`，D-153 §一 補充二）跑中，§四 的後續規則以它為準。
- **第十四輪（D-156）已結案 → A 線依 D-153 §四 收線**：每天判斷「賣還是抱」的模型挑賣哪一檔比隨機好（勝 20 個 seed 的 100%），
  但理論門檻讓九成部位 7 天內賣掉、換股成本吃光好處；每持有日比抱滿 M0 +0.005%（t −1.0）、M1（＋借券／當沖／注意／處置）+0.010%（t −0.5）——
  都未過事前門檻。**A 系列不再在開發期資料上加工**；還能做的乾淨一步是把 A20 登記進第二保留期（使用者決定）。
  附錄（D-156 §七）：扣成本後 21 天內抱愈久每天愈好。每日名單腳本：`scripts/a20_picks.py`（`--outcome` 價格只用到 09-08）。
- **D-155（使用者提議）安靜→5 日急拉 ≥15%→回測 5／10／20 日扣抵值：沒有優勢**（對配對超額 +0.04／−0.02／+0.09pp，t < 0.4），此線停、確認窗不開。
- **D-158（使用者提問，事後描述、非登記試驗）布林規則**：`scripts/bb_rule_profile.py`——等碰下軌才買明顯更差（會飆的 76% 不回下軌）；
  隔天買＋收盤破下軌停損＋離開上軌停利讓虧 >10% 的 16.8% → 9.3%，每持有日 +0.081% vs 抱滿 +0.074%（雜訊量級、未檢定）。
  要採用須另立登記進第二保留期，且回測引擎要先加布林出場。`scripts/blend_e0_a20.py`：E0／A20 日相關 0.68，混合 Calmar 下降，A20 不是 E0 的分散工具。
- **第十五輪（D-159）跑完（2026-09-28）**：下軌停損＋停利線、最長 121 天，對抱滿 21 天。逐筆 `data/logs/d159_trades_20260928.log`；
  組合 `scripts/run_round15.sh` → `data/tracks_round15/*.json`（1,000 萬零股），報表 `scripts/d153_report.py --bundles D:/project/twquant/data/tracks_round15`。
  逐筆：T 臂虧 >10% 變少、T-ATR 淨 ≥ +50% 是 S0 的 4 倍，但**每持有日月區塊兩臂對 S0 都是 t ≈ −3.5**（合併值較高是組成效果）。
  組合：T-ATR +18.7%／−36.0%／Calmar 0.52（S0 +8.3%／−44.4%）、T-MA +7.5%／**−57.3%**；T-ATR 仍輸 E0（0.67）且回撤超過 35% 門檻。
  **T-BRK（分點主力成本）依 §六 收線**；T-MA、T-ATR 照原樣留待第二保留期（2027-03），§六「開發期每持有日」用哪個值待使用者在開啟前裁決。
  S0 重跑與第十三輪結果包逐位元相同——引擎加三種出場沒有副作用。
- **D-161（使用者要求，看過 D-159 後事前登記）T-ATR-F15＝拿掉下軌、改固定 −15% 停損**：`scripts/d161_atr_fixed_stop.py`、`configs/strategies/quiet_t_atr_f15.yaml`。
  每筆 +2.41%、淨 ≥ +50% 4.36%（三臂最高）但平均抱 47 天、虧 >10% 37.4%，**每持有日三臂最低**；組合 +11.7%／−36.1%／Calmar 0.32。照原樣留待第二保留期。
- **停利文獻回顧**：`docs/reports/2026-09-25-exit-literature.md`（15 篇、逐篇核對出處）——固定停利對右偏報酬必傷期望值、停損買的是保險、
  出場應看狀態機率；第十四輪的標籤、對照臂（隨機賣出、Han 等 −10% 停損、μ̂−σ̂²/2 理論臂）照這份設計。
- **第 1 批免費籌碼（D-154／T-309，PR #98，分支 `claude/free-chip-data-t309`）**：`daytrade_daily`、`sbl_short_daily`、`attention_stocks`、
  `disposition_stocks` 四張**直寫 PG** 表（`serving rebuild` 不碰）。**全史都已入庫**（2014 起到 2026-09-24，筆數見 §2 那段）；
  FinMind 全量回補 2026-09-25 完成（`data/logs/chip_batch1_full_*.log`，0 失敗），之後已 pg_dump（`twquant_serving_20260925-1010_post-chip-batch1.dump`）。
  **注意**：證交所注意股的「累計次數」是查詢期間總數＝前視，不存；當沖數字 T+2 才定案，做因子前先量修正幅度（D-154 §三 待裁決）。
  每日增量 T-310 與估值每日來源 T-313 已併入本 PR（#99）；**合併部署後才每天跑**，在那之前這四張表與 `stock_valuation` 停在 09-24。
- **TAIL-B（第九輪 B 臂）已上架**平台「策略持股」頁（D-147）：列的是**今日候選前 15**，不是實際持股；
  模型每日推論（`configs/ml.yaml` 的 `research_live_models`，失敗不擋正式名單）
- **策略持股頁標新進／移出／連續天數、可切日期（T-300）；ML 選股頁有模型選單、可切日期（T-301／D-150）**：
  研究模型（`research_live_models`）每天也匯出推薦快照（只套流動性閘門）。部署後補 09-23 的右尾模型名單：
  `ml export-recommendations --date 2026-09-23 --model D:/project/twquant/data/models/lgbm_tailb_tu_h60_20260430_7ab9b0f9 --out D:/project/twquant/data`。
  **重跑 T-300 之前的日子的策略持股會被拒絕覆寫**（快照多了 `kind`／`score`）——要重出先把舊檔改名 `.superseded-…`
- **第二保留期（2026-09-09 起）已登記第九輪 B 臂**（D-148，`configs/holdout2.yaml`）：最早 2027-03-01 開、只開一次。
  **開啟前不得對保留期做任何績效統計**——包括把 TAIL-B 每日候選的後續漲跌算成勝率。判讀：`scripts/holdout2_tail.py`
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

- **第 1 批免費籌碼（T-309／D-154，2026-09-25）**：PG `serving.daytrade_daily`（當沖）、`sbl_short_daily`（借券賣出，股）
  來自 FinMind 免費層；`disposition_stocks`（處置）、`attention_stocks`（注意）來自證交所＋櫃買公告，2014 起、含權證。
  **直寫 PG、不在鏡射清單**——`serving rebuild` 不會碰它們，重抓用 `scripts/backfill_chip_batch1.py`（`report` 子指令只讀）。
  **全史已載入**（PG 實數，2026-09-27 查）：當沖 3,943,191 列／1,961 檔（2014-01-06 起）、借券 4,960,985 列／1,998 檔（2014-01-02 起）、
  處置 5,921 列、注意 69,650 列，四張都到 2026-09-24；FinMind 全量回補 0 失敗（借券恆等式違反 52 列，D-154 §四 的品質報告）。
  **每日增量已接（T-310／D-157，部署後生效）**：每日 pipeline 新步驟「籌碼」（也可 `twquant chip-daily --date <日>`）——**當天不抓**，
  當沖抓前 1／2／3 個交易日覆寫（T+2 才定案）、借券抓前 1 個交易日、最近 10 個交易日有缺自己補；處置／注意查一個窗（處置迄日往後推 14 天）。
  當沖第一次抓到的值另存 `serving.daytrade_first_pub`（量修訂幅度用，D-154 §三 仍待裁決——**當沖因子進研究前先看它**）。
  官方數字與 FinMind 歷史逐位相同；**`sbl_covering` 不要用**：官方沒有這一欄 → null，FinMind 全史是它自己補的 0。
  每日因子還沒有讀這四張表（只有第十四輪研究腳本 D-156 M1 用過）。

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
| **籌碼每日補抓（手動）** | `TWQUANT_DATA_DIR=D:/project/twquant/data TWQUANT_PG_DSN=... uv run --all-groups python -m twquant chip-daily --date <日>`（與 pipeline「籌碼」步驟同一個函數；單日單表用 `ingest --date <日> --tables daytrade_daily`）|
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


**⑭ 研究回測的模型分數會讓每日 pipeline OOM（2026-09-23 發生兩次，T-294／T-299 已修）。** `ml backfill-scores` 把研究模型的樣本外分數寫進生產同一張 `serving.ml_scores`；每日物化原本用全體最長回看（850 天）讀每張表，於是四年半的 ml_scores 整批進記憶體。表長到 4,162 萬列時程序在 15.3 GB 被砍。T-294 讓 ml_scores 只讀 30 天——但只修了物化，同一天重跑又在**策略持股快照**被砍（T-299）。**每日路徑讀 PG 一律經過 `cli._daily_pg_source`**，不要自己組 `PgTableSource`。研究分數仍在表內（T-295）。**另一個教訓**：`docker exec … | tail` 的結束碼是 tail 的——要看 pipeline 真的成功，查 heartbeat 或 `dmesg`（`wsl -d docker-desktop dmesg | grep -i oom`）。

**⑮ 命中率倍率一律附配對基準（D-143 §十二）。** 挑最熱、最會動的股票，60 日內大漲的機率天生就高（大跌也是）。
只跟全市場比會把動能／波動效應誤認成模型優勢——第九輪就是這樣宣布了一個 1.08× 的「3 倍訊號」。

**⑯ `ml.dataset` 與 `backtest` 會循環匯入。** `ml.dataset` 在模組層匯入 `backtest.costs`（觸發 `backtest/__init__`
→ engine → exposure）；`backtest.exposure` 因此必須在**函式內**匯入 `attach_adjustment`。先匯入 ml.dataset 的程序
（`ml train`）才會炸，同程序先匯入過 backtest 的測試抓不到——`tests/test_ml_state_pool.py::test_ac7` 用子程序守著。

**⑰ 給配置加選配欄位會讓凍結指紋／模型 id 變掉。** `StrategyConfig` 加欄位 → 加進 `reporting/holdout.py` 的
`_INERT_EXTENSIONS`（否則第二金庫的指紋測試紅）；`MlConfig` 加欄位 → 加進 `ml/train.py` 的 `_BACKCOMPAT_DEFAULTS`
（否則既有模型的目錄名對不回來）。只能列「預設值＝不存在時的行為」的欄位。

**⑱ 研究腳本一律設 `TWQUANT_DATA_DIR=D:/project/twquant/data`。** 否則研究登記簿（DSR 試驗數）寫進 worktree 自己的
`data/`——第九、十輪的 14 筆曾因此不在正本，2026-09-23 已搬回（572 → 586）。`run_tail_round11.sh`／`run_round12.sh` 已設。

**⑲ 事故還原後，2023 價格缺口曾經又回來（2026-09-24 再修）。** 09-10 還原到 09-03 的備份，`run_recovery.sh` 重放了
依賴它的因子重物化（T-149）卻漏了價格修補本身（T-136）——2023-04 上市股每天只剩 6 檔、台積電整月 0 列，兩週沒人發現。
已補回（`repair_prices_2023.py --write --lake ""`）、2023-02~12 因子重物化、重放腳本補上該步。**任何還原之後都要重跑
「每月上市／上櫃四碼股每日檔數」的檢查**——從沒修的輸入重算下游，看起來也是完整的。

**⑳ 回測／訓練在跑的時候，`src/twquant/` 的任何 .py 都不能改（2026-09-25 損失一支 45 分鐘的回測）。**
`twquant backtest` 在最後寫 `--out` 結果包時才 `import twquant.reporting.result_bundle`；我在它跑的期間給
`engine.py` 加了常數、`result_bundle.py` 改成匯入它——那個程序裡的 `engine` 是舊版，新版 `result_bundle`
找不到名字，`ImportError`、rc=1、research_log 也沒記。與「不要編輯正在跑的 bash 腳本」同一類但更隱蔽。
要改就等它跑完，或先停掉再改再重跑；`scripts/*.py`、`configs/`、markdown 不受影響（跑到那步才起新程序）。

**㉑ 程式裡有的因子，每日物化可能好幾週都沒算（2026-09-25 發現並補）。** T-284 的 8 支波動因子在 **9/01~9/22 每天 0 列**
（09-23 的整批重物化只到 08-31，部署的映像 09-23 起才開始算）；TAIL-B 那幾週的每日研究分數是在這 8 支全空下算的，沒有任何錯誤。
已用 `materialize_long_history.py --from 2026-09-01 --to 2026-09-24 --factors <8 支＋5 支型態>` 補齊（日誌 `remat_sept_13f_*.log`）；
TAIL-B 已匯出的快照不回頭改（D-150 不可變）。另外 `stock_valuation` **不在每日排程**（停在 09-18）→ `earnings_yield` 09-21 起空，T-313 接官方每日來源
（PR #99；09-21~24 已用官方端點補、三支估值因子已重物化）。**9/01 另有 5 支特徵整天漏算**（融資速度、券資比、市值、兩支產業動能；原始資料都在）——已補。
**檢查法**：對每個上線模型 `meta.json` 裡的特徵，看最近一個月**每天**的列數，不只看 `max(date)`。

## 7. 還在跑的東西（2026-09-19 部署後）

四個容器，全部在 compose 專案 `twquant`、映像來源＝`main`（`392d00e`，含 #69／#70）：

| 容器 | 做什麼 | 位址 |
|---|---|---|
| `twquant-pg-1` | 供應層 PG | `localhost:5433` |
| `twquant-api-1` | 唯讀 JSON API | `http://localhost:8000/api` |
| `twquant-frontend-1` | 平台（含「ML 選股」「策略持股」）| `http://localhost:8899` |
| `twquant-scheduler-1` | **每日 19:00 跑完整 pipeline** | — |

每日 pipeline 的步驟（依序）：擷取（T-313 起含估值）→ 品質閘門 → **籌碼**（T-310：當沖／借券／處置／注意，直寫 PG）→ 券商買賣超 → 訊號生成 → **指數** → 供應層 →
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

**`stock_valuation` 現在每天自動抓（T-313／D-157，2026-09-25，部署後生效）。** 證交所 `BWIBBU_d`、櫃買 `peQryDate`
接進既有的 twse／tpex 每日來源（資料湖 → 供應層鏡射同步進 PG，與行情同一條路）。09-18 兩市 1,965 檔與 FinMind 歷史逐位相同；
虧損公司「不計算」的本益比沿用歷史慣例存 0。**09-21~09-24 已補進 PG**（每天約 1,970 檔），但 **`factor_values` 的三支估值因子
（`fundamental.earnings_yield`／`book_yield`／`dividend_yield`）那四天仍是空的**——要補得重物化那 4 天
（`scripts/materialize_long_history.py --from 2026-09-21 --to 2026-09-24`，會連 composite 一起重算，未做）。
以前的做法（FinMind 逐檔回補，2,067 檔 × 6.42 秒 ＝ 3.6 小時只為了 13 個交易日；免費層不支援全市場單日查詢）不必再用。

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
