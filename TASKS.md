# TASKS — 實作任務佇列

規則見 CLAUDE.md §2；「模型」欄依 MODEL-POLICY.md §2（A=OPUS、B=SONNET、C=HAIKU）。
狀態：`todo | in_progress | done | blocked`。挑最小編號的、無未完成依賴的 todo。
完成時附上閘門輸出摘要。B/C 級任務合併前需 OPUS 級複審（見 MODEL-POLICY §2-2）。

**A 級任務測試先行（MODEL-POLICY §2-1，REVIEW H13）**：標「A」的任務拆成
`Txxx-t`（依 SPEC AC 撰寫並凍結測試，獨立 session）→ `Txxx`（實作通過凍結測試）；
Txxx 的 diff 不得包含 tests/ 變更（`git diff --stat` 檢查）。下表 A 級任務隱含此拆分。

| ID | 任務 | 規格 | 依賴 | 模型 | 狀態 |
|---|---|---|---|---|---|
| T-001 | 專案骨架：pyproject(uv)、src 佈局、`check` 閘門指令（含 IO 邊界靜態測試）、ruff/mypy/pytest、CI | CLAUDE.md §2/§4 | — | B | todo |
| T-002 | conventions 模組：TZ、assert_tw_timestamp、例外樹、pydantic 配置載入器（全 config 清單見 SPEC-000） | SPEC-000 | T-001 | B | todo |
| T-004 | 資料合約：schemas.py 全表 pandera 定義（含 inst_holding、limit_up/down_price、structural_breaks） | SPEC-001 §1 | T-002 | B | todo |
| T-006 | http 基礎設施：session、滑動窗限流器、重試、raw 版本化落地 | SPEC-001 §2/§7 | T-002 | B | todo |
| T-007 | 來源轉接器：twse_rwd（prices/T86/MI_MARGN） | SPEC-001 | T-006 | B | todo |
| T-008 | 來源轉接器：tpex（同三表，anti-bot headers） | SPEC-001 | T-006 | B | todo |
| T-009 | 來源轉接器：tdcc、mops 月營收、taifex | SPEC-001 | T-006 | B | todo |
| T-010 | 來源轉接器：finmind broker_trades、inst_holding | SPEC-001 | T-006 | B | todo |
| T-003 | 交易日曆：以人工核對靜態日曆檔為第一版（有 prices_daily 後改推導核對） | SPEC-000 AC-3 | T-002 | B | todo |
| T-005 | golden fixtures：以 T-007~T-010 抓真實切片（30 檔 × 2 年，含除息/減資/漲停/處置/月營收/當沖案例）+ 一次性下載腳本（附來源 URL 與 hash）+ 凍結輸入層 | CLAUDE.md §4 | T-004, T-007, T-009 | B | todo |
| T-011 | 倉儲核心：Warehouse/PitView（20:00 截點）、還原價（逐日換錨）、寫入協定、品質閘門、backfill 斷點續傳 | SPEC-001 §4/§5/§6/§7 | T-004, T-005 | A | todo |
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

（完成任務時把該列移到這裡並附證據摘要）
