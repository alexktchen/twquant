# twquant — 台股系統性量化交易平台

一套模組化、配置驅動、可由 AI 代理持續開發與維運的台股量化交易系統。

## 文件導讀（依閱讀順序）

| 文件 | 內容 | 讀者 |
|---|---|---|
| [CLAUDE.md](CLAUDE.md) | AI 實作規則書：黃金規則、開發閉環、完成定義 | 任何要動這個 repo 的 AI/人 |
| [MODEL-POLICY.md](MODEL-POLICY.md) | 模型分工：開發任務分級（Opus/Sonnet/Haiku）與運行時 LLM 路由 | 領任務前、配置洞悉層時 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 系統架構：模組邊界、資料合約、擴充點 | 實作前必讀 |
| [docs/LOOP.md](docs/LOOP.md) | 四大閉環：研究、開發、營運、洞悉 | 理解系統如何自我修正 |
| [docs/STRATEGY.md](docs/STRATEGY.md) | 策略設計書：台股實證結論、sleeve 設計、主力分點因子規格 | 實作因子/策略時讀 |
| [docs/specs/](docs/specs/) | 各模組規格書（含逐條驗收條件） | 實作該模組時讀 |
| [TASKS.md](TASKS.md) | 實作任務佇列（含依賴順序與模型分級） | 挑任務時讀 |
| [DECISIONS.md](DECISIONS.md) | 決策紀錄：規格模糊處的提問與裁決 | 遇到規格衝突時讀寫 |

## 系統一句話

官方公開資料（+ FinMind 分點）進 point-in-time 資料倉儲，因子註冊表把資料變成因子，
策略以 YAML 配置組合因子產生目標權重，回測引擎與實盤共用同一套成本與約束模型，
執行層接券商 API，監控層每日比對「實盤 vs 回測預期」並在漂移超標時自動降風險。

## 模組總覽

```
twquant/
├── src/twquant/
│   ├── data/          # SPEC-001 資料層：來源轉接器、倉儲、PIT 視圖
│   ├── factors/       # SPEC-002 因子註冊表：純函數因子 + 標準化 + IC 報告
│   ├── strategy/      # SPEC-003 策略框架：YAML 配置 → 目標權重
│   ├── backtest/      # SPEC-004 回測引擎：成本、漲跌停、驗證協議
│   ├── portfolio/     # SPEC-005 投組與風控：組合、波動目標、限額
│   ├── execution/     # SPEC-006 執行層：券商轉接器、下單、對帳
│   ├── monitoring/    # SPEC-007 監控：績效歸因、漂移偵測、告警
│   └── insight/       # SPEC-008 LLM 洞悉層：日評、異常解釋、策略調整提案
├── configs/           # 策略與系統 YAML 配置（參數只准放這裡）
├── data/              # Parquet 資料湖（raw/clean/feature/signal 四層）
├── tests/             # 測試（golden fixtures 在 tests/golden/，不可修改）
└── docs/              # 本文件體系
```

## 快速開始（實作完成後）

```bash
uv sync                          # 安裝相依
python -m twquant check          # 開發閘門：lint + type + test 一次跑完
python -m twquant ingest --date 2026-07-03   # 拉一天資料
python -m twquant backtest configs/strategies/momentum_52wh.yaml
```

## 完整文件索引

- 根目錄：[CLAUDE.md](CLAUDE.md)（AI 實作規則）· [MODEL-POLICY.md](MODEL-POLICY.md)（模型分工）· [ARCHITECTURE.md](ARCHITECTURE.md)（架構書）· [TASKS.md](TASKS.md)（任務佇列）· [DECISIONS.md](DECISIONS.md)（決策紀錄）· [CHANGELOG.md](CHANGELOG.md)
- 設計文件：[docs/LOOP.md](docs/LOOP.md)（四大閉環）· [docs/STRATEGY.md](docs/STRATEGY.md)（策略設計書）· [docs/research/PAPERS.md](docs/research/PAPERS.md)（76 篇文獻驗證總表）
- 模組規格：[SPEC-000 全域慣例](docs/specs/SPEC-000-conventions.md) · [SPEC-001 資料層](docs/specs/SPEC-001-data.md) · [SPEC-002 因子註冊表](docs/specs/SPEC-002-factors.md) · [SPEC-003 策略框架](docs/specs/SPEC-003-strategy.md) · [SPEC-004 回測引擎](docs/specs/SPEC-004-backtest.md) · [SPEC-005 投組與風控](docs/specs/SPEC-005-portfolio.md) · [SPEC-006 執行層](docs/specs/SPEC-006-execution.md) · [SPEC-007 監控](docs/specs/SPEC-007-monitoring.md) · [SPEC-008 LLM 洞悉層](docs/specs/SPEC-008-insight.md)

## 狀態

目前為規格階段。實作依 [TASKS.md](TASKS.md) 順序進行，每個任務對應規格書中的驗收條件。
