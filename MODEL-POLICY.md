# MODEL-POLICY — 模型分工政策

本文件定義兩件事：（一）開發時，哪一級任務用哪個模型實作與審查；
（二）運行時，洞悉層（SPEC-008）的 LLM 路由。
原則：**品質由閘門保證，不由模型保證**——`python -m twquant check`、黃金規則、
驗收條件對所有模型一視同仁；分級只影響成本與速度，不影響驗收標準。

## 1. 可用模型（唯一合法字串表）

| 代號 | 模型 ID | 定位 | 相對成本 |
|---|---|---|---|
| OPUS | `claude-opus-5` | 最強推理；架構、核心引擎、審查 | 高 |
| SONNET | `claude-sonnet-5` | 高性價比；規格明確的模組實作 | 中 |
| HAIKU | `claude-haiku-4-5` | 快與便宜；機械性工作、固定格式輸出 | 低 |
| CF_QWEN3 | `@cf/qwen/qwen3-30b-a3b-fp8`（Cloudflare Workers AI，D-185） | 試跑期間的 `candidate_analysis`／`daily_brief`；免費額度 10,000 Neurons／日 | 免費額度內 0 |

程式碼與提示詞中禁止寫死模型字串；一律從 `configs/llm.yaml` 注入。
模型規格的 `provider` 決定 backend（`anthropic` SDK 或 `cloudflare` REST）；Cloudflare 模型必須附 Neurons 費率（D-185）。
換模型 = 改 config + 重跑 prompt 回歸測試（見 §4），不改程式。

## 2. 開發時分級（對應 TASKS.md 的「模型」欄）

分級依據 = 錯誤成本 × 任務模糊度：

| 級別 | 特徵 | 模型 | 例子 |
|---|---|---|---|
| A | 錯了會汙染全系統：狀態機、金流會計、防前視、併發/重啟正確性 | OPUS（effort high，難題 xhigh） | 倉儲 PIT、因子框架、回測引擎、投組/kill-switch、執行對帳、pipeline 編排 |
| B | 規格與 AC 明確、介面已定死的模組實作 | SONNET（effort high） | 來源轉接器、因子實作、IC 報告、監控、洞悉層功能 |
| C | 機械性、可完全由閘門驗證 | HAIKU | 跑測試與失敗歸類、CHANGELOG/文件同步、格式修正、fixture 重新生成（腳本已存在時） |

配套規則（這些是閉環的一部分，不是建議）：

1. **測試與實作分離（Tier A）**：A 級任務的測試由獨立 session 依 SPEC 驗收
   條件先寫好並凍結，實作 session 不得修改測試（CLAUDE.md 黃金規則 7 本來
   就擋，這裡是把「寫測試的人 ≠ 寫實作的人」制度化，防止自我放水）。
2. **跨級審查**：B、C 級的 diff 合併前由 OPUS 級 session 做 code review
   （對照 SPEC 逐條 AC + 黃金規則掃描）；A 級的 diff 至少要另一個 OPUS
   session 複審。審查產出寫進任務完成註記。
3. **降級重試**：SONNET 對同一任務三輪閘門不過 → 升級 OPUS 接手，
   並在 DECISIONS.md 記錄卡點（規格可能有問題）。
4. **C 級的邊界**：HAIKU 不寫業務邏輯。它可以跑 `check --fast` 並把失敗
   分類成「規格問題/實作問題/環境問題」回報，但不動手修 A/B 級程式碼。

## 3. 運行時路由（洞悉層，configs/llm.yaml）

| 功能 | 模型 | 參數 | 頻率 | 失敗行為 |
|---|---|---|---|---|
| daily_brief 日報摘要 | HAIKU | 無 thinking；max_tokens 2000 | 每交易日 | 靜默降級（日報照發） |
| advisor 策略提案 | OPUS | `thinking: adaptive`、`effort: high` | 週五盤後 + 手動 | 跳過本週，WARN |
| explainer 異常解釋 | OPUS | `thinking: adaptive`、`effort: high` | 事件觸發 | 告警照發（無解釋段） |

- 預算：`daily_budget_twd`（初始 150）與 `monthly_budget_twd`（初始 2500）
  雙上限，超限停用洞悉層至次日/次月，發 WARN。用量從 `response.usage`
  記入 `llm_log.parquet`（SPEC-008 AC-4/AC-5）。
- 路由升降級：若 daily_brief 品質不夠（人工抽查），升 SONNET 只需改 config；
  反之 advisor 降級到 SONNET 需在 DECISIONS.md 裁決（錯誤成本高）。

## 4. API 使用規範（摘要，細節見 SPEC-008 §3）

- OPUS/SONNET：`thinking={"type": "adaptive"}`；禁用 `temperature/top_p/top_k`
  （這些參數在 Opus 4.8 / Sonnet 5 已移除，傳了會 400）
- 結構化輸出一律 `client.messages.parse(..., output_format=PydanticModel)`
- system prompt 凍結 + `cache_control: {"type": "ephemeral"}`（prompt caching；
  變動資料進 user turn）
- 長輸出（max_tokens > 16k）必須 streaming
- 429/5xx 用 SDK 內建 retry（max_retries=2），不自己寫重試迴圈
- **prompt 回歸測試**：`tests/prompts/` 放每個功能的 golden 輸入 fixture 與
  輸出 schema 斷言（不斷言字面內容，斷言結構與必要欄位）。換模型或改
  prompt 模板時必跑，過了才能改 config。這是「換模型不壞系統」的閘門。

## 5. 給未來模型的告知

任何模型（包括比現在更弱或更強的）進到這個 repo：
先讀 CLAUDE.md → 領任務時看 TASKS.md 的「模型」欄是否符合你的等級——
等級不符不是禁令，而是提醒：低於建議等級時，預期閘門會攔你更多次，
請更小步提交；高於建議等級時，不要順手擴大範圍（黃金規則不變）。
