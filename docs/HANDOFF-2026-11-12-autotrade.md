# 交接：獨立程式交易系統 autotrade（2026-11-12）

分支 `claude/eloquent-euler-qsnnu7`。裁決 `DECISIONS.md` D-222；規格與進度 `autotrade/SPEC.md`。
這一輪在雲端做，下一輪使用者要換到自己電腦上的 session 接手。

> **更新（2026-11-12，本機 session）**：下面「下一步第一件事」的真實比對已經跑完，
> 2026-09-09～11-11 共 41 個交易日全部一致，結果記在 `autotrade/SPEC.md` A-004 AC10。
> 逐筆不用重新下載：主 checkout 的 `data/research_ms/taifex_ticks/TMF` 就是微台的完整逐筆。
> 接下來從 A-005 開始。

## 使用者決定了什麼

- 把**動能順勢**（網友原版 Momentum_Scalping，只做多）接上元大真單：微台 1 口、全自動、行情與下單都走元大。
- 要一套**獨立且完整**的程式交易系統，放 `autotrade/`，不 import twquant；模擬結果之後單向寫給網站。
- 要有風控（清單在 D-222 與 `autotrade/SPEC.md` §5）。
- **還沒決定**：每日虧損上限金額（真單模式沒設會拒絕啟動）；元大的實際手續費（設定檔先用每口 15 元，同現有模擬單）。

## 做完的（autotrade 閘門 49 條全過）

| 編號 | 內容 |
|---|---|
| A-001 | 帳務、風控 |
| A-002 | 模擬券商、委託管理、期交所逐筆重播、主引擎、指令列 |
| A-003 | 事件日誌（雜湊鏈）、當機後還原 |
| A-004 | 動能順勢轉接 `strategy/momentum.py`、比對工具 `tools/parity_momentum.py`——**程式寫完，真實比對還沒跑** |

## 下一步第一件事：在使用者電腦上跑真實比對（A-004 的最後一條 AC）

雲端不允許執行外部 repo 的程式，所以這裡只用假訊號（`tests/fake_original.py`）驗證過
「轉接＋模擬券商」跟對照引擎（`reference/day_engine.py`，現有模擬單跑的那份 DayEngine）五天逐筆相同。
用**真的**網友原版比對要在有 `C:/t/Momentum_Scalping`（commit 1dea51b）的電腦上跑：

```bash
cd autotrade
python tools/parity_momentum.py --ms-repo C:/t/Momentum_Scalping --config configs/tmf_sim.toml --ticks <Daily_2026_11_12.csv> --day 2026-11-12
```

- `--ticks` 要期交所「每日逐筆成交」的**完整** csv（含 TMF）。本機 `data/research_ms/taifex_30d/csv` 只留了小台的列，不能用；
  從 `https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_YYYY_MM_DD.zip` 重新下載解壓。
- 可以一次給多組 `--ticks … --day …`。期望每天印「一致」、結束碼 0。至少跑有成交的 5 天。
- 不一致時先看印出來的「autotrade 風控事件」：最可能是 autotrade 的風控（進場時段、進場次數上限、委託逾時）擋了對照引擎沒擋的單。
  那是設定要對齊，不是改對照引擎。`reference/day_engine.py` 不可以改。
- 比對通過後把天數與結果寫進 `autotrade/SPEC.md` A-004，再往下做。

## 之後的順序

A-005 模擬結果寫給網站 → A-006 手機通知 → A-007 獨立看門程式（元大國內期貨沒有停損單，停損靠程式盯價）
→ A-008 元大行情（卡在使用者請營業員加開「行情API」；元件是 32 位元）→ A-009 元大下單（卡在測試主機連不到）。
元大的連線測試（T-468）與兩份官方文件的重點在 `research/yuanta/README.md`。

## 要注意的

- **`autotrade/` 有自己的閘門**，twquant 的 `python -m twquant check` 不檢查它：
  `cd autotrade && python -m ruff check . && python -m ruff format --check . && python -m mypy && python -m pytest`
  （雲端的 mypy 找不到 pytest，用 `uvx --with pytest mypy`）。
- 停手時會**平倉**（刪掉沒成交的新倉單、市價平掉部位），不只是不再進場。這是 AI 選的做法，使用者還沒表示意見。
- 期交所逐筆檔的成交數量是「買＋賣」（實際口數的兩倍），重播時照原值餵給策略，跟現有模擬單一致。
- 重播用的 `--journal` 請給絕對路徑。
- 測試環境沒通之前不寫對正式帳戶送單的程式；登入與啟動都由使用者自己做。
