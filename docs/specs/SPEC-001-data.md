# SPEC-001 — 資料層

範圍：來源轉接器、原始落地、清洗管線、倉儲與 point-in-time 視圖、品質閘門、回補。

## 1. 資料表清單（clean 層）

| 表名 | 主鍵 | 頻率 | 來源 | available_at 規則 |
|---|---|---|---|---|
| prices_daily | date, stock_id | 日 | TWSE rwd / TPEX / twstock | 當日 18:00 |
| institutional_daily | date, stock_id | 日 | TWSE T86 / TPEX | 當日 18:00 |
| inst_holding | date, stock_id | 日 | 自 T86 累計估算 / FinMind | 當日 18:00 |
| margin_daily | date, stock_id | 日 | TWSE MI_MARGN / TPEX | 當日 21:00 |
| broker_trades | date, stock_id, broker_id | 日 | FinMind（付費） | 次日 08:00（保守） |
| shareholding_dispersion | date, stock_id, level | 週 | TDCC opendata | 公布日（週六）12:00 |
| monthly_revenue | year_month, stock_id | 月 | MOPS | 實際公布日 22:00；未知時取「次月 10 日後第一個營業日 22:00」 |
| corporate_events | date, stock_id, event_type | 不定 | TWSE/TPEX 公告 | 公告日 18:00 |
| disposition_stocks | date, stock_id | 日 | TWSE/TPEX 處置公告 | 公告日 18:00，**生效區間另有欄位** |
| taifex_institutional | date, product, side | 日 | TAIFEX | 當日 15:30 |
| stock_meta | stock_id | 緩變 | TWSE/TPEX isin 清單 | 快照制，含上市日、下市日、產業、market |
| trading_calendar | date | 年 | 由 prices_daily 推導+人工核對 | — |
| ingest_log | source, table, date, attempt_ts | 每次抓取 | 系統自產 | — |

- 欄位定義的唯一真相來源是 `src/twquant/data/schemas.py`（T-004 建立）。
- `prices_daily` **必含** `limit_up_price`、`limit_down_price`（來源提供則直接用，
  否則清洗層依 §3 檔位表精算填入），供回測漲跌停判定（SPEC-004 §3，REVIEW M21/H1）。
- `inst_holding`（REVIEW H2）：投信/外資累計持股張數與佔流通股本比。台股無官方
  投信持股比日頻源；以 T86 淨買超自基準日累計估算為第一版（欄位標
  `estimated=True`），或接 FinMind 對應資料集；`available_at` 同 institutional_daily。
  STRATEGY Sleeve A/D 的「投信持股比」讀此表。
- `monthly_revenue` 的 available_at fallback 改為「次月 10 日後第一個營業日 22:00」
  （申報期限遇假日順延、且常有逾期公布；用固定次月 10 日會提早 1–3 天看到營收，
  對 P0 月營收動能因子造成前視污染，REVIEW M25/L1）；估算列標 `available_at_estimated=True`。
- 結構斷點清單（供所有回測引用，REVIEW gaps 5/6/7）：2015-06-01 漲跌幅 7%→10%、
  2017-04-28 當沖證交稅減半、2020-03-23 逐筆交易、2020-10-26 盤中零股、
  2018 股利稅改。存 `data/clean/structural_breaks`。

## 2. 來源轉接器

```python
class Source(Protocol):
    name: str                       # "twse_rwd", "tpex", "tdcc", "mops", "taifex", "finmind"
    tables: list[str]
    def fetch(self, table: str, date: dt.date) -> pl.DataFrame:
        """抓一天/一期的原始資料。回傳原始欄名，不做轉換。失敗 raise DataError。"""
```

- 共用基礎設施（`data/http.py`）：session、UA、限流器（token bucket，
  TWSE 5 秒 3 次；TPEX 帶瀏覽器 headers；各來源速率在 `configs/system.yaml`）、
  重試（3 次指數退避）、原始 response 落地到 raw 層
- TPEX 有 anti-bot：requests 需完整 headers + cookie session；若持續 403/redirect，
  raise DataError 並在 note 標記 `anti_bot_suspected`，禁止自動升級成無頭瀏覽器
  （是否引入 Playwright 由 DECISIONS.md 裁決）
- FinMind：`data/sources/finmind.py`，token 從 env；免費/付費層級錯誤要能區分
  （HTTP 400 + level 訊息 → raise DataError("finmind_level_insufficient")）

## 3. 清洗管線

