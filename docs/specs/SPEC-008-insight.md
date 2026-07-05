# SPEC-008 — LLM 洞悉層（Insight Layer）

範圍：用 Claude API 讓系統「自己看報表、自己提出策略調整」，以及每日洞悉摘要。
這是系統的第四個閉環（見 LOOP.md §4：洞悉閉環）。

## 0. 安全邊界（比功能更重要，違反即嚴重違規）

1. **LLM 只能「提案」，不能「執行」**：洞悉層對 `configs/strategies/`、
   `configs/risk.yaml`、執行層完全沒有寫入路徑。它的輸出是提案檔
   （`proposals/` 目錄），提案必須通過研究閉環的機器閘門 + 人工核准才生效。
2. **提案不繞過任何閘門**：LLM 提的參數調整跟人提的走完全同一條驗證管線
   （回測 → walk-forward → deflated Sharpe，見 LOOP.md §1）。LLM 的角色是
   假說產生器，不是決策者。
3. **資料進 user turn，指令進 system prompt**：報表、數據、任何外部文字
   一律放 user message；system prompt 凍結不含變動資料（同時利於 prompt
   caching 與防 prompt injection）。
4. **可離線**：沒有 `ANTHROPIC_API_KEY` 時，量化系統其他所有模組必須完全
   正常運作。洞悉層是增強，不是依賴。

## 1. 模組結構

```
src/twquant/insight/
├── client.py        # Anthropic SDK 封裝：模型路由、用量記錄、成本上限
├── schemas.py       # pydantic 輸出模型（提案、日評、異常解釋）
├── daily_brief.py   # 每日洞悉摘要（盤後 pipeline 最後一步）
├── advisor.py       # 週頻策略調整提案
├── explainer.py     # 異常事件解釋（漂移超標、閘門失敗時觸發）
└── prompts/         # system prompt 模板（版本控管，改動走 PR）
```

## 2. 三個功能

### 2.1 每日洞悉摘要（daily_brief）
- 輸入：當日 DailyReport（SPEC-007）的結構化數據 + 近 20 日摘要統計
- 輸出：3-6 句繁中摘要（哪個 sleeve 貢獻/拖累、漂移狀態、值得注意的部位），
  附加在日報與 Telegram 推播
- 模型：`claude-haiku-4-5`（便宜、快，格式固定）

### 2.2 策略調整提案（advisor，週頻 + 手動觸發）
- 輸入：過去 4-12 週的歸因報告、因子健康度（RankIC 趨勢）、市場 regime 指標、
  當前策略 YAML、research_log 的歷史嘗試（避免重提測過的東西）
- 輸出（結構化，見 §3）：`no_action`（預設，必須是合法選項）、
  `param_change`（調整視窗/閾值）、`factor_reweight`、`factor_swap`、
  `risk_adjust`（曝險/限額建議）之一，附理由與預期影響
- 模型：`claude-opus-4-8` + adaptive thinking + `effort: high`
- 後續：提案落地 `proposals/YYYY-MM-DD-<id>.yaml`（狀態 `pending`）→
  系統自動對提案跑回測驗證 → 結果 attach → 使用者 CLI 核准
  （`python -m twquant proposal approve <id>`）→ 進模擬倉觀察期

### 2.3 異常解釋（explainer，事件觸發）
- 觸發：漂移超標、品質閘門連續失敗、kill-switch 狀態變更
- 輸入：事件上下文（哪個檢查、期望 vs 實際、相關時序數據）
- 輸出：1-3 段解釋（最可能原因排序 + 建議的人工檢查步驟），附進 CRIT 告警
- 模型：`claude-opus-4-8`（錯誤成本高，要最強推理）

## 3. API 使用規範（依 claude-api skill，實作時不得憑記憶）

