# SPEC-016 — 投信／ETF 資金流軌跡（研究 PRD）

狀態：2026-10-11 提案；**D-171 待裁決（門檻由使用者填）**。本文件是給實作模型的規格書：
資料、事件定義、量測方法、任務與驗收全部寫死；**通過門檻不由 AI 代填**（D-134 改制）。
所有數字都是**定義**（事件怎麼算）而非**門檻**（結果算不算好）；兩者分開放。

依賴與邊界：
- D-163 只留兩條線（大盤精選 `CORE`＝`lw_e0_ctrl`、潛伏飆股 `QUIET`＝`quiet_s0`）。本線**不開新的選股架構**，
  產出只能是這兩條線的（1）排除濾網、（2）每日看板上的監控旗標。
- D-141 八輪結論：不在開發期資料上再對 E0 加工。本線加的是**新資料**（ETF 申贖、持股、換股公告），不是舊特徵再組合。
- D-109 第二保留期（2026-09-09 起）**一律不碰**；所有軌跡只算到 2026-08-31。
- D-134 改制：部署／採用門檻由使用者訂；統計量照報、不當通過條件。
- 資料盤點前置：[docs/reports/event-signal-data-inventory-2026-09-28.md](../reports/event-signal-data-inventory-2026-09-28.md)。

## 0. 給實作者的一頁（先讀這裡）

1. 讀 `WHERE-IS-EVERYTHING.md` → `CLAUDE.md` §1 黃金規則 → `SPEC-000` → 本文件你要做的那個任務的章節。別全讀。
2. 任務依 `TASKS.md` T-334 → T-341 的順序做，一次一個；每個任務先寫 `test_acN_*` 測試、確認紅、再實作。
3. 參數一律放 `configs/fund_flow.yaml`（T-335 建立 pydantic 模型 `FundFlowConfig`，`extra="forbid"`）。`.py` 裡不得出現本文件 §4 的任何數字。
4. 因子與事件只能透過 `PitView` 讀資料（黃金規則 1）；每個新因子的測試檔必須呼叫 `assert_no_lookahead`。
5. 可用時點：`available_at` 由資料層決定（§2.4）。21:00 以後才公布的表（借券、融資）照 `chip.py` 的 `_to_next_session` 前移到下一個交易日。
6. 歷史不足時 **raise**，不准補 0、不准靜默跳過（黃金規則 5）。ETF 持股沒有歷史的日期，因子必須明確回報 `coverage_start`，呼叫端決定要不要用。
7. 任何會寫檔的指令給絕對路徑：`--out D:/project/twquant/data/...`（CLAUDE.md §0.2）。
8. 閘門：`PYTEST_ADDOPTS="--basetemp=C:/t/pt" TWQUANT_PG_DSN=... uv run --all-groups python -m twquant check`。紅就回去改程式，不改測試。
9. 研究腳本（T-339）跑完**只產出報告與數字**；不得在腳本裡寫「通過／不通過」字樣，判讀由使用者依 D-171 填好的門檻做。
10. 看到 §6 的空格就停：那是使用者要填的，不是你要填的。

## 1. 問題與假說

投信買賣超（`institutional_daily.trust_net`）混了兩種性質相反的交易（REVIEW.md 第 9 條）：
ETF 申贖與指數換股產生的**機械性**買賣，以及基金經理人的**裁量**買賣。文獻對兩者的預期相反：

| 來源 | 多方軌跡 | 空方軌跡 | 文獻 |
|---|---|---|---|
| ETF 機械流量 | 納入公告→生效日被推高；**之後回吐** | 剔除公告→生效日被壓低；**之後回補** | Chang, Hong & Liskovich (2015)；Coval & Stafford (2007)；Lou (2012) |
| ETF 申贖 | 申購潮推高，流量停止後反轉 | 贖回潮壓低，之後回補 | Lou (2012)；Brown, Davies & Ringgenberg (2021) |
| 投信裁量 | 連買起點後有資訊（可跟） | 連賣／連買終止**證據弱** | Chen, Jegadeesh & Wermers (2000)；Hung, Lu & Lee (2010) |
| 投信季底 | 季底前五日拉抬 | 季初鬆手 | Carhart et al. (2002) |

