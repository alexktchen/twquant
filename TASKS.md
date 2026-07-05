# TASKS — 實作任務佇列

規則見 CLAUDE.md §2；「模型」欄依 MODEL-POLICY.md §2（A=OPUS、B=SONNET、C=HAIKU）。
狀態：`todo | in_progress | done | blocked`。挑最小編號的、無未完成依賴的 todo。
完成時附上閘門輸出摘要。B/C 級任務合併前需 OPUS 級複審（見 MODEL-POLICY §2-2）。

**A 級任務測試先行（MODEL-POLICY §2-1，REVIEW H13）**：標「A」的任務拆成
`Txxx-t`（依 SPEC AC 撰寫並凍結測試，獨立 session）→ `Txxx`（實作通過凍結測試）；
Txxx 的 diff 不得包含 tests/ 變更（`git diff --stat` 檢查）。下表 A 級任務隱含此拆分。

| ID | 任務 | 規格 | 依賴 | 模型 | 狀態 |
|---|---|---|---|---|---|
| T-001 | 專案骨架：pyproject(uv)、src 佈局、`check` 閘門指令（含 IO 邊界靜態測試）、ruff/mypy/pytest、CI | CLAUDE.md §2/§4 | — | B | done |
| T-002 | conventions 模組：TZ、assert_tw_timestamp、例外樹、pydantic 配置載入器（全 config 清單見 SPEC-000） | SPEC-000 | T-001 | B | done |
| T-004 | 資料合約：schemas.py 全表 pandera 定義（含 inst_holding、limit_up/down_price、structural_breaks） | SPEC-001 §1 | T-002 | B | done |
| T-006 | http 基礎設施：session、滑動窗限流器、重試、raw 版本化落地 | SPEC-001 §2/§7 | T-002 | B | done |
| T-007 | 來源轉接器：twse_rwd（prices/T86/MI_MARGN） | SPEC-001 | T-006 | B | done |
| T-008 | 來源轉接器：tpex 上櫃行情（anti-bot headers）；法人/融資券改走 FinMind（D-014） | SPEC-001 | T-006 | B | done |
| T-009 | 來源轉接器：mops 月營收（done）；tdcc 股權分散、taifex 期貨（待補） | SPEC-001 | T-006 | B | 部分完成 |
| T-010 | 來源轉接器：finmind broker_trades、inst_holding | SPEC-001 | T-006 | B | todo |
| T-003 | 交易日曆：以人工核對靜態日曆檔為第一版（有 prices_daily 後改推導核對） | SPEC-000 AC-3 | T-002 | B | done |
| T-005 | golden fixtures：以 T-007~T-010 抓真實切片（30 檔 × 2 年，含除息/減資/漲停/處置/月營收/當沖案例）+ 一次性下載腳本（附來源 URL 與 hash）+ 凍結輸入層 | CLAUDE.md §4 | T-004, T-007, T-009 | B | todo |
| T-011 | 倉儲核心：Warehouse/PitView（20:00 截點）、寫入協定、DuckDB SQL、清洗管線；還原價/品質閘門/backfill 待補 | SPEC-001 §4/§7 | T-004 | A | 部分完成 |
| T-012 | 因子框架：@factor 註冊表（名稱格式驗證）、price.mom_20d、compute、standardize、assert_no_lookahead | SPEC-002 | T-011 | A | done |
| T-013 | IC 報告（純函數，落地在 reporting/）+ research_log schema；本地因子中性化待市值資料 | SPEC-002 §4 | T-012 | B | done |
| T-014 | P0/P1 價量因子 + 最小本地因子組（市場/SMB/HML/52週高/短反轉，作中性化基準） | STRATEGY §4/§5a | T-012 | B | 部分完成 |
| T-015 | P0 籌碼因子：投信買超（分主動/被動重驗 IC）、月營收動能（PEAD）、散戶密集度 | STRATEGY §4/§5a | T-012 | B | done |
| T-016 | 主力分點因子：清洗管線（總公司排除、隔日沖標籤、鉅額剔除）+ 集中度系列 | STRATEGY §3 | T-010, T-012 | B | todo |
| T-017 | 策略框架：配置、宇宙、combine、overlays（三態 regime 狀態機；**不做波動縮放**，D-004） | SPEC-003 + STRATEGY §2 | T-012 | B | 部分完成 |
| T-018 | 回測引擎：執行模型、價格相依成本、漲跌停旗標判定、會計恆等、指標 | SPEC-004 | T-017 | A | 部分完成 |
| T-019 | 驗證工具：walk-forward、deflated_sharpe(trial_sharpes)、plateau、bootstrap | SPEC-004 §5 | T-018 | B | done |
| T-020 | 投組層：sleeve 組合、**條件式波動目標**、限額、四態 kill-switch、data/state 持久化 | SPEC-005 | T-018 | A | todo |
| T-021 | PaperBroker（共用回測成交模組）+ 每日執行流程 + 對帳 | SPEC-006 | T-020 | A | todo |
| T-022 | ShioajiBroker（dry_run 預設、雙開關） | SPEC-006 | T-021 | B | todo |
| T-023 | 每日 pipeline 多波次編排（ingest→gate→factor→signal）+ heartbeat/watchdog + 排程 | LOOP.md §3 | T-021 | A | todo |
| T-024 | 監控：日報、漂移偵測（tracking error）、告警、唯讀儀表板 | SPEC-007 | T-023 | B | todo |
| T-025 | Sleeve A（籌碼×營收動能）完整策略上模擬倉 | STRATEGY §2 + LOOP §1 | T-015, T-016, T-024 | B | todo |
| T-026 | 洞悉層基座：LLM client 封裝、configs/llm.yaml（含 pricing/匯率）、用量記錄與預算閘、prompt 回歸測試框架 | SPEC-008 §3 + MODEL-POLICY §4 | T-002 | B | todo |
| T-027 | daily_brief + explainer（含離線降級） | SPEC-008 §2.1/2.3 | T-024, T-026 | B | todo |
| T-028 | advisor + 提案生命週期（proposals/、validate/approve CLI、回饋迴路） | SPEC-008 §2.2/§4 | T-019, T-026 | A | todo |
| T-029 | 容量分析：各 sleeve 周轉 × ADV 分布 + 衝擊函數 → 容量上限，寫回 STRATEGY §2 | STRATEGY §5a | T-018 | B | todo |
| T-030 | 待評估策略族回測 + DECISIONS 決策：配對/統計套利、指數 ETF 換股、除權息、融券軋空 | STRATEGY §5a | T-018 | B | todo |

