# 洞悉層實作規劃（2026-09-06）

> 這是規劃，不是規格。實作前要先把 §7 的待裁決項裁掉、再切成 SPEC-015 與工單。
> 依據：[SPEC-008](specs/SPEC-008-insight.md)（安全邊界與基座）、[PRD-002](PRD-002-insight-ml-recommendation-platform.md)
> （L0～L3 分層、證據包、輸出 schema、回饋 ML）、[SPEC-014](specs/SPEC-014-platform-console.md)（平台讀取面）、
> [D-080](../DECISIONS.md)（分期：選項 A）。

## 0. 三十秒版本

- **已經有的比想像多**：LLM client（離線降級、日預算閘、schema 重試、用量記錄）、凍結 prompt、
  提案生命週期、推薦快照（L0／L3 不可變）、洞悉證據包合約（引用與截點驗證）、回訓匯出契約——全在 repo 裡且有測試。
- **真正缺的只有三塊**：① 從供應層**組證據包**的程式；② 對每檔候選**跑結構化分析**的 task；
  ③ 把分析**接回平台**（候選的洞悉狀態、個股洞悉頁籤）。
- **一個資料現實**：目前**沒有任何消息／公告／法說來源**。`mops.py` 只抓月營收，`corporate_events` 只有除權息與減資。
  PRD-002 §4.2「消息與事件」在有資料源之前，只能誠實記為 `gap`。新聞接入是獨立研究線（PRD-002 §11），不在本規劃的關鍵路徑。
- **順序**：schema v2 → 證據包建構 → 分析 task（mock backend，不連網）→ CLI 編排（冪等）→ 平台接線 → pipeline 掛鉤。
  L2 規則分類與回訓**不在本輪**，各自需要事前登記的門檻。

## 1. 現況盤點（重用，不重寫）

| 已有 | 在哪 | 對本規劃的意義 |
|---|---|---|
| `InsightClient`：backend 可注入、無 key 離線回 `None`、日成本 ≥ `daily_budget_twd` 拒呼叫、schema 連兩次失敗放棄、每次寫 `llm_log` | `insight/client.py`（T-026） | 分析 task 直接用它；測試以 mock backend 跑，**不連網** |
| `LlmConfig`／`configs/llm.yaml`：模型字串唯一來源、任務→模型路由、TWD 成本估算 | `insight/config.py` | 新增 `candidate_analysis` 任務路由即可，程式不寫模型字串 |
| 凍結 system prompt ＋ `PROMPT_VERSION`；資料一律進 user turn | `insight/prompts.py` | 沿用同一防注入結構；新 prompt 進同檔並 bump 版本 |
| 提案生命週期（pending→validated→approved→paper） | `reporting/proposals.py`（T-028 部分） | 洞悉層**不碰**策略參數；本規劃不用提案路徑 |
| 推薦快照 `RecommendationSnapshot`：不可變、冪等、候選有 `insight_run_id`／`data_status` | `reporting/recommendation_snapshot.py`（T-205） | L0 來源；**快照不可事後改寫**，洞悉連結必須反向掛（見 §3.3） |
| 洞悉證據包 `InsightPack`：evidence 帶 published/available/captured、方向、`analysis` 帶 prompt／模型版本；**引用不存在或晚於截點一律拒絕** | `reporting/insight_evidence.py`（T-210） | L1 落地格式；`InsightAnalysis` 要擴成 PRD-002 §5 的欄位（§2） |
| `ml export-recommendations`：既有推論→快照，`as_of`＝D-008 截點 | `reporting/recommendation_export.py`（T-213） | 每晚的 L0 入口 |
| 回訓匯出契約：PIT 身分、全部候選、未成熟 `target=null`、LLM 輸出只當特徵 | `reporting/retrain_export.py`（T-214） | P4 的資料字典已定，本輪不啟動訓練 |
| 平台讀取面：`/api/platform/insights`、洞悉頁、個股推薦事件 | SPEC-014 T-206～T-212 | 接線點 |

