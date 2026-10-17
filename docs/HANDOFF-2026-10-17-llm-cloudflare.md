# 交接：洞悉層走 Cloudflare Workers AI 免費額度（T-368／D-185，2026-10-17）

## 一、使用者要什麼

2026-10-17 連續問：「有什麼可以利用 LLM 來輔助這個系統」→「用 LLM API 有免費的嗎」→「我現在付費的 Max 沒用嗎」→
「那我要 cloudflare 方案試試」→「建立 PR 並交接一下文件」。

查證結論（都寫進 D-185 §背景）：

- 洞悉層（SPEC-008）基座早就蓋好，`candidate_analysis`／`daily_brief`／`explainer`／`advisor` 都有程式與測試，
  但 `.env` 沒有 `ANTHROPIC_API_KEY`，證據包至今 0 份；雲端 pipeline 把洞悉步驟放在 `skip_pipeline_steps`。
- Anthropic API 沒有永久免費層；haiku-5-5 跑每晚 30 檔約 1.2 TWD／日，opus 約 62 TWD／日（T-218 實測用量）。
- **Claude Max 訂閱只涵蓋 claude.ai 與 Claude Code**（也就是這些開發 session），拿訂閱 OAuth token 給自己的程式呼叫 API
  自 2026 年初起明文禁止。開發繼續用 Max，pipeline 那條要另外付或走免費供應商。
- Cloudflare Workers AI 每天 10,000 Neurons 免費（UTC 00:00 重置），本專案已在用 Cloudflare（Pages、Worker cron、Hyperdrive）。

使用者選 Cloudflare。**我提的其他 LLM 用法**（為什麼選它白話段落、每日摘要推播、追蹤股問答、MOPS 公告分類、PR 規則審查員、
「這個試過了嗎」查詢、交接文件起草、explainer 接 pipeline）只有口頭討論，沒有登記、沒有工單。

## 二、做了什麼（分支 `claude/cloudflare-workers-ai-backend`，commit c24cac1）

| 檔 | 要點 |
|---|---|
| `configs/llm.yaml` | 模型規格加 `provider`（預設 `anthropic`，既有寫法不變）與 Neurons 費率；新模型 `cf_qwen3`＝`@cf/qwen/qwen3-30b-a3b-fp8`；`candidate_analysis`／`daily_brief` 路由到它，`advisor`／`explainer` 留 opus；`daily_neuron_budget: 9000`、`request_timeout_s: 120` |
| `insight/config.py` | `ModelSpec.provider`、Neurons 欄位，cloudflare 缺費率拒絕載入；`LlmConfig.neurons`／`neurons_for_model` |
| `insight/client.py` | 依任務路由的供應商選 backend；`unavailable_reason(now, task)` 點名缺哪個環境變數；Cloudflare REST `POST /accounts/{id}/ai/run/{model}` 帶 `response_format json_schema`；回應只去 `<think>` 與 code fence 後交 pydantic；沒有 `usage` 視為失敗；HTTP 傳輸可注入（`http_post`），真傳輸用標準庫 urllib |
| `reporting/llm_log.py` | `today_neurons(data_dir, utc_day, rate)` 依 **UTC 日** 累計；`record_usage` 檔名加亂數尾碼（見 §三 第一條） |
| `insight/schemas.py` | `PingReply`（連線測試用） |
| `cli.py` | `llm-ping --task <task>`：印供應商、模型、token、Neurons、等值成本；缺憑證印具名原因並 exit 1 |
| `tests/test_insight_cloudflare.py` | 11 條 AC 測試，全部注入假傳輸、不連網 |
| 文件 | DECISIONS D-185、TASKS T-368、CHANGELOG、SPEC-008 §3、MODEL-POLICY §1、WHERE-IS-EVERYTHING、`.env.example` |

閘門：ruff、format、mypy、pytest **1821 passed／49 skipped**（基線 1810）。

## 三、陷阱與發現

- **用量紀錄會互相覆蓋（既有缺陷，已修）**：`record_usage` 的檔名只用 `now` 的時間戳，而 `run_insights` 對整批候選傳同一個 `now`，
  30 筆用量會寫成同一個檔名、只剩 1 筆，TWD 與 Neurons 預算閘都因此少算。檔名已加亂數尾碼，有回歸測試
  `test_ac4_same_timestamp_records_do_not_overwrite_each_other`。
- **Neurons 要按 UTC 日算，不是台北日**：Cloudflare 在 UTC 00:00（台北 08:00）重置。GitHub 備援排程常晚 5 小時，21:13 的班可能在台北 02:13 才跑，
  那與前一天 17:33 的主跑是同一個 UTC 日。`today_neurons` 依 `ts` 轉 UTC；naive 時間戳（舊紀錄、測試）視為台北。
