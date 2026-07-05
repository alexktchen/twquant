# DECISIONS — 決策紀錄

規格模糊、文件矛盾、測試基準疑義，都在這裡記錄與裁決。
格式：D-編號、日期、狀態（待裁決/已裁決）、問題、選項、裁決與理由。

## 待裁決

（無）

## 已裁決

### D-001（2026-07-04，已裁決）分點資料來源
- 問題：官方 bsr/TPEX 分點查詢有驗證碼與 anti-bot，如何取得 broker_trades？
- 選項：A. FinMind 付費 API；B. 驗證碼自動破解；C. 半自動人工輸入
- 裁決：A。B 明確禁止（法律與穩定性風險）；C 只作為個別股票補資料的手段。

### D-002（2026-07-04，已裁決）回測與模擬倉的成交假設
- 問題：PaperBroker 是否獨立實作成交邏輯？
- 裁決：不獨立。PaperBroker 直接 import 回測引擎的成交模組（SPEC-006 AC-1
  鎖定），否則「實盤 vs 回測」漂移偵測會混入兩套假設的差異，失去意義。

### D-003（2026-07-04，已裁決）第一版範圍
- 裁決：現股多頭、日頻、盤後制。不做：做空、信用、當沖、日內、零股。
  介面（權重正負、Order.side）預留擴充。

---

以下（D-004~D-011）為 2026-07-05 規格對抗審查後的裁決，逐項回應
[docs/research/REVIEW.md](docs/research/REVIEW.md)。

### D-004（已裁決）波動目標的唯一歸屬（REVIEW H5/M3）
- 問題：STRATEGY §2 Overlay-2（條件式）與 SPEC-005 vol_target（無條件）語義矛盾，
  且 T-017 與 T-020 會各實作一次造成雙重縮放，兩邊測試各自通過、check 攔不住。
- 裁決：波動縮放**只存在於 SPEC-005 投組層（T-020）**，採**條件式**版本
  （僅波動落在 252 日 >90 百分位才降曝險）。T-017 overlay 不做波動縮放。
  新增跨模組 AC：全 pipeline 波動縮放係數只被套用一次（golden 端到端驗證）。

### D-005（已裁決）參與率分母的唯一定義（REVIEW H12）
- 裁決：回測與實盤都用「過去 20 交易日成交金額中位數（ADV）」作參與率分母；
  回測另以當日實際成交金額做事後上限截斷並記入 unfilled。ADV 統一為 SPEC-003
  的「20 日成交金額中位數」，LOOP/SPEC-004/STRATEGY 全引用此定義。

### D-006（已裁決）IC 門檻兩階段（REVIEW M2/M15）
- 裁決：單因子初篩 RankIC t-stat ≥ 2 進候選；正式上線 ≥ 3。兩數字放
  configs（`ic_tstat_screen`、`ic_tstat_promote`），LOOP §1 與 STRATEGY §1-8
  引用同一鍵，不再各寫一個值。

### D-007（已裁決）文件優先權順序（REVIEW H11）
- 裁決：衝突時權威順序 `data/schemas.py > 模組 SPEC > ARCHITECTURE（示意）
  > 其他`。ARCHITECTURE §3 簽名視為示意，以各 SPEC 為準。寫入 SPEC-000。

### D-008（已裁決）訊號生成 PIT 截點（REVIEW H3/H6/H17/H18/M25/M30）
- 問題：回測 view(as_of=date) 取 23:59、生產 18:00 產訊號，兩軌可見資料集不同
  （融資 21:00、月營收 22:00、TDCC 週六），是內建漂移來源，反前視測試不會攔。
- 裁決：定義訊號生成截點。每交易日訊號的 as_of = 當日 20:00（含 18:00 盤後，
  **不含** 21:00 融資 → 融資以 T-1 進當日訊號）。回測 date 型 as_of 一律 coerce
  到 20:00 而非 23:59。資料擷取改多波次（LOOP §3）。新增 AC：回測與生產對
  同一 as_of 的 PitView 可見列集合 bit-identical。

### D-009（已裁決）kill-switch 四態狀態機（REVIEW H10/H34）
- 裁決：擴為 `NORMAL → NO_NEW_ENTRY → REDUCED(50%) → HALTED`。單日虧損 >3%
  → NO_NEW_ENTRY（持倉不動、禁止任一權重上升）；回撤 >15% → REDUCED；
  回撤 >20% 或品質閘門連 3 日不過 → HALTED。回撤高點基準 = 上線以來權益高
  水位。HALTED 僅人工恢復。SPEC-005 §2 逐條列觸發器→狀態→恢復→持久化＋AC。