> 依賴修正（REVIEW H15）：T-003 改以靜態日曆檔起步（不再假依賴 prices_daily）；
> T-005 依賴補上轉接器；T-023 的 report 步驟改為掛點預留、T-024 接上（解除 T-023↔T-024
> 循環）；T-025 補上 T-015/T-016。表格順序依實際依賴重排（T-004/006/007-010 先於 T-003/005）。

## 完成紀錄

- **T-019**（2026-07-06）驗證工具（SPEC-004 §5）。backtest/validation.py：
  walk_forward（連續互斥無縫測試窗，AC-9）、deflated_sharpe（DSR，用試驗 SR 變異數
  估期望最大 SR，REVIEW M11）+ deannualize_sharpe、bootstrap_sharpe_ci（決定性 seed）、
  param_plateau（因子 window 掃描找高原）。閘門全綠（114 tests）。
- **T-014（部分完成）**（2026-07-06）價量因子。price.high_52w（52週高接近度）、
  price.reversal_5d（5日反轉，dir=-1）、volume.vol_ratio_5d（量比）、
  volume.turnover_z_20d（量能 z）。反前視全綠（109 tests）。真實 IC（連續期）：
  reversal_5d 5日 t=-6.0 確認反轉（dir=-1 正確）；量能因子 5日偏負（放量後短期
  反轉）。**待接**：本地 SMB/HML/市場因子（需市值/在外股數），故中性化基準未齊。
- **T-018（部分完成）**（2026-07-06）回測引擎（SPEC-004）。backtest/：costs
  （台股檔位表、價格相依半價差 REVIEW M1、漲跌停 proxy、手續費含最低/證交稅/
  adv_impact 滑價）、engine（next_open 執行、整數張、先賣後買現金約束、漲跌停與
  參與率 unfilled、會計恆等 AC-6）、metrics（CAGR/Sharpe/Sortino/MDD/Calmar/
  周轉/勝率）。CLI backtest + configs/costs.yaml。ACs 2/3/4/5/6/8 通過（104 tests）。
  **真實回測**：sample 策略在連續期 2025-08~2025-12（104 交易日）成本後
  Sharpe 1.12、CAGR +8.57%、MDD -4.17%、勝率 56.3%、0 未成交。**待接**：AC-1
  golden 凍結（需複審）、AC-7 處置強制出場（無 disposition 資料）、AC-9
  walk-forward（T-019）。
