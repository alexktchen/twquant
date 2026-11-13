# autotrade — 獨立的程式交易系統

跟線上的 twquant 分開的一套程式交易系統（D-222）。**不 import twquant**；只把狀態單向輸出給網站。
規格、模組清單、進度與風控總表都在 [SPEC.md](SPEC.md)。

現況（2026-11-12）：帳務與風控（A-001）、模擬券商＋委託管理＋期交所逐筆重播＋主引擎（A-002）、
事件日誌與當機後還原（A-003）、動能順勢轉接（A-004，真實比對 41 天全部一致）、
期貨商對程式交易的六項要求（A-010：下單頻率、單筆口數、停損、斷線重連、被阻擋時暫停、部位比對與修正）。
**還沒接元大行情與下單。** 接手請先讀 `docs/HANDOFF-2026-11-12-autotrade.md`。

```bash
cd autotrade
python -m ruff check . && python -m ruff format --check . && python -m mypy && python -m pytest
```

設定範例：`configs/tmf_sim.toml`（微台、只做多、最多 1 口）。真單模式沒設每日虧損上限會拒絕啟動。