每張表一支 `data/pipelines/<table>.py`，職責：raw → clean。
規則：單位統一（張→股）、欄名統一、型別統一、民國年→西元、全形→半角、
數字欄位的 "--"/"" → null（不是 0）、加 `available_at` 與 `schema_version`。

## 4. 倉儲與 PIT 視圖

```python
class Warehouse:
    def __init__(self, data_dir: Path): ...
    def view(self, as_of: dt.date | dt.datetime) -> PitView: ...
    def write(self, layer: str, table: str, df: pl.DataFrame) -> None: ...
    def latest_date(self, table: str) -> dt.date | None: ...

class PitView:
    def table(self, name: str) -> pl.LazyFrame:
        """已過濾 available_at <= as_of。as_of 為 date 時 coerce 到當日 20:00
        （訊號生成截點，D-008），不是 23:59。"""
    def prices(self, adjusted: bool = True) -> pl.LazyFrame:
        """還原價：用 corporate_events 的除權息因子即時計算（不落地 adj 表）。"""
```

- **訊號生成 PIT 截點（D-008，關鍵）**：date 型 as_of 一律 coerce 到當日 20:00，
  與生產軌同一 cut-off。故當日 21:00 才可得的 margin_daily、22:00 的 monthly_revenue
  在 `view(as_of=當日)` 看不到 → 這兩類資料以 T-1 進當日訊號。這消除回測（原本
  取 23:59 看得到）與生產（18:00–20:00 看不到）的系統性資料集差異。
- **還原價錨定規則（REVIEW H23）**：因子計算一律用「as_of = 該因子值 date 當日」
  的還原價。批次/向量化模式必須逐日換錨，或因子被證明為比率型（錨不變）並在
  註冊表標 `anchor_invariant=True` 才准走向量化捷徑。feature 層 metadata 記錄
  錨定規則版本。這是 SPEC-002 AC-6（增量 vs 批次 bit-identical）能成立的前提。
- 還原價演算法：後復權。除權息事件類型須涵蓋現金股利、盈餘/公積配股、現金
  增資除權、現金減資、彌補虧損減資（各有不同參考價公式，REVIEW M7），
  corporate_events.event_type 逐類定義。
- `write` 前自動跑 pandera 驗證，失敗 raise QualityGateError。寫入協定見 §7。

## 5. 品質閘門（每日 pipeline 的第 2 步）

檢查項（初始集，配置於 `configs/system.yaml`）。**逐表檢查時點 = 該表預期
可得時點之後**（REVIEW H3/H18）——margin_daily 21:00 前不檢查其列數，否則
每天誤觸發 degraded：
1. 每張日頻表當日列數 ≥ 過去 20 日中位數的 80%（在該表 available_at 之後才查）
2. prices_daily 與 institutional_daily 的 stock_id 交集覆蓋率 ≥ 99%
3. 三大法人：全市場合計買超 vs TWSE 公布市場總表誤差 ≤ 0.5%
4. 價格跳動檢查：以「當日漲跌幅相對除權息/減資參考價」判定，|實際/參考 − 1|
   > 該股當日漲跌停幅 → 標記異常。除權息/減資日先用 corporate_events 調整
   prev_close 再比對；恢復交易與 IPO 前 5 日（無漲跌幅限制）豁免（REVIEW M6）
5. broker_trades：單股買賣總量 vs prices_daily 成交量比值 ∈ [0, 2.2]
6. 磁碟：data 目錄所在磁碟剩餘空間 ≥ configs 閾值（如 50GB），低於發 WARN、
   更低發 CRIT 並停止 ingest（REVIEW M33）

## 6. 回補（backfill）

`python -m twquant ingest --table X --from 2015-01-01 --to 2026-07-01`
- 按日期正序；斷點續傳的「可跳過」條件 = ingest_log 中該 (source, table, date)
  最新 attempt 的 `stage=clean_validated, status=success`（不是 raw 落地成功，
  否則 clean 失敗會被永久跳過，REVIEW M27）
- 寫入順序固定：raw 落地 fsync 完成後才記 log
- `--force`：來源事後更正資料時，產生新 raw 版本並重建 clean
- 低頻表以期別（週五清單／year_month 清單）迭代，非日曆日
- `--dry-run` 顯示將抓的日期數與預估時間（依限流速率）
- backfill 與每日 ingest 對同一來源禁止並行（backfill 啟動時檢查鎖，見 §7）

## 7. 寫入協定（D-011，單機 Windows 正確性的核心）