**供應層可當證據的表**（PG `serving`，皆有 `available_at`）：`ml_scores`、`monthly_revenue`（含 `available_at_estimated`）、
`financial_statements`（statement／type／value）、`stock_valuation`（per／pbr／dividend_yield）、`institutional_daily`、`margin_daily`、
`shareholding_dispersion`、`broker_trades`、`corporate_events`（僅除權息／減資／分割）、`disposition_stocks`、`stock_meta`（**只有當前產業，無版本**）、`index_daily`。

**兩個必須修正的既有假設**

1. ~~SPEC-008 §3 的 `output_format=` 已棄用~~——**這條規劃時寫錯了**（2026-09-06 實作時查證更正）。
   棄用的是 `messages.create()` 上的 `output_format`；SPEC-008 與既有 `_anthropic_backend` 用的是
   `client.messages.parse(..., output_format=Model)`，正是目前建議寫法，**不需要改**。既有 backend 也早就用
   `thinking={"type": "adaptive"}`，沒有送會 400 的 `budget_tokens`。
2. `configs/llm.yaml` 與 MODEL-POLICY 的 OPUS 是 `claude-opus-4-8`；現行世代是 `claude-opus-5`（同價 $5／$25 per M，thinking 預設開）。
   換模型＝改 config ＋ 重跑 prompt 回歸（MODEL-POLICY §1），列入 §7 裁決。

## 2. 目標與非目標

**目標（＝PRD-002 P2 ＋ P0 尚缺的證據包）**：每個交易日盤後，對 L0 快照的前 N 檔候選，用供應層資料組出可引用、帶時間語意的證據包，
送 LLM 產生固定 schema 的分析，驗證後以不可變證據包落地，並在平台上可查。LLM 離線、超預算、輸出驗證失敗時，
L0 名單照常發布、候選維持 `pending`、證據包帶缺席原因。

**非目標（本輪明文不做）**

- L2 規則分類（精選／觀察／排除的**最終** `bucket`）：門檻需先進 DECISIONS 事前登記，另立工單。
- 任何訓練、回訓、模型晉級（D-066／D-080）。`recommended_bucket` 是 LLM 的**提案欄位**，永遠不是最終分類。
- 消息／公告／法說擷取（獨立研究線，需自己的來源、PIT、成本規格）。
- LLM 對 `configs/strategies/`、`configs/risk.yaml`、下單的任何寫入路徑（SPEC-008 AC-2 的靜態測試繼續生效）。
- 讀取頁觸發運算：GET 不呼叫模型（SPEC-014 §4／§6 已定）。

## 3. 架構

### 3.1 資料流

```
盤後 pipeline（ml score → ml export-recommendations 之後）
  RecommendationSnapshot (L0, mode=research)
    └─ 前 N 檔候選，每檔：
         evidence_bundle 建構（純函數，PitView/serving @ as_of）
           → InsightEvidence[]：support / contradict / gap，各帶 available_at ≤ as_of
         → InsightClient.complete(task="candidate_analysis", schema=CandidateAnalysis v2)
           （N 檔一起走 Message Batches：非即時、半價）
         → 驗證器：引用 ⊆ 證據、available_at ≤ as_of、數字只能來自被引用的證據
         → InsightPack（analysis 或 analysis=None + 缺席原因）→ save_pack（原子、冪等）
  平台：以 (run_id, stock_id) 把證據包 join 回候選（快照本身不改）
```

### 3.2 模組落點（遵守 IO 邊界，黃金規則 10）

