# SPEC-007 — 監控與閉環

範圍：每日報告、績效歸因、漂移偵測、告警、因子健康度。營運閉環（LOOP.md §3）
的度量與回饋端。

## 1. 每日報告（`monitoring/daily_report.py`）

盤後 17:00 產出（HTML + parquet + 推播摘要）：
- 損益：當日/MTD/YTD，帳戶與各 sleeve（用 TargetBook 的 sleeve_attribution）
- 部位：前 10 大持倉、產業分布、現金比
- 執行品質：實際成交價 vs 送單參考價（實現滑價 bps）、未成交清單與原因
- 風險：60 日已實現波動 vs 目標、回撤、kill-switch 狀態
- 資料品質：當日品質閘門各項結果

## 2. 漂移偵測（閉環核心）

每日用「同一訊號、同一成交模組」跑影子模擬（PaperBroker），與實盤逐日比較：
- `drift_daily = pnl_live - pnl_shadow`（TWD 與 bps 双記）
- 滾動 20 日 drift 年化標準差 > configs 閾值（初始 3%）→ 告警 + 建議 REDUCED
- drift 的分解：成交價差（執行問題）、未成交差（流動性假設問題）、
  部位差（對帳/狀態問題）——三個來源分開報告，指出該修系統的哪一層

## 3. 策略健康度（週頻）

- 實盤累積報酬落在回測 bootstrap 信賴帶（SPEC-004 AC 工具）內外的位置
- 各因子滾動 12 個月 RankIC t-stat；< 1 標記疑似失效（研究閉環回饋）
- 換手率、成本佔比 vs 回測預期的偏離

## 4. 告警（`monitoring/alerts.py`）

- 通道：Telegram bot（第一版），介面預留 email/LINE
- 等級：INFO（日報摘要）/ WARN（閘門 degraded、漂移接近閾值）/
  CRIT（kill-switch 觸發、對帳不符、pipeline 中斷）
- CRIT 必須包含：發生什麼、系統已自動做了什麼、需要人做什麼
- 告警去重：同一 (等級, 事件鍵) 24 小時內只發一次，但 CRIT 每次都發

## 5. 儀表板（React 前端 + 唯讀 JSON API，D-016）

唯讀。前端為 React + Vite + lightweight-charts（`frontend/`，台股紅漲綠跌），
即時 fetch 後端 API（`monitoring/api.py`，FastAPI）；API 與離線快照
（`frontend/scripts/export_snapshot.py`）共用同一組建構函數（`monitoring/snapshot.py`）。
內容：等權市場指數 K 線、個股 K 線（即時選單）、因子健康度（RankIC）、策略回測
（權益曲線＋成本後指標，可選期間）、法人流向、月營收、容量、擷取狀態（涵蓋/缺口/失敗）。
不放任何操作按鈕（操作走 CLI + DECISIONS.md，避免誤觸）。以 `twquant serve` 啟動 API；
docker-compose 的 `api` + `frontend` 兩服務常駐（`frontend` 的 nginx 反代 `/api`）。
（原 Streamlit 單頁 `monitoring/dashboard.py` 已於 D-016 退役。）

個股頁改版（看盤式八面板：法人/融資券/分點/集保/月營收/因子計分卡+歷史）與查詢
效能的 PostgreSQL 供應層另立規格：見 [PRD-001](../PRD-001-stock-page.md) 與
[SPEC-009](SPEC-009-serving.md)。供應層是唯讀原則下的衍生讀取層，不改本節唯讀語義。

## 驗收條件

- AC-1：fixture 注入已知損益 → 日報的帳戶/各 sleeve 損益與手算一致（< 0.01 TWD）
- AC-2：漂移分解三項加總 == 總 drift（< 1e-9）
- AC-3：連續注入超閾值 drift 20 日 → 產生 WARN，且 portfolio_state 收到
  REDUCED 建議旗標（實際降風險由 SPEC-005 執行）
- AC-4：告警去重：同一 WARN 事件 2 次只發 1 次；CRIT 2 次發 2 次（mock 通道驗證）
- AC-5：pipeline 任一步驟 raise → CRIT 告警內含步驟名與例外訊息，且當日
  訊號回退（沿用前日權重）路徑被觸發
- AC-6：API/前端可用 fixture 資料離線啟動（無網路、無券商連線）——API 對 tmp 資料湖
  回傳快照；前端於 API 不可達時以內嵌 data.json fallback 渲染

## 範圍外

即時盤中監控（第一版盤後制）、自動參數再訓練（研究閉環是人/高階模型觸發，
不自動改生產參數）。
