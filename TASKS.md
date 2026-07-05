# TASKS — 實作任務佇列

規則見 CLAUDE.md §2；「模型」欄依 MODEL-POLICY.md §2（A=OPUS、B=SONNET、C=HAIKU）。
狀態：`todo | in_progress | done | blocked`。挑最小編號的、無未完成依賴的 todo。
完成時附上閘門輸出摘要。B/C 級任務合併前需 OPUS 級複審（見 MODEL-POLICY §2-2）。

| ID | 任務 | 規格 | 依賴 | 模型 | 狀態 |
|---|---|---|---|---|---|
| T-001 | 專案骨架：pyproject(uv)、src 佈局、`python -m twquant check` 閘門指令、ruff/mypy/pytest 配置、CI | CLAUDE.md §2 | — | B | todo |
| T-002 | conventions 模組：TZ、assert_tw_timestamp、例外樹、pydantic 配置載入器 | SPEC-000 | T-001 | B | todo |
| T-003 | 交易日曆：從 prices_daily 推導 + 覆寫檔；next/is_trading_day | SPEC-000 AC-3 | T-002 | B | todo |
| T-004 | 資料合約：schemas.py 全表 pandera 定義 | SPEC-001 §1 | T-002 | B | todo |
| T-005 | golden fixtures：30 檔 × 2 年切片（含除息、漲停、處置、月營收公布日案例）+ 生成腳本 + 凍結 | CLAUDE.md §4 | T-004 | B | todo |
| T-006 | http 基礎設施：session、限流器、重試、raw 落地 | SPEC-001 §2 | T-002 | B | todo |
| T-007 | 來源轉接器：twse_rwd（prices/T86/MI_MARGN） | SPEC-001 | T-006 | B | todo |
| T-008 | 來源轉接器：tpex（同三表，anti-bot headers） | SPEC-001 | T-006 | B | todo |
| T-009 | 來源轉接器：tdcc、mops 月營收、taifex | SPEC-001 | T-006 | B | todo |
| T-010 | 來源轉接器：finmind broker_trades | SPEC-001 | T-006 | B | todo |
| T-011 | 倉儲核心：Warehouse/PitView、還原價、品質閘門、backfill CLI | SPEC-001 | T-004, T-005 | A | todo |
| T-012 | 因子框架：@factor 註冊表、compute、standardize、assert_no_lookahead | SPEC-002 | T-011 | A | todo |
| T-013 | IC 報告 + research_log | SPEC-002 §4 | T-012 | B | todo |
| T-014 | P0/P1 價量因子（52 週高點、量比、高量溢酬、短期反轉、波動、寬度） | STRATEGY §4 | T-012 | B | todo |
| T-015 | P0 籌碼因子：投信買超系列、月營收動能、散戶密集度 | STRATEGY §4 | T-012 | B | todo |
| T-016 | 主力分點因子：清洗管線（總公司排除、隔日沖標籤、鉅額剔除）+ 集中度系列 | STRATEGY §3 | T-010, T-012 | B | todo |
| T-017 | 策略框架：配置、宇宙、combine、overlays（三態 regime、條件式波動目標） | SPEC-003 + STRATEGY §2 | T-012 | B | todo |
| T-018 | 回測引擎：執行模型、成本、台股限制、指標、報告 | SPEC-004 | T-017 | A | todo |
| T-019 | 驗證工具：walk-forward、deflated Sharpe、plateau、bootstrap | SPEC-004 §5 | T-018 | B | todo |
| T-020 | 投組層：sleeve 組合、波動目標、限額、kill-switch 狀態機 | SPEC-005 | T-018 | A | todo |
| T-021 | PaperBroker（共用回測成交模組）+ 每日執行流程 + 對帳 | SPEC-006 | T-020 | A | todo |
| T-022 | ShioajiBroker（dry_run 預設、雙開關） | SPEC-006 | T-021 | B | todo |
| T-023 | 每日 pipeline 編排（ingest→gate→factor→signal→report）+ 排程 | LOOP.md §3 | T-021 | A | todo |
| T-024 | 監控：日報、漂移偵測、告警、儀表板 | SPEC-007 | T-023 | B | todo |
| T-025 | Sleeve A（籌碼×營收動能）完整策略上模擬倉 | STRATEGY §2 + LOOP §1 | T-024 | B | todo |
| T-026 | 洞悉層基座：LLM client 封裝、configs/llm.yaml、用量記錄與預算閘、prompt 回歸測試框架 | SPEC-008 §3 + MODEL-POLICY §4 | T-002 | B | todo |
| T-027 | daily_brief + explainer（含離線降級） | SPEC-008 §2.1/2.3 | T-024, T-026 | B | todo |
| T-028 | advisor + 提案生命週期（proposals/、validate/approve CLI、回饋迴路） | SPEC-008 §2.2/§4 | T-019, T-026 | A | todo |

## 完成紀錄

（完成任務時把該列移到這裡並附證據摘要）