1. **原子寫入**：一律先寫 `*.tmp` 再 rename 成最終檔名；讀取端 glob 排除 tmp。
   重建分區採「寫新目錄 → 切換 manifest 指標檔」而非原地刪改。中途崩潰不留
   半寫檔汙染 DuckDB 掃描（REVIEW H22/M31）。
2. **raw 版本化與選版（REVIEW H19）**：raw 檔名含單調遞增鍵
   `fetched_at=YYYYMMDDTHHMMSS_seq.parquet`。任何對 raw 的讀取（含 clean 重建）
   先按 (source, table, date) 取 max 版本；**DuckDB 視圖不得直接 glob raw 全目錄**。
3. **log 類 append-only 分片（REVIEW H21）**：research_log、llm_log、ingest_log
   每次 append 產生新小檔到各自目錄，讀取時 union all。**禁止「重寫整個 log
   parquet」**（parquet 無原地 append，重寫會在並行時遺失更新，直接弱化 deflated
   Sharpe 的 trials 計數）。research_log 的 schema 見 SPEC-004 §5。
4. **clean/feature/signal 冪等（REVIEW M31）**：write 為「分區級替換」——同一
   (table, date) 重複 write，讀取列數與內容等於最後一次寫入，不得累加。
5. **schema 演進（REVIEW M32）**：新增欄位必須 nullable + 預設 null；所有跨分區
   讀取一律 `union_by_name=true`（實作在倉儲讀取層，不留給呼叫端）；schema_version
   存表級 manifest + 每檔 parquet metadata，清洗管線按 raw 記錄的來源格式版本分派解析器。
6. **並行與檔鎖（REVIEW H22）**：儀表板（SPEC-007）讀 pipeline 產出的每日快照
   副本或用 DuckDB 唯讀連線 + 重試，不長期持有生產分區 handle；跨行程互斥用
   檔案鎖（限流器、backfill、每日 ingest 對同一來源不得並行）。
7. **限流器語意（REVIEW M24）**：定義為「任意 5 秒滑動窗 ≤ 3 次請求」（不是標準
   token bucket——後者冷啟動 burst 可在 5 秒窗內達 6 次而觸發封鎖）；限流器狀態
   跨行程共享或明文禁止並行對同一來源請求。

## 驗收條件

- AC-1（PIT 核心）：對 monthly_revenue，`view(as_of=公布日-1).table()` 看不到
  該月資料，`view(as_of=公布日)` 看得到。用 fixture 驗證
- AC-2（raw 不可變）：對同一 (source, table, date) 重複 ingest，raw 層產生新
  版本檔而非覆寫；clean 層為最新版本
- AC-3（單位）：fixture 中 TWSE 原始「張」數據清洗後為股數（×1000），
  schema 驗證通過
- AC-4（限流）：mock clock 下，任取 5 秒滑動窗，TWSE 來源請求數 ≤ 3
- AC-5（品質閘門）：注入「列數腰斬」的 fixture 日，閘門 raise QualityGateError
  且訊息含表名與實際/期望列數
- AC-6（還原價）：fixture 含一次除息事件，還原價在事件前日的 adj close 
  等於 raw close × 還原因子，誤差 < 1e-9；as_of 在事件前時 adj == raw
- AC-7（斷點續傳）：中斷後重跑 backfill，clean 未驗證成功的日期會重抓（以 http
  fixture 呼叫計數驗證）；clean 已驗證成功的不重抓
- AC-8（隔日可得）：broker_trades 的 available_at 為次日 08:00，
  `view(as_of=當日)` 看不到當日分點資料
- AC-9（訊號截點，D-008）：margin_daily（available_at 21:00）在 `view(as_of=當日)`
  （coerce 到 20:00）看不到當日資料，`view(as_of=次日)` 看得到；回測與生產對同一
  as_of 的 PitView 可見列集合 bit-identical
- AC-10（raw 選版，H19）：同 (source, table, date) 有兩個 raw 版本檔時，clean
  重建只含最新版本的列且無重複
- AC-11（log 並行，H21）：兩行程各 append N 筆 research_log 後，總筆數 = 2N
- AC-12（原子寫，H22/M31）：模擬 write 中途 kill 行程後，倉儲讀取不含殘檔且
  可自動復原；同一 (table, date) write 兩次後讀取內容 == 最後一次

## 範圍外

即時行情串流、tick 資料、bsr.twse.com.tw 驗證碼站點的自動化抓取（明確禁止）。
