# autotrade — 獨立的程式交易系統

跟線上的 twquant 分開的一套程式交易系統（D-222）。**不 import twquant**；只把狀態單向輸出給網站。
規格、模組清單、進度與風控總表都在 [SPEC.md](SPEC.md)。

現況（2026-11-12）：只有帳務與風控（A-001）。**還不能下單，也還沒接行情。**

```bash
cd autotrade
python -m ruff check . && python -m ruff format --check . && python -m mypy && python -m pytest
```

設定範例：`configs/tmf_sim.toml`（微台、只做多、最多 1 口）。真單模式沒設每日虧損上限會拒絕啟動。