本線要回答的只有一題：**把 trust_net 拆成機械與裁量兩部分後，上表八條軌跡在台股有沒有形狀、扣成本後有沒有可用的段落。**
不推論基金與任何人的意圖；「配合出貨」不是本線能證明或否定的命題，本線只量「誰的流量在前、誰的在後、之後價格怎麼走」。

## 2. 資料

### 2.1 已在 PG、可直接用

| 表 | 用途 | 注意 |
|---|---|---|
| `institutional_daily` | `trust_net`（股）、`foreign_net` | 18:00 可用 |
| `prices_daily`、`corporate_events` | 還原價、ADV、成交金額 | ETF 代號也在 `prices_daily`（T-141） |
| `sbl_short_daily` | `sbl_sell` 每日**流量**（股） | 21:00 → 以 T-1 進訊號 |
| `margin_daily` | 融資餘額變動 | 21:00 → T-1 |
| `attention_stocks`、`disposition_stocks` | 事件窗內排除／分層 | 公告日 18:00 |
| `index_daily` | 大盤狀態分層 | — |
| `shareholding_dispersion` | 千張大戶 | **只有近 51 週**，第一版不用（盤點報告三） |

### 2.2 新表（T-335～T-337 建立；`schemas.py` 加 pandera 定義，直寫 PG，沿 T-309 `direct_ingest` 模式）

`etf_meta`（靜態，人工核對後進版控 `configs/etf_meta.yaml`，載入時寫 PG）

| 欄 | 型別 | 說明 |
|---|---|---|
| etf_id | str | 代號，保留前導零（"0050"、"00981A"） |
| name | str | |
| issuer | str | 投信簡稱 |
| index_id | str, nullable | 追蹤指數代號；主動式為 null |
| is_active | bool | 主動式 ETF |
| listed_date | date | |

`etf_units_daily`（每日申贖）

| 欄 | 型別 | 說明 |
|---|---|---|
| date | date | 基準交易日 |
| etf_id | str | |
| units_outstanding | int64 | 已發行受益權單位數 |
| units_delta | int64 | 與前一交易日差（可負；**由來源欄位直接取，不自算**；來源沒有就 null） |
| nav | float64 | 每單位淨值 TWD |
| available_at | TW_DT | §2.4 |

`etf_holdings_daily`（每日持股，來源 PCF）

| 欄 | 型別 | 說明 |
|---|---|---|
| date | date | 持股所屬交易日（PCF 的「基準日」，不是公布日） |
| etf_id | str | |
| stock_id | str | |
| shares | int64 | 股 |
| weight | float64 | 權重（小數） |
| available_at | TW_DT | §2.4 |

`index_rebalance_events`（指數換股）

| 欄 | 型別 | 說明 |
|---|---|---|
| announce_date | date | 公告日 |
| effective_date | date | 生效日（收盤後換股的那一天） |
| index_id | str | |
| stock_id | str | |
| action | str | `add` / `remove` |
| source_url | str | 公告來源 |
| available_at | TW_DT | announce_date 18:00 |

### 2.3 來源（T-334 唯讀盤點先確認，報告寫進 `docs/reports/fund-flow-data-inventory-<日期>.md`）

| 資料 | 候選來源 | 盤點要回答 |
|---|---|---|
| 申贖單位數、淨值 | 證交所「ETF 申贖資訊及即時淨值揭露專區」：各投信提供固定 URL 的 JSON（含已發行單位數、與前日差異、預估淨值） | 只有當日還是有歷史？各投信 URL 清單在哪？格式是否一致？ |
| 持股（PCF） | 各投信官網的申購買回清單頁；主動式 ETF 每日公布持股 | 歷史保留幾天？能否逐日抓到基準日？ |
| 換股公告 | 臺灣指數公司、FTSE Russell（0050／0056 系列）、MSCI（00878）公告頁 | 歷史能回抓到哪一年？公告日與生效日是否都有？ |
| ETF 清單 | 證交所／櫃買 ETF 商品清單 | 台股成分 ETF 有幾檔、規模 |

盤點的結論要分成兩種：**有歷史**（可回測）與**只有當日**（從現在開始累積，只能做監控）。這決定 §4 哪些事件能跑。

### 2.4 可用時點（`available_at`）與防前視