- **JSON mode 不保證**：Cloudflare 文件明列支援 `response_format json_schema` 的只有 llama-3.3-70b 等幾個模型，qwen3 的模型頁只寫「有 `response_format` 欄位」。
  文件也說可能回 `JSON Mode couldn't be met` 錯誤。所以解析只做兩件容錯（去 `<think>`、去 code fence），其餘照 SPEC-008 AC-1 連續失敗兩次就放棄。
  llama-3.3-70b 每晚約 12,500 Neurons，超過免費額度，只有把 N 降到 20 以下才用得起。
- **pydantic 的 `$defs`**：`CandidateAnalysis` 含巢狀 `InsightRisk`，`model_json_schema()` 會產生 `$defs`＋`$ref`，原封不動送出。
  Cloudflare 吃不吃 `$ref` 沒有文件可查，只有實連才知道。
- **IO 邊界**：`insight/` 是純運算層裡唯一能呼叫 LLM 的例外（`tests/test_io_boundary.py` 禁 `requests`／`httpx`／`urllib3`，不禁標準庫 `urllib`）。
  不要為了方便把 `requests` 引進去。
- **`test_insight_client.test_sample_llm_yaml_loads` 的斷言改了**：原本斷言 `candidate_analysis` 走 `claude-opus-5`（D-081a），依 D-185 改為 `provider == "cloudflare"`。
  這是配置裁決改變，不是放寬測試；D-081a 的 opus 路由在 D-185 §3 明記為「試跑期間暫改」。
- **沒有真實連線過**：這台機器沒有 Cloudflare 憑證。請求形狀依 Cloudflare 文件（REST 端點、`messages`、`response_format`、`result.response`／`result.usage`），
  另容許 OpenAI 形狀 `result.choices[0].message.content`。第一次 `llm-ping` 失敗時先看印出的 Cloudflare 錯誤原文。

## 四、使用者要做的（照 D-185「怎麼試」）

1. Cloudflare 後台建自訂 token，權限只給 Account › Workers AI › Read；帳號 ID 在 Workers & Pages 右側欄（與部署網站的 `CLOUDFLARE_ACCOUNT_ID` 同一個）。
2. 本機 `.env` 加 `CLOUDFLARE_ACCOUNT_ID`、`CLOUDFLARE_AI_TOKEN`（格式見 `.env.example`）。
3. ```bash
   uv run --all-groups python -m twquant llm-ping --task candidate_analysis
   uv run --all-groups python -m twquant insights-run --run <run_id> --top 3 --out D:/project/twquant/data
   ```
4. JSON mode 不通就在 `configs/llm.yaml` 換候補模型（D-185 §2：`@cf/google/gemma-4-26b-a4b-it`、`@cf/zai-org/glm-4.7-flash`），記得附定價頁的 Neurons 費率。

## 五、還沒做

- **雲端 pipeline 開洞悉步驟**：仍在 `configs/serving.yaml` 的 `skip_pipeline_steps`。要開得先解決雲端沒有推薦快照檔（`run_id_for_day` 讀 `data/recommendations/*.json`，
  雲端 runner 每次都是新機器）；T-332 的雲端流程寫的是 PG `daily_picks`，洞悉步驟要改成從那裡取名單。另立任務。
- **本機 Docker 排程**：要重建映像（照 memory／§4 的部署步驟）並在 `.env` 補兩個變數，排程才會走新路由。
- **判讀**（D-185 末段，兩週後）：看 `data/insights/` 缺席比率與 `unverified_numbers` 比率；缺席超過三成換候補模型或改 N。這是觀察，不是事前登記的門檻。
- advisor／explainer 仍走 Claude（沒排程）；要全部走 Cloudflare 得另記一條裁決。
- §一 列的其他 LLM 用法都沒有工單；使用者要哪一個再登記。

## 六、實連結果（同日第二個 session）

細節在 DECISIONS D-185「實連補記」。重點：

- **連得上、跑得出來**：wrangler 登入（`npx wrangler whoami` 會刷新 token）的 OAuth token 有 `ai:write`，可以拿來臨時測試；
  正式排程仍要使用者建專用 token 填 `.env`（§四 第 1、2 步沒變）。帳號 ID 見 `npx wrangler whoami`。
- 修了兩個實連才看得到的問題（推理用光 `max_tokens` 回空內容、受限解碼省略有預設值的欄位），前 10 檔 10/10 產出。
- **本機 PG 沒開時**：`insights-run` 只讀 PG，可把 `TWQUANT_PG_DSN` 暫時指向 `.env` 的 `TWQUANT_SUPABASE_DSN`。
  CLI 不會自己讀 `.env`，要先 export。
- **下一步要使用者決定**（都沒做）：
  1. 新聞證據：10 檔有 9 檔「資料不足」，主因 `events_news_unavailable`。接 MOPS 重大訊息（PG 已有 `material_news`）當證據，或改提示。
  2. 提示加 `/no_think`：便宜約三倍、實測 8/8 通過，但屬提示改版。
  3. 雲端 pipeline 開洞悉步驟（§五 第一條）。
