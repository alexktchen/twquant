# SPEC-006 — 執行層

範圍：券商轉接器協定、下單流程、盤前檢查、對帳。
唯一可以對真實世界發送指令的模組。安全性優先於一切效能考量。

## 1. Broker 協定

```python
class Broker(Protocol):
    name: str
    def connect(self) -> None: ...
    def place(self, order: Order) -> OrderAck: ...
    def cancel(self, order_id: str) -> None: ...
    def positions(self) -> list[Position]: ...
    def cash(self) -> float: ...
    def fills(self, date: dt.date) -> list[Fill]: ...

@dataclass(frozen=True)
class Order:
    stock_id: str
    side: Literal["buy", "sell"]
    shares: int                 # 1000 的倍數（第一版）
    order_type: Literal["limit"]
    limit_price: float
    tif: Literal["ROD"]
    idempotency_key: str        # date+stock+side+seq 的 hash，重送防重複
```

實作：
- `PaperBroker`：成交模擬直接 import 回測引擎的成交模組（同一套程式碼，
  這是漂移偵測有意義的前提）
- `ShioajiBroker`：Shioaji API；dry_run 模式（log 不送單）是預設，
  實際送單需要 `configs/system.yaml` 的 `execution.live: true` 加上
  環境變數 `TWQUANT_LIVE=1` 兩道開關同時打開

## 2. 每日執行流程（`execution/daily.py`）

1. 盤前（08:30）：
   - 券商 positions/cash vs 系統帳本一致性檢查（誤差 > 1 股或 > 100 TWD →
     拒絕執行，告警）
   - 當日處置股清單再過濾一次（防止訊號日之後新公告）
   - TargetBook → 差額 → 訂單清單（帶 idempotency_key）
2. 執行（09:00–13:00）：
   - 分批：每檔股票拆 N 批（configs 定，預設 3 批：開盤、10:30、12:30），
     限價 = 送單時刻參考價 ± protection_bps
   - 漲跌停處理：參考價已達漲停的買單直接放棄（記 unfilled），不掛漲停價追
3. 收盤後（14:00）：
   - fills 對帳：券商回報 vs 送單清單逐筆核對；有系統不知道的成交 → 凍結
     次日下單 + 告警
   - 帳本更新落地 `data/signal/ledger_live`

## 3. 安全規則

- 單日總下單金額上限（configs/risk.yaml `max_daily_order_twd`），超過拒送
- 同一 idempotency_key 重送 → Broker 層直接回既有 ack，不重複下單
- 任何 API 例外 → 該檔股票放棄本批次，記錄，不 retry 送單類操作
  （查詢類可 retry）
- kill-switch 狀態 HALTED → place() 直接 raise ExecutionError

## 驗收條件

- AC-1：PaperBroker 對 golden fixture 的成交結果 == 回測引擎對同日同單的
  結果（bit-identical；這條測試同時鎖住兩邊不分家）
- AC-2：idempotency：同一 Order 對 PaperBroker place() 兩次，只產生一筆成交
- AC-3：盤前檢查注入 1 股差異 → 整日執行中止，無任何 place() 呼叫
  （mock broker 驗證呼叫次數 = 0）
- AC-4：dry_run 預設：不設環境變數時，ShioajiBroker.place() 只寫 log 不呼叫
  API（mock 驗證）；兩道開關少任何一道都不送單
- AC-5：對帳發現多出的成交 → 次日下單凍結旗標寫入 portfolio_state
- AC-6：HALTED 狀態下 place() raise ExecutionError

## 範圍外

零股交易、信用交易、期貨下單、智慧路由/演算法單（TWAP 以外）。
