# ms_replay — Momentum_Scalping 逐筆重播評估器

用錄下來的逐筆成交重播交易日，**直接呼叫 Momentum_Scalping 原本的程式**，量測這套策略扣成本後的期望值。

| 環節 | 呼叫的原程式 | 頻率 |
|---|---|---|
| 大盤許可、類股快照 | `market.attach_sector_contract` / `resolve_strategy_market_context` | 每 30 秒（同 Runtime） |
| 選股 | `scanner.scan_candidates` | 每 30 秒 |
| Momentum / Trend 狀態機 | `intraday.get_signal_context` | 每筆成交 |
| 進場 17 道閘門 + 部位大小 | `trading.evaluate_trading_decision` | 有進場訊號時 |
| 送單前再驗證 | `trading.revalidate_buy_before_order` | 通過閘門時 |
| 出場數量（整張、減碼） | `trading.evaluate_trading_decision`（SELL） | 有出場訊號時 |
| 13:00 時鐘清倉 | 同 `Runtime._run_clock_exits` | — |

本工具只負責餵資料、模擬帳戶與成交、扣成本、做統計。**策略與風控沒有重寫**，所以量到的是那份程式碼本身的行為。

只用 Python 標準函式庫（3.9 以上），Windows / Linux 都能跑。

---

## 快速開始

```bash
# 1. 先用合成資料確認環境可以跑（合成資料的結果沒有任何績效意義）
python make_synthetic.py --out demo --days 2
python ms_replay.py --repo C:\path\to\Momentum_Scalping --data demo\fugle_trades.jsonl --ref demo\ref.csv --out demo_out

# 2. 用真實資料
python ms_replay.py --repo C:\path\to\Momentum_Scalping ^
    --data C:\path\to\api_replay\fugle_trades.jsonl ^
    --ref ref.csv --market-csv market.csv ^
    --params-json C:\path\to\gui_parameters\latest.json ^
    --workers 4 --out result
```

輸出（`--out` 資料夾）：

| 檔案 | 內容 |
|---|---|
| `report.md` | 總結、各進場型態的期望值與 95% 信賴區間、出場原因、訊號漏斗、假設清單 |
| `trades.csv` | 每一筆完整交易：進出時間與價格、成交依據（ASK/BID）、停損距離、毛利／價差／手續費／稅、淨損益、R 倍數、最大有利／不利波動 |
| `signals.csv` | 每個進場訊號：被哪一層、哪個代碼擋下，或何時通過 |
| `summary.json` | 全部統計與實際使用的參數 |

---

## 資料怎麼來

### 逐筆成交（必要）

**方法 A：用原系統自己錄（推薦）**

原系統的 `ReplayStore` 會把每筆 Fugle 成交寫進 `api_replay/fugle_trades.jsonl`，**但在 LIVE 模式下是關閉的**：

```python
# api_gateway_v2_7.py 第 2777 行
self.replay_store = ReplayStore(replay_dir, persist=self.mode != "LIVE")
```

把 `persist=self.mode != "LIVE"` 改成 `persist=True`，LIVE 模式就會開始錄。這是對原系統的修改，請自行評估後再改。

- 錄到的是整個訂閱範圍（約 600 檔）的成交，Scanner 的選股母體會跟實盤接近。
- 檔案很大，600 檔一天約數百 MB，請預留硬碟空間。
- 不需要開 AUTO，MANUAL 模式掛著收資料就行。

**方法 B：其他來源的逐筆 CSV**

欄位如下，標題大小寫不拘：

| 欄位 | 必要 | 說明 |
|---|---|---|
| `symbol` | ✔ | 股票代號，字串，保留前導零 |
| `market` | ✔ | `TWSE` 或 `TPEx` |
| `time` | ✔ | ISO 時間（沒寫時區視為台北時間），或 epoch 秒／毫秒／微秒 |
| `price` | ✔ | 成交價 |
| `size` | ✔ | 單筆成交量；預設單位是**張**，若是股請加 `--size-unit SHARE` |
| `bid`, `ask` |  | 成交當下的最佳買價／賣價。**強烈建議提供**：沒有的話，送單前檢查會因五檔不完整（BOOK_NOT_READY）而拒絕所有買單 |
| `volume` |  | 當日累計量；沒有就用 `size` 累加 |
| `serial` |  | 成交序號；沒有就依時間排序後自動編號 |

### 參考資料 `--ref`（建議）

```csv
date,symbol,prev_close,day_trade_eligible,sector,sector_code,name
2026-10-20,2330,1050,true,半導體業,24,台積電
,2317,,true,其他電子業,,鴻海
```

- `date` 留空表示每天都適用，適合當沖資格、類股這類不常變動的資料。
- `prev_close`：沒給時，用資料裡前一個交易日的最後一筆成交價（約等於收盤價）。**每段連續資料的第一天**一定要給，否則那天算不出漲幅，Scanner 不會選這檔。
- `day_trade_eligible`：沒給時預設可當沖（`--no-assume-day-trade-eligible` 可關閉這個預設）。
- `sector`：**沒有類股的股票，Trend 一律不會進場**，實盤也是如此（`SECTOR_MAPPING_MISSING`）。

### 指數資料 `--index-csv` + `--index-daily-csv`（建議）

給了這兩份資料，大盤狀態就直接交給原系統的 `market.calculate_market_context` 計算，包括指數分數、與日線 MA5 比較的短線方向、急跌保護。市場廣度與類股強度則用資料裡的股票來算。