| 層 | 模組 | 內容 | 邊界 |
|---|---|---|---|
| 純運算 | `insight/evidence_bundle.py` | 由**傳入的** DataFrame／dict 組 `InsightEvidence` 列表與 `missing` 清單 | 不讀檔、不查 DB；資料由呼叫端經 PitView／serving 取好傳入 |
| 純運算 | `insight/candidate_analysis.py` | 組 user payload（只放證據）、呼叫 `InsightClient`、跑驗證器 | 可 `import anthropic`（洞悉層例外）；不寫檔 |
| 純運算 | `insight/schemas.py` | `CandidateAnalysis` v2 輸出模型 | 同現有 `Proposal` 等 |
| IO | `reporting/insight_evidence.py` | 既有 `save_pack`／`index_packs`；新增 by-run 索引 | 唯一寫檔處 |
| CLI | `cli.py: insights-run` | 取資料、建包、批次呼叫、落地；`--out` 絕對路徑 | 編排；失敗不留半成品 |

### 3.3 三個關鍵設計決定

**快照不可變，所以洞悉連結反向掛。** 候選的 `insight_run_id`／`data_status` 在 T-205 的不可變快照裡；事後把分析「寫回」快照等於改寫已發布資料。
做法：`InsightPack` 已帶 `stock_id`，再加 `recommendation_run_id`；讀取端（`/api/platform/recommendations`、個股事件）在 reporting 層以
`(run_id, stock_id)` join，回傳**衍生的** `insight` 欄位（stance／提案分組／缺席原因／pack id）。快照檔案一個位元組都不動。

**冪等鍵＝輸入雜湊。** `insight_run_id = hash(recommendation_run_id, stock_id, bundle_hash, PROMPT_VERSION, model_id)`。
相同輸入重跑走 `save_pack` 的「相同內容保留」；證據或 prompt 版本一變就是新 pack（舊 pack 保留，不覆寫）。這與 PRD-002 §8「相同輸入 hash 不建立重複版本」一致。

**證據的 `url`。** `InsightEvidence.url` 必填；供應層衍生的證據沒有網址。用內部 URI `twquant://serving/<table>/<stock_id>/<date>`，
平台顯示時明標「內部資料」；未來新聞來源才是真網址。列入 §7 裁決。

### 3.4 證據包各區段（只用真實存在的欄位）

| 區段 | 來源 | 證據項（例） | 方向判定 | 缺口記法 |
|---|---|---|---|---|
| ML | `ml_scores` | 分數、`rank_pct`、model_id、與前一 run 的名次變化 | 不判方向（它就是 L0） | 缺前一 run → gap |
| 基本面 | `monthly_revenue`、`financial_statements`、`stock_valuation` | 最近月營收 YoY／MoM、連續成長月數、毛利率變化、PER／PBR | 由**規則**給 support／contradict（例：YoY>0 且加速＝support），LLM 不改方向 | `available_at_estimated=true` 的列標為 `estimated`，**不能當 support**（§7） |
| 籌碼 | `institutional_daily`、`margin_daily`、`shareholding_dispersion`、`broker_trades` | 外資／投信近 10 日淨買、券資比、集中度 | 規則給方向 | 富邦分點缺日 → gap |
| 產業 | `stock_meta`（當前）＋ sector-flows | 產業資金流、相對強弱、涵蓋檔數 | 規則 | **明標「當前分類，非歷史 PIT」**（T-209 已做） |
| 事件 | `corporate_events`、`disposition_stocks` | 近期除權息／減資、處置狀態 | 處置＝contradict；其餘 neutral（記為 gap 的補充） | **消息／公告／法說：恆為 gap，直到有來源** |

規則給方向、LLM 只整理與提出假說——這是 PRD-002 §5「收益、波動、排名由程式計算」的落實；也讓「支持／反對」可以在沒有 LLM 時照樣顯示。

## 4. LLM 呼叫規格（依 claude-api skill，實作時再讀一次，不憑記憶）