- `etf_units_daily`：揭露專區當日盤後公布 → `date` 18:00。盤點若發現是盤中即時值，一律取**收盤後最後一筆**並記 18:00；不得用盤中值。
- `etf_holdings_daily`：PCF 通常在基準日**前一晚或當天開盤前**公布「當日適用」的清單，內容是**前一交易日**持股。
  故 `date`＝持股所屬交易日，`available_at`＝公布時點；盤點未確認前一律保守記 `date` 的**下一個交易日 18:00**。
- `index_rebalance_events`：`announce_date` 18:00。公告在盤中發布的也記 18:00（保守）。
- 三張表與日頻表 join 一律 backward asof on `available_at`（SPEC-000）。
- 每個事件的「事件日 T」＝訊號截點 20:00 當天能看到該事件的第一個交易日；進場一律 T+1 開盤。

## 3. 衍生量與因子（T-338；只經 `PitView`）

記號：`u_e(t)`＝ETF e 在 t 的 `units_delta`；`nav_e(t)`；`w_{e,i}(t−1)`＝e 在 t 的前一個持股基準日對股票 i 的權重；
`close_i(t)`；`ADV_i(t)`＝SPEC-003 的 20 日成交金額中位數（TWD）。

| 量 | 定義 | 單位 |
|---|---|---|
| `etf_flow_twd_i(t)` | Σ_e u_e(t) × nav_e(t) × w_{e,i}(t−1) | TWD；機械性需求 |
| `etf_flow_shares_i(t)` | `etf_flow_twd_i(t)` / `close_i(t)` | 股 |
| `trust_disc_i(t)` | `trust_net_i(t)` − `etf_flow_shares_i(t)` | 股；裁量近似 |

因子（名稱依 SPEC-000；窗口、分母從 `configs/fund_flow.yaml` 注入）：

| 因子 | 定義 | direction |
|---|---|---|
| `chip.etf_flow_pressure_5d` | Σ_{5 日} `etf_flow_twd` / ADV | 先驗 −1（壓力越大之後回吐）；只是標記 |
| `chip.trust_disc_net_10d` | Σ_{10 日} `trust_disc` / Σ_{10 日} 成交股數 | +1 |
| `chip.trust_disc_streak` | 對 `trust_disc > 0` 做 `trust_streak` 同樣的連買計數 | +1 |
| `market.etf_flow_total_5d` | 全部台股成分 ETF 五日 Σ u×nav / 全市場五日成交金額 | 大盤層級，分層用 |

規則：
- 任一日 ETF 持股或申贖**缺整欄** → 因子回傳該日 null 並在結果附 `coverage_start`；呼叫端（事件腳本、overlay）看到 null 必須 raise 或明確排除並計數，不得當 0。
- `trust_disc` 在沒有 PCF 的歷史期間**等於** `trust_net`；因子必須另輸出 `is_decomposed`（bool）欄，研究報告分開統計兩段。
- 跨缺日不用列位移；先對齊交易日曆（SPEC-015 §4 同規則）。

## 4. 事件定義（寫死；參數值進 `configs/fund_flow.yaml`）

每個事件是一列 `(event_type, T, stock_id)`。T 是事件可被看見的交易日（§2.4）。同一股同一事件型態 20 個交易日內只取第一次。

| 代號 | 名稱 | 定義 | 需要的表 |
|---|---|---|---|
| E1 | 納入公告 | `index_rebalance_events.action = add`，T＝announce_date；另記 `effective_date` 供分段 | rebalance |
| E2 | 剔除公告 | 同上，`action = remove` | rebalance |
| E3 | 申購潮 | `chip.etf_flow_pressure_5d` 在 T 的橫斷面 ≥ 第 95 百分位，且 > 0 | units + holdings |
| E4 | 贖回潮 | 同上 ≤ 第 5 百分位，且 < 0 | units + holdings |
| E5 | 裁量連買起點 | `chip.trust_disc_streak` 在 T 恰好 = 3，且 T−3 前連續 ≥ 5 日非買超 | institutional（＋PCF 若有） |
| E6 | 裁量連賣起點 | 對 `trust_disc < 0` 的連賣計數在 T 恰好 = 3，前 ≥ 5 日非賣超 | 同上 |
| E7 | 季底拉抬 | T 在每季最後 5 個交易日內，且 `chip.trust_disc_net_10d` 橫斷面 ≥ 第 90 百分位 | 同上 |
| E8 | ETF 接貨 | E3 成立 **且** `sbl_sell` 五日合計 / ADV（股）橫斷面 ≥ 第 80 百分位 **且** 融資餘額五日增量 / 流通股數 ≥ 第 80 百分位 | units + holdings + sbl + margin |