```csv
# index_intraday.csv
date,time,index,price
2026-10-21,09:01:00,TAIEX,47074.17
2026-10-21,09:01:00,TPEX,397.27
# index_daily.csv（每天的收盤價，用來算昨收和 MA5，至少要有前 5 天）
date,index,close
2026-10-20,TAIEX,46292.93
```

### 沒有逐筆資料時：`fetch_public_tw.py`（1 分 K 近似）

```bash
python fetch_public_tw.py --out public --top 300
python ms_replay.py --repo ... --data public/ticks.csv --ref public/ref.csv \
    --index-csv public/index_intraday.csv --index-daily-csv public/index_daily.csv --workers 4 --out result
python plot_jiangbo.py --run result --public public --out charts   # 把交易畫在江波圖上
```

這支腳本用公開資料組出重播需要的輸入：

- **選股母體**：證交所和櫃買中心的日行情，取前一交易日成交值前 N 名。
- **價格**：Yahoo 的 1 分 K。
- **當沖資格**：證交所的當沖標的表。
- **產業別**：證交所和櫃買中心的 OpenAPI。

每根 1 分 K 會拆成 4 筆近似成交，**分鐘內的高低點先後順序是假設的**。加上 `--rebuild-ticks reversed` 可以用相反的順序重建一份，比較結果差多少。Yahoo 的 1 分 K 只保留最近幾天；上櫃股票查不到公開的當沖名單，一律假設可以當沖。

### 大盤時間序列 `--market-csv`（選配，但影響很大）

```csv
date,time,taiex_change_pct,tpex_change_pct,breadth_net_ratio,market_score,market_state,intraday_trend
2026-10-20,09:00:30,0.12,0.20,0.15,22.5,NORMAL,UP
```

- 依時間取「最近一筆」使用（as-of）。
- `market_state`：BULL / NORMAL / WEAK / PANIC。
- `intraday_trend`：UP / SIDEWAYS / DOWN。
- **沒提供時，大盤濾網等於全開**（NORMAL、短線 UP、指數漲跌 0%），結果會偏樂觀。報告的假設清單會寫出這點。

---

## 常用參數

| 參數 | 預設 | 說明 |
|---|---|---|
| `--params-json` | 程式預設值 | GUI 存的 `gui_parameters/latest.json`。**請用你實盤的參數檔**，因為程式預設值與說明文件不一致（例如 `structure_buffer_pct` 程式是 0.5、文件寫 0.1） |
| `--set module.name=value` | — | 覆寫單一參數，可重複，例如 `--set intraday.momentum_take_profit_pct=1.5` |
| `--latency-ms` | 500 | 從決策到成交的延遲 |
| `--extra-slippage-ticks` | 0 | 成交價再多吃幾個跳動點，用來做壓力測試 |
| `--fee-discount` / `--min-fee` / `--tax-rate` | 0.28 / 20 / 0.0015 | 手續費折數、最低手續費、當沖稅 |
| `--capital` | 1,000,000 | 每天的起始權益；部位大小由原 Trading 模組計算 |
| `--fix-momentum5m` | 關 | 修正 Momentum 出場條件讀不到 `momentum_5m` 的 bug，與原版比較用 |
| `--skip-scanner` | 關 | 跳過 Scanner 的名次與分數門檻，所有股票都當候選；只錄了少數股票時用 |
| `--eval-min-interval-ms` | 0 | 同一檔兩次評估的最短間隔。0 表示每筆成交都評估（最貼近實盤，最慢）；設 250–1000 可加速，但會偏離實盤 |
| `--workers` | 1 | 同時處理幾個交易日 |

**速度**：原系統每評估一次約 2 毫秒（每次都會重算當日 K 棒）。30 檔候選 × 每檔約 1 萬筆成交，一天約 10 分鐘（單核）。多天資料請加 `--workers`。

---

## 建議的評估流程

1. **先定好及格標準再跑**。例如：「至少 200 筆交易，且每筆淨報酬 95% 信賴區間下界 > 0」。看到結果後才改標準，等於沒有標準。
2. **資料至少錄 20–40 個交易日**，而且要包含弱勢日、急跌日。只有多頭日的資料，大盤濾網就沒被測到。
3. **調參與驗證分開**：用前一半日期調參數，後一半日期只跑一次，用來驗證。
4. 依序跑這幾種情境：
   - 原始程式 + 你的實盤參數（基準）
   - `--fix-momentum5m`（看修 bug 的影響）
   - `--latency-ms 1000 --extra-slippage-ticks 1`（執行比較差時）
   - `--fee-discount 0.6`（手續費折扣比較少時）
5. **看「依進場型態」那張表**，不要只看總數。通常只有一兩種型態有優勢，其他型態會把它拖垮。

## 解讀時要記得的限制

- **成交是理想化的**：假設最佳一檔吃得下，沒有排隊，也沒有部分成交。1–3 張的小部位比較合理，部位越大越樂觀。
- **五檔時間**：用成交事件附帶的 bid/ask 當最佳一檔，所以送單前的五檔新鮮度（3 秒）一定會通過；實盤可能會被擋。
- **類股強度與 Scanner 排名只用資料裡有的股票計算**。實盤是 500 檔裡挑前 30，錄得越完整越接近。
- **量比**：原 Runtime 沒有提供 `volume_ratio`，這裡同樣不提供，與實盤一致。
- **每天從 `--capital` 重新開始**，不複利。