| 項目 | 規劃 |
|---|---|
| 模型 | 只從 `configs/llm.yaml` 取；建議新增 `candidate_analysis: opus` 且 OPUS 改 `claude-opus-5`（§7-a） |
| 思考 | Opus 5 預設 adaptive；`output_config.effort: high`；**不**送 `budget_tokens`（現行模型會 400） |
| 結構化輸出 | `client.messages.parse(..., output_format=CandidateAnalysis)`（既有 backend 已是此寫法，不需改） |
| 呼叫方式 | **Message Batches**：N 檔一批、以 `custom_id=insight_run_id` 對應（結果不保證順序）；成本減半、不佔即時額度 |
| Prompt caching | system prompt 凍結並掛 `cache_control`；證據包在 user turn。`cache_read_input_tokens` 為 0 就是有東西在打破前綴 |
| 拒答 | 檢查 `stop_reason == "refusal"`；記 `analysis_absent_reason = "refusal:<category>"`，不重試 |
| 預算 | 沿用日預算閘；批次送出前先用 `count_tokens` 估總量，超過當日剩餘預算就縮 N 而不是超支 |
| 用量記錄 | 沿用 `llm_log`（模型、tokens、prompt 版本、成本）；批次結果逐筆記 |

**成本估算（要實測，不是承諾）**：一檔證據包約 6～10k input、輸出約 1k。Opus 5 $5／$25 per M → 每檔約 $0.03～0.075；
N=30 → 每晚 $0.9～2.3，批次半價後 $0.45～1.1 ≈ **TWD 15～35／晚**，在 `daily_budget_twd: 300` 之內。
實作第一步就用 `count_tokens` 對真實證據包量一次，把數字寫回這裡。

## 5. `CandidateAnalysis` v2（PRD-002 §5 落成 pydantic）

```
analysis_schema_version: 2
stance: support | neutral | conflict | insufficient_data
supporting_evidence_ids / contradicting_evidence_ids: list[str]   # 必須 ⊆ 證據包
event_horizon: short | medium | long | unknown
risks: list[{kind, evidence_ids, note}]
missing_data: list[str]                                          # 對應證據包 gap
recommended_bucket: select | watch | exclude                      # 提案欄位，非最終分類
rationale: str                                                    # 繁中；只准引用 evidence_id
```

驗證器（拒絕即整份分析作廢、記缺席原因，**不留半格式**）：引用存在且 `available_at ≤ as_of`（T-210 已有）；
`stance=support` 時 `supporting_evidence_ids` 非空；`insufficient_data` 時 `missing_data` 非空；
rationale 出現的數字必須能在被引用證據的文字中找到（容忍千分位／百分比格式；嚴格度見 §7-d）。
v1 pack（T-210 的 summary＋cited ids）仍可讀，讀取端以 `analysis_schema_version` 分流。

## 6. 工單切分（建議 T-216～T-222；每張非測試 diff < 400 行）

| 工單 | 內容 | 先寫的 AC 測試（紅→綠） | 依賴 |
|---|---|---|---|
| T-216 | `CandidateAnalysis` v2 ＋ 驗證器；`InsightPack` 加 `recommendation_run_id`；v1 相容讀取 | 引用／截點／stance 一致性／數字驗證各一條；v1 pack 仍可 index | T-210 |
| T-217 | 證據包建構（純函數）：五區段、方向規則、gap、`estimated` 標記、`bundle_hash` | fixture 資料→證據列可重現（冪等）；`available_at > as_of` 的列被排除且記 gap；估計可得時間不得為 support | T-216 |
| T-218 | `candidate_analysis` task：凍結 prompt ＋版本、llm.yaml 路由、`_anthropic_backend` 改現行 API、批次模式、離線／預算／拒答→缺席原因 | mock backend：壞 JSON 兩次→無 pack、超預算→拒、離線→pipeline 綠；**測試不連網** | T-216、T-217 |
| T-219 | CLI `insights-run --date --run --top N --out`：組包、批次、驗證、落地；冪等鍵；失敗不留半成品 | 同輸入重跑 created=False；證據變更→新 pack 舊 pack 保留；任一檔失敗不影響其他檔 | T-218 |
| T-220 | 平台接線：recommendations API 回傳衍生 `insight`、個股頁「洞悉」頁籤、洞悉頁依 run 篩選；`recommended_bucket` 顯示為「LLM 提議」 | 瀏覽器 fixture：有分析／缺席／v1 pack 三態；快照檔案 hash 不變 | T-219 |
| T-221 | pipeline 掛鉤：`export-recommendations` 之後跑 `insights-run`，任何失敗不阻塞主線；一鍵跑今日全部資料要含此步 | 洞悉步驟 raise 時 pipeline 其餘步驟照常且結果含錯誤摘要 | T-219 |
| T-222 | （另需授權）outcome 到期補寫 ＋ 用 T-214 契約組回訓資料集；**不訓練** | 未成熟 target=null；排除股有 outcome | D-08x 裁決後 |