百分位門檻（95／5／90／80）、連買天數（3）、前置空窗（5）、季底日數（5）、去重窗（20）全部是**事件定義**，
登記於 D-171 §一，**不得依結果調整**；想換定義就是新 trial、原結果保留。

沒有 PCF 歷史時：E3／E4／E8 只能從累積起點開始算，報告標「監控段」；E1／E2／E5／E6／E7 用全史跑，`is_decomposed=false` 段另標。

## 5. 軌跡量測（寫死）

- 宇宙：上市上櫃普通股（`stock_meta`），`min_price` 與 ADV 門檻沿 `lw_e0_ctrl.yaml`（`min_adv_pctl 0.70`、`min_price 10`）；
  另列「全市場」與「A20 安靜池」兩個事前分層，三個都報，不得事後挑。
- 窗口：T−20 … T+60 個交易日；報酬以還原價、T+1 開盤為基準價（同 SPEC-015 §2）；T 之前的段落用收盤。
- 對照：**同日**、同 `price.vol_20d` 十分位、同 `price.mom_60d` 十分位、同 ADV 十分位的非事件股，取中位數路徑；
  配對數 < 5 的事件丟棄並計數回報（沿 `scripts/d143_matched_lift.py` 的配對基準做法）。
- 報告（每個事件型態 × 每個分層）：
  - 事件數、股票數、逐年事件數、兩市場占比、丟棄數與原因。
  - +1／+5／+10／+20／+40／+60 的平均與中位數超額累積報酬（對配對組）、命中率（超額 > 0 的比例）。
  - E1／E2 另分「公告→生效」與「生效→+60」兩段。
  - **每持有日**超額報酬（D-153 §六：不得用持有天數灌水）。
  - 扣成本版本：進場 T+1 開盤、出場 T+k 開盤，成本沿 `configs/costs.yaml`（不另寫費率）。
  - 月區塊 bootstrap 的 t 值與 95% 信賴區間（seed 42、次數在 config），**照報、不當通過條件**。
  - 分段：`is_decomposed` true／false；2017-04-28 當沖降稅前後（REVIEW.md 第 5 條）。
- 期間：全部 ≤ 2026-08-31。腳本開頭硬性 assert 最晚日期；越界 raise。
- 登記：每次執行寫 `research_log`，`trial_scope = "flow:traces"`，`params_hash` 含事件定義與量測參數全體。
- 輸出：`docs/reports/fund-flow-traces-<日期>.md`（表格與逐年數字）＋ `data/reports/fund_flow_traces/*.json`（原始數字）。

## 6. 採用門檻（使用者填；AI 不代填）

依 D-134 改制：門檻由使用者於**看到任何結果之前**填入 D-171 §二。下列只是要填的格子，空著就不得判讀。

| 用途 | 要填的量 | 值 |
|---|---|---|
| 排除濾網（空方） | 哪些事件型態、哪一段窗口、每持有日超額至少多負、至少幾個年份同號 | ＿＿ |
| 進場參考（多方） | 同上，方向相反 | ＿＿ |
| 監控旗標 | 不需門檻；只要資料每日可得 | — |

## 7. 若通過：兩種用途（T-340、T-341）

**排除濾網**：新增 overlay `flow_exclude`（`strategy/overlays.py`，簽名同既有 overlay），在 selection 後把命中使用者指定事件型態的股票權重設 0，
**不**重新分配給其他股（曝險下降是這個濾網的代價，要看得到）。配置：`lw_e0_ctrl_xflow.yaml`、`quiet_s0_xflow.yaml` 與原檔逐字相同只加 overlay；
`trial_scope = "flow:exclusion"`。比較看組合 Calmar 與 MDD（不看單獨 IC、不比 0050）。原檔指紋不動（`holdout2.yaml` 的測試守著）。
要不要登記進第二保留期由使用者另裁。

**監控旗標**：`pick_board.json` 每檔持股與觀察名單加 `flow_flags`：`etf_flow_pressure_5d`、`trust_disc_streak`、是否在 E1～E8 任一事件窗內、
主動式 ETF 持股變化（只有 2025-05 起的資料，頁面標「監控、未回測」）。HTML 版沿 T-329 的欄位加法。