- **T-017（部分完成）**（2026-07-06）策略框架（SPEC-003）。strategy/：config
  （pydantic，AC-1 因子存在+最相近提示、權重加總=1）、universe（20 日 ADV 中位數 +
  最低價 + 四碼普通股）、combine（zscore_weighted、rank_avg；方向併入權重）、
  overlays（breadth_regime_filter，市場寬度由 prices 計算）、build（build_targets +
  signal_dates 用 T-003 日曆排週/月頻，AC-3）。CLI `targets`；configs/strategies/
  sample_revenue_trust.yaml。**真實資料端到端**：sample 策略在 2026-06-30 產出 40 檔
  等權目標（月營收 YoY 0.6 + 投信連買 0.4，zscore 組合、top40、寬度 overlay 後總曝險
  82%）。ACs：1/3/5/6/7 通過（95 tests）。**待接**：AC-2 處置/全額交割排除、上市日、
  市場別（無 disposition/stock_meta 資料）；AC-4 ic_weighted 組合（需 PIT IC 重估）；
  score_prop/inv_vol 權重；index_trend_filter（需加權指數）。
- **T-003**（2026-07-06）交易日曆（SPEC-000 AC-3）。data/calendar.py：
  TradingCalendar（is_session/between/missing/next_session/prev_session）、
  derive_from_prices（由 prices_daily 各來源日期聯集推導——TPEX 完整即還原真實
  開市日）、persist/load（單檔 clean/trading_calendar/calendar.parquet）、
  cross_check（各來源自身區間內缺口）。CLI calendar 指令。**禁用 weekday()**：
  status CLI 與儀表板缺口偵測改以開市日為準。**實測**：243 開市日、TPEX 無缺口
  （原誤報 18 假日缺口→歸零）、TWSE 正確顯示 101 個開市日 ban 缺口。閘門全綠
  （86 tests）。
- **T-015**（2026-07-06）P0 籌碼 + 基本面因子。factors/chip.py（trust_net_10d、
  foreign_net_10d、trust_streak、margin_velocity_5d）、factors/fundamental.py
  （revenue_yoy、revenue_accel，以公布日 available_at as-of 映射到日頻，PEAD）、
  factors/builtins.py（一次匯入註冊全因子，CLI/儀表板共用）。月營收因子 PIT
  as-of 測試 + 反前視護欄全綠（81 passed）。**真實資料 IC + 對抗驗證**（as_of
  2026-06-30，四碼普通股）：
  - `fundamental.revenue_yoy`：10/20 日 t=+7.3/+8.4，缺口前連續期仍 t≈+3.9→
    **最穩健**（月營收 PEAD 屬實，§1-4）。
  - `chip.trust_net_10d`：10 日 t=-10.2，**連續期仍 t=-11.6→真實反向**（非缺口
    假象），與 +1 先驗衝突，呼應 §1-2 投信擁擠反轉；符號待 T-019 定奪。
  - `chip.foreign_net_10d`：t≈-5 穩健負（外資買超偏反向，§1-3）。
  - `chip.trust_streak`：全期 t=+3.5 但連續期崩至 +1.4（缺口灌水，勿高估）。
  - `chip.margin_velocity_5d`：|t|<2，弱。散戶密集度真身（TDCC 集保）待接。
  未做：投信主動/被動拆分（需 ETF 持股）、chip.concentration_*（分點，T-016）。
- **T-013**（2026-07-05）IC 檢驗 + research_log。factors/ic.py（純函數）：前向報酬、
  逐日 RankIC（Spearman）、均值/t-stat/IR、五分位多空價差。reporting/research_log.py
  （IO 層，append-only 分片，trials 計數供 deflated Sharpe）。CLI ic-report。
  **真實資料驗證**：price.mom_20d 在 2025-H2 上，5 日 RankIC t=-2.32（顯著反轉）、
  20 日 t=-0.14（動能無效）——精準重現 STRATEGY.md 台股實證。本地因子中性化待
  市值資料。閘門全綠（72 passed）。
- **T-012**（2026-07-05）因子框架（SPEC-002）。@factor 註冊表（名稱格式驗證、
  重複/類別檢查）、compute 引擎（long format）、標準化管線（MAD 去極值→z-score，
  AC-4）、assert_no_lookahead 反前視護欄（AC-3，含壞因子反向測試）、price.mom_20d
  基準因子。因子為純函數（IO 邊界靜態測試涵蓋）。閘門全綠（67 passed）。