### D-010（已裁決）portfolio_state 移出可重建層（REVIEW H20）
- 裁決：狀態與凍結旗標移出 `data/signal/`，改放新增的 `data/state/` 專層
  （SPEC-000 標記不可重建、須備份）。單一 state manager 序列化＋原子寫；
  狀態檔缺失/損毀 → fail-safe HALTED＋CRIT，禁止靜默回 NORMAL。

### D-011（已裁決）資料湖寫入協定（REVIEW H19/H21/H22/M31/M32/M33）
- 裁決：新增 SPEC-001 §7「寫入協定」，統一規範原子寫入（temp+rename）、raw
  版本化與選版、log 類 append-only 分片、跨分區 union_by_name、Windows 檔鎖、
  schema 演進、磁碟治理。

### D-015（2026-07-05，已裁決）大量歷史回補走 FinMind，官方端點供每日擷取
- 發現：一年 TWSE 回補跑到約第 130 天（累積約 390 請求）後，TWSE rwd 端點開始
  回空白/HTML（IP 暫時封鎖），即使限流在 3 req/5s 內——TWSE 對「持續大量」請求
  有超越單純 rate 的封鎖機制。TPEX（不同基礎設施）與 MOPS 未被擋。
- 裁決：（1）**每日擷取**（scheduler，每日約 4 請求）用免費官方端點，不會觸發封鎖；
  （2）**大量歷史回補**改走 FinMind（付費，設計給批量歷史，無封鎖風險，且統一
  TWSE+TPEX+分點）——與 D-014 一致；（3）免費端點的 backfill 保留作為補洞用，但
  需分小塊、加長間隔，且預期 TWSE 會被暫時擋。
- 現況：TPEX 上櫃 243 交易日（完整年）、TWSE 上市 142 日（2025-07~2026-01-02）、
  月營收 12 月完整。TWSE 缺口待 ban 解除後補跑，或改由 FinMind 一次補齊。

### D-014（2026-07-05，已裁決）TPEX 上櫃資料來源分工
- 問題：TPEX 有 anti-bot；法人/融資券的歷史端點難尋（OpenAPI 僅回最新日，
  帶 date 的 afterTrading 端點名不易找）。
- 裁決：T-008 聚焦 **TPEX 行情**（afterTrading/dailyQuotes，帶瀏覽器 headers，
  支援歷史日期、附次日漲跌停價），與 TWSE 上市共同寫入 prices_daily（按來源
  分檔 part-<source>.parquet 共存）。TPEX **法人/融資券的歷史資料改由 FinMind**
  （T-010，統一 TWSE+TPEX）提供，避免與 TPEX latest-only OpenAPI 的日期不符前視。
- 倉儲改動：Warehouse.write 加 source 參數，同表同分區各來源寫各自 part 檔、
  掃描時全讀，解決多來源覆蓋問題。

### D-013（2026-07-05，已裁決）Docker infra 與資料庫選型
- 問題：infra 服務容器化；資料庫要 DuckDB/Parquet 還是 DB 伺服器？
- 裁決：**維持 DuckDB + Parquet**（嵌入式、單機、零維運，符合原架構），資料湖
  掛在 Docker volume。docker-compose 三服務共用同一映像：app（執行 CLI）、
  scheduler（每日排程跑 ingest→pipeline）、dashboard（Streamlit，SPEC-007）。
  不引入 Postgres/Timescale（多人/服務化才考慮，屆時走 DECISIONS）。
- scheduler 與 dashboard 為 v1 骨架：scheduler 目前跑 `twquant daily`（單日 ingest，
  容忍非交易日），dashboard 讀倉儲顯示資料狀態與法人流向；完整版分別在 T-023、T-024。

### D-012（已裁決）會計恆等式修正（REVIEW H7/M5）
- 問題：SPEC-004 AC-6「現金＋持倉市值−累計成本 == 權益」重複扣成本（成本成交
  時已從現金扣除），照字面寫會把錯誤會計凍進 golden。
- 裁決：AC-6 改為「權益(t)=現金(t)+持倉市值(t)；且 權益(t)−權益(t-1)==當日持倉
  損益−當日成本（誤差<1e-6）」。equity curve 定義為含成本淨值、成本於成交時
  自現金扣除。