另立、不在關鍵路徑：**T-223 消息／公告來源接入**（PRD-002 §11 的獨立研究線：來源、PIT、成本、fixture 全要重新規格）。

## 7. 待裁決（進 DECISIONS.md 的 D-081，裁完才動手）

| # | 問題 | 建議 | 影響 |
|---|---|---|---|
| a | `candidate_analysis` 路由：`claude-opus-5`（品質、adaptive thinking）或 `claude-sonnet-5`（$2／$10） | 先用 opus-5 跑一個月並建 20 檔人工標註的 eval；有數據再決定降級 | llm.yaml、MODEL-POLICY §1 OPUS 列 |
| b | 每晚分析檔數 N | N=30（前 30 名），事前登記；不是「有預算就多跑」 | 成本、涵蓋 |
| c | `recommended_bucket` 是否顯示 | 存、顯示為「LLM 提議」並與最終分類欄位分開；L2 規則未定前**不得**當分組 | 平台 UI、T-220 |
| d | 數字驗證嚴格度 | 先實作但只記錄拒絕率，兩週後決定是否強制 | 分析可用率 |
| e | 供應層證據的 `url` 形式 | 內部 URI `twquant://serving/...`，UI 明標內部資料 | 合約 |
| f | `available_at_estimated` 的列能否當 support | 不能；只能是 `estimated` 中性證據 | PIT 誠實度 |

## 8. 驗收不變量（每張工單都要能證明）

- 測試**不連網**：LLM 一律 mock backend；沒有 `ANTHROPIC_API_KEY` 時全套閘門與每日 pipeline 全綠（SPEC-008 AC-6 精神）。
- 引用不存在／晚於截點／數字對不上 → 整份分析拒絕並記原因，不產生半格式資料。
- 推薦快照檔案在洞悉流程前後 **hash 相同**。
- 同輸入冪等、不同輸入新 pack、舊 pack 永不覆寫。
- `insight/` 內無 `open(`／寫檔／對 `configs/strategies|risk.yaml` 的寫入路徑（既有靜態閘門）。
- 平台三態分開：有分析／缺席（含原因）／尚未產生；讀取頁不觸發模型。

## 9. 風險

- **證據文字被當指令**：未來新聞接入後風險最高。防線＝資料只進 user turn、system 凍結、輸出 schema 固定、驗證器只認 evidence_id。
- **PIT 洩漏**：`available_at_estimated` 與 `stock_meta` 無版本是兩個已知洞；前者靠 §7-f，後者靠明標「當前分類」，並在回訓契約裡把產業當弱特徵。
- **把 LLM 提議當結論**：`recommended_bucket` 若被畫成分組，就是用 LLM 做了 L2。§7-c 擋在合約與 UI 兩層。
- **成本漂移**：批次＋預算閘＋事前 `count_tokens`；日誌有每檔成本，異常可查。
- **沒有消息資料就沒有「洞悉」的一大半**：本輪的分析主要在基本面／籌碼／事件；要誠實地在頁面說明消息區段為 gap，而不是讓 LLM 用常識補。
