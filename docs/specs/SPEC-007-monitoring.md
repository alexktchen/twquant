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

## 6. 策略選股池（`monitoring/screener.py`，T-040）

把主力買賣超研究（2026-07-14，樣本 2022-02~2026-06、隔日開盤進場、對全池超額）中
逐年穩定的高勝率條件常態化為每日選股池，供前端「因子選股」頁追蹤。唯讀、每請求
即時從 PG serving 計算（不快取，與其他重運算端點同語義）；**描述性選股池，非交易
訊號**——正式因子化仍走 T-016/T-036。

- 宇宙：四碼普通股 ∩ 20 日均成交金額 ≥ `liquidity_floor_twd`。
- 指標（僅用 ≤ as_of 資料）：主力 N 日淨買超佔量比（broker_trades 日聚合 ÷ 成交量，
  股）、MA 快/慢線、月營收 YoY（讀 `factor_values.fundamental.revenue_yoy`，
  取 ≤ 當日的最近已物化值前向沿用——物化可落後行情日，月頻慢變數語義、PIT 安全）。
- 池定義的**規則屬程式、閾值屬配置**（`configs/screener.yaml`，黃金規則 8）；
  百分位 = 當日宇宙內 rank(average)/count。
- 每池成員附「連續在池天數」（截至 as_of 的連續成員日數，回看 `streak_days` 窗）。
- 端點：`GET /api/screener?n=&date=`（n＝每池成員數上限）→
  `{as_of, source, snapshot, universe_n, pools:[{key,name,rule,note,total,members:[...]}]}`；
  PG 不可用 → 503。
- 每日快照與回溯（T-041）：CLI `screener-snapshot [--from --to]` 把每日池成員
  存入 `serving.screener_daily`（PK＝date/pool/stock_id；`ON CONFLICT DO NOTHING`
  ＝首錄保留——之後改配置閾值或資料修訂不重寫歷史）。`?date=` 給定時優先讀快照
  （payload 標 `snapshot: true`）、該日無快照則以 ≤ date 資料即時回算
  （`snapshot: false`；非交易日貼齊前一交易日）。寫入只走 CLI，API 維持唯讀。

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
- AC-7（選股池）：非四碼代號與 20 日均額低於配置下限者不入任何池
- AC-8（選股池）：主力佔量比 / MA / 百分位對 fixture 手算一致（1e-9）；
  百分位以當日宇宙 rank(average)/count 計
- AC-9（選股池）：五池歸屬與配置閾值一致；YoY 缺值之個股不入需 YoY 的池
- AC-10（選股池）：連續在池天數 = 截至 as_of 的連續成員日數，中斷歸零重計
- AC-11（選股池）：payload 含 as_of/universe_n/pools，每池成員依主指標遞減、
  受 n 上限截斷且 total 為截斷前總數
- AC-12（快照）：screener_daily 寫入冪等——同區間重跑列數不變、首錄保留
- AC-13（回溯）：快照列重建的 payload 與即時 payload 同形（排序/截斷/欄位一致）；
  非法日期 → 400
- AC-14（快照）：snapshot_frame 產出的某日某池成員集合 == 即時 payload 該池成員集合

## 範圍外

即時盤中監控（第一版盤後制）、自動參數再訓練（研究閉環是人/高階模型觸發，
不自動改生產參數）。