## 8. 任務與驗收

| 任務 | 級 | 內容 | AC（測試名含 `test_acN_`） |
|---|---|---|---|
| T-334 | C | 唯讀盤點：§2.3 四種來源；輸出報告；**不寫 PG** | AC-1：報告對每個來源回答「有歷史／只有當日」、欄位、更新時點、URL；AC-2：腳本不含任何寫入呼叫（IO 邊界測試涵蓋 `scripts/probe/`） |
| T-335 | B | `configs/fund_flow.yaml` ＋ `FundFlowConfig`；`etf_meta`、`etf_units_daily` schema、擷取與直寫 PG；接進每日 pipeline | AC-3：未知欄位／負百分位／窗口 ≤ 0 載入即失敗並指出欄位；AC-4：fixture 解析逐欄比對（股、TWD、available_at 18:00、tz-aware）；AC-5：重跑同日冪等，列數不變；AC-6：來源缺當日 → raise，不寫部分列 |
| T-336 | B | `etf_holdings_daily` 擷取與直寫 PG（每家投信一個 parser，fixture 錄自真實頁面） | AC-7：`date` 為持股基準日而非公布日，`available_at` 依 §2.4；AC-8：權重和在 [0.98, 1.02] 之外 → raise；AC-9：同 AC-5／AC-6 |
| T-337 | B | `index_rebalance_events` 歷史回抓＋每日擷取 | AC-10：公告日 ≤ 生效日、action 只有 add/remove、同鍵不重複；AC-11：fixture 含至少一次 0050 與一次高股息 ETF 換股 |
| T-338 | B | §3 四個因子 | AC-12：每個因子的 `assert_no_lookahead`；AC-13：手算三檔兩日的 `etf_flow_twd` 與 `trust_disc`；AC-14：缺 PCF 的日期回傳 null 且 `is_decomposed=false`，不得為 0；AC-15：同輸入重跑 bit-identical |
| T-339 | A | §4 事件與 §5 軌跡腳本 `scripts/d171_fund_flow_traces.py` | AC-16：事件去重與前置空窗手算案例；AC-17：配對數 < 5 的事件被丟棄且計數；AC-18：最晚日期 > 2026-08-31 即 raise；AC-19：報告含 §5 全部欄位，JSON 可重算 markdown；AC-20：research_log 多一筆 `flow:traces` |
| T-340 | B | `flow_exclude` overlay ＋ 兩支 `_xflow.yaml` | AC-21：命中股權重為 0 且其他股權重不變；AC-22：原檔指紋測試仍綠；AC-23：事件型態清單從配置來，空清單 → raise |
| T-341 | B | 看板 `flow_flags`（JSON＋HTML） | AC-24：缺資料日旗標為 null 並顯示「無資料」，不顯示 0；AC-25：主動式 ETF 欄位標「監控、未回測」 |

T-334 完成前 T-335～T-337 的來源 URL 不得寫死；T-339 在 D-171 §二 空著時可以跑、可以出報告，但報告不得含判讀字句。
A 級任務（T-339）依 MODEL-POLICY 拆 `T-339-t`（先凍結測試）。

## 9. 文獻

- Coval & Stafford (2007), Asset fire sales (and purchases) in equity markets, JFE：基金流量驅動的買賣壓力與反轉。
- Lou (2012), A flow-based explanation for return predictability, RFS：資金流可預測報酬與之後反轉。
- Chang, Hong & Liskovich (2015), Regression discontinuity and the price effects of stock market indexing, RFS：指數納入／剔除的價格效應。
- Brown, Davies & Ringgenberg (2021), ETF arbitrage, non-fundamental demand, and return predictability, Review of Finance：ETF 流量是非基本面需求。
- Chen, Jegadeesh & Wermers (2000), The value of active mutual fund management, JFQA：基金買進有資訊、賣出沒有。
- Carhart, Kaniel, Musto & Reed (2002), Leaning for the tape, JF：季底拉抬。
- Hung, Lu & Lee (2010), Mutual fund herding and its impact on stock returns: Evidence from Taiwan；Lien et al. (2020)（REVIEW.md 第 9 條引用）。
- 配對基準做法：`scripts/d143_matched_lift.py`；事件資料盤點：`docs/reports/event-signal-data-inventory-2026-09-28.md`。