- **T-011 backfill**（2026-07-05）歷史回補 CLI。`backfill --from --to`（TWSE+TPEX
  日資料，跳週末、跳假日、依分區存在續傳）、`backfill-revenue --from --to`（逐月）。
  實測回補 2026-06-16~30：9 交易日新增、端午節(06-19)自動略過、06-30 續傳跳過；
  資料湖累積 10 個交易日全市場（每日約 6000-7000 檔）。閘門全綠（58 passed）。
- **T-009（部分）**（2026-07-05）MOPS 月營收轉接器（P0 因子）。t21sc03 Big5
  HTML 逐月抓（上市 sii + 上櫃 otc），yoy 自算（當月/去年當月−1，避開來源
  「去年同月增減」欄的 HTML 錯位）；營收千元→元、金融股可為負（放寬 nonneg）。
  monthly_revenue 加 date 分區鍵（月底）。CLI ingest-revenue。實測 2026-05：
  1848 家（上市 987 + 上櫃 861），YoY 因子可查。TDCC 股權分散、TAIFEX 期貨待補。
  閘門全綠（55 passed）。
- **T-008**（2026-07-05）TPEX 上櫃行情轉接器（D-014）。dailyQuotes（瀏覽器
  headers 繞 anti-bot、歷史日期、附次日漲跌停）；HttpClient 加 headers 支援；
  倉儲 write 加 source 參數（上市/上櫃按來源分檔共存於 prices_daily，不覆蓋）。
  實測 2026-06-30：全市場 6747 檔（上市 1361 + 上櫃 5386）。法人/融資券歷史
  走 FinMind（T-010）。閘門全綠（52 passed）。
- **T-011（部分）**（2026-07-05）倉儲核心 + 清洗管線 + DuckDB 分析。
  clean.py：三張 TWSE 表 raw→typed（單位張→股、去逗號、佔位符轉 null、加
  available_at 與 date）。warehouse.py：Parquet 分區資料湖、原子寫、分區級替換、
  PitView（D-008 的 20:00 截點，AC-9）、DuckDB SQL 查詢。CLI 加 ingest / sql。
  **實際運作驗證**：ingest 2026-06-30 存入 prices 1361 / 法人 1330 / 融資券 1279 列；
  SQL join 查投信買超前 10 名，重現「投信季底作帳、外資對作金融股」現象。
  閘門全綠（44 passed）。**待補**：還原價（需 T-009 corporate_events）、
  完整品質閘門、backfill CLI、raw 層落地整合。
- **T-007**（2026-07-05）TWSE rwd 轉接器完成。實盤抓取 2026-06-30 三張表成功
  （prices 1369 檔、法人 1330 檔、融資券 1279 檔）；台積電收盤 2410、投信買賣超
  -89,595 股等真實資料已驗。fixture 錄自實盤精簡（tests/fixtures/http/twse_rwd/），
  8 個離線測試驗解析/去重/異常（stat/空/不支援）。閘門全綠（35 passed）。
- **T-006**（2026-07-05）HTTP 基礎設施完成。滑動窗限流（AC-4）、指數退避重試、
  raw 版本化原子落地；clock/transport 可注入離線測試。閘門全綠（27 passed）。
- **T-004**（2026-07-05）資料合約完成。14 表 pandera schema，available_at 型別
  強制 tz-aware；AC-3 單位、strict、tz、負值測試。閘門全綠（22 passed）。
- **T-002**（2026-07-05）conventions 完成。TZ/assert_tw_timestamp/as_of_cutoff、
  例外樹、配置載入器（AC-1/AC-2）。tzdata 依賴。閘門全綠（14 passed）。
- **T-001**（2026-07-05）專案骨架完成。`uv sync --dev` 建 venv；
  `python -m twquant check` 跑 ruff check + ruff format --check + mypy(strict)
  + pytest 四項全綠（4 tests passed）；`--fast` 跑 ruff + pytest -x。
  IO 邊界靜態測試（tests/test_io_boundary.py）以 AST 掃描純運算層禁止的網路
  import 與寫檔呼叫，含反向測試防恆真。正反雙向驗證：注入 `import requests`
  到 factors/ → 閘門退出碼 1、IO 測試抓到違規；移除後退出碼 0。CI（.github/
  workflows/ci.yml）於 push/PR 跑同一閘門。完成定義各項已核。