```python
import anthropic
from pydantic import BaseModel

class Proposal(BaseModel):
    action: Literal["no_action", "param_change", "factor_reweight",
                    "factor_swap", "risk_adjust"]
    target_strategy: str
    changes: list[ProposedChange]     # 具體到 YAML 欄位路徑與新值
    rationale: str                    # 繁中，引用輸入數據的具體數字
    expected_impact: str
    confidence: Literal["low", "medium", "high"]

client = anthropic.Anthropic()        # key 從環境解析
response = client.messages.parse(
    model="claude-opus-4-8",
    max_tokens=16000,
    thinking={"type": "adaptive"},
    output_config={"effort": "high"},
    system=[{"type": "text", "text": ADVISOR_SYSTEM,      # 凍結模板
             "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": report_payload}],
    output_format=Proposal,
)
proposal = response.parsed_output     # 已通過 schema 驗證
```

規則：
- 模型 ID 只准用 `configs/llm.yaml` 裡列的字串（見 MODEL-POLICY.md），
  程式碼不得寫死模型字串
- 結構化輸出一律 `messages.parse` + pydantic；驗證失敗由 SDK 層 retry，
  連續 2 次失敗 → 記錄並放棄本次（不產生半格式的提案）
- system prompt 放 `insight/prompts/`，帶 `cache_control`（prompt caching）；
  任何會變動的內容禁止進 system prompt
- 每次呼叫把 `response.usage`（input/output/cache tokens）、模型、prompt 模板
  版本寫入 `llm_log.parquet`；日累計成本超過 `configs/llm.yaml` 的
  `daily_budget_twd` → 當日停用洞悉層 + WARN 告警
- 429/5xx 交給 SDK 內建 retry；仍失敗 → 該次功能靜默降級（日報無 LLM 摘要
  也要照常發出），不得阻塞 pipeline

## 4. 提案生命週期（狀態機）

```
pending → backtesting → validated | rejected_by_gate
validated → approved（人工）→ paper（60 交易日）→ promoted | demoted
任何狀態 → discarded（人工）
```

- 提案 YAML 含：提案內容、LLM metadata（模型、prompt 版本、tokens）、
  回測驗證結果、審核紀錄。整包進 git。
- `rejected_by_gate` 的提案回饋進下次 advisor 的輸入（「上次提的 X 沒過
  閘門，原因 Y」），這是洞悉閉環的學習迴路。

## 驗收條件

- AC-1：advisor 輸出不符 Proposal schema 時（mock 回傳壞 JSON），重試 2 次後
  放棄且不產生 proposal 檔，記錄 ERROR
- AC-2：洞悉層模組內不存在任何寫入 `configs/strategies/` 或 `configs/risk.yaml`
  的程式路徑；`apply` 動作只存在於 CLI 的 `proposal approve` 指令（靜態測試：
  grep insight/ 內無該路徑字串 + 單元測試驗證 API）
- AC-3：建立 pending 提案後，`proposal validate <id>` 自動以提案參數跑回測，
  結果寫回提案檔，狀態轉 `validated` 或 `rejected_by_gate`（用 golden fixture）
- AC-4：mock usage 使日成本超過 `daily_budget_twd` → 下一次 LLM 呼叫被拒且
  發 WARN；次日自動恢復
- AC-5：所有呼叫都在 llm_log.parquet 留有紀錄（模型、tokens、prompt 版本、
  成本），且與 mock 的 usage 數字一致
- AC-6：未設 `ANTHROPIC_API_KEY` 時：每日 pipeline 全綠跑完，日報產出無 LLM
  摘要欄位，無任何例外
- AC-7：`no_action` 提案不產生 proposal 檔，只記錄一行（避免提案疲勞）
- AC-8：daily_brief 的輸出長度 ≤ 500 字，且被完整附進日報與推播（fixture 驗證）

## 範圍外

自動核准（永遠需要人）、盤中即時 LLM 決策、用 LLM 產生交易訊號本身
（LLM 調「策略的參數與組合」，不直接產生買賣清單）、新聞/輿情資料接入（Phase 2+）。
