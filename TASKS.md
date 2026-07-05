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
| T-009 | 來源轉接器：tdcc、mops 月營收、taifex | SPEC-001 | T-006 | B | todo |
| T-010 | 來源轉接器：finmind broker_trades、inst_holding | SPEC-001 | T-006 | B | todo |
| T-003 | 交易日曆：以人工核對靜態日曆檔為第一版（有 prices_daily 後改推導核對） | SPEC-000 AC-3 | T-002 | B | todo |
| T-005 | golden fixtures：以 T-007~T-010 抓真實切片（30 檔 × 2 年，含除息/減資/漲停/處置/月營收/當沖案例）+ 一次性下載腳本（附來源 URL 與 hash）+ 凍結輸入層 | CLAUDE.md §4 | T-004, T-007, T-009 | B | todo |
| T-011 | 倉儲核心：Warehouse/PitView（20:00 截點）、寫入協定、DuckDB SQL、清洗管線；還原價/品質閘門/backfill 待補 | SPEC-001 §4/§7 | T-004 | A | 部分完成 |
| T-012 | 因子框架：@factor 註冊表（名稱格式驗證）、price.mom_20d、compute、standardize、assert_no_lookahead | SPEC-002 | T-011 | A | todo |
| T-013 | IC 報告（純函數，落地在 reporting/）+ research_log schema + 本地因子模型基準 | SPEC-002 §4 | T-012 | B | todo |
| T-014 | P0/P1 價量因子 + 最小本地因子組（市場/SMB/HML/52週高/短反轉，作中性化基準） | STRATEGY §4/§5a | T-012 | B | todo |
| T-015 | P0 籌碼因子：投信買超（分主動/被動重驗 IC）、月營收動能（PEAD）、散戶密集度 | STRATEGY §4/§5a | T-012 | B | todo |
| T-016 | 主力分點因子：清洗管線（總公司排除、隔日沖標籤、鉅額剔除）+ 集中度系列 | STRATEGY §3 | T-010, T-012 | B | todo |
| T-017 | 策略框架：配置、宇宙、combine、overlays（三態 regime 狀態機；**不做波動縮放**，D-004） | SPEC-003 + STRATEGY §2 | T-012 | B | todo |
| T-018 | 回測引擎：執行模型、價格相依成本、漲跌停旗標判定、會計恆等、指標 | SPEC-004 | T-017 | A | todo |
| T-019 | 驗證工具：walk-forward、deflated_sharpe(trial_sharpes)、plateau、bootstrap | SPEC-004 §5 | T-018 | B | todo |
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
