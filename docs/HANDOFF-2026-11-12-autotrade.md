# 交接：獨立程式交易系統 autotrade（2026-11-12）

分支 `claude/eloquent-euler-qsnnu7`。裁決 `DECISIONS.md` D-222；規格與進度 `autotrade/SPEC.md`。
這一輪在雲端做，下一輪使用者要換到自己電腦上的 session 接手。

> **更新（2026-11-12，本機 session）**：下面「下一步第一件事」的真實比對已經跑完，
> 2026-09-09～11-11 共 41 個交易日全部一致，結果記在 `autotrade/SPEC.md` A-004 AC10。
> 逐筆不用重新下載：主 checkout 的 `data/research_ms/taifex_ticks/TMF` 就是微台的完整逐筆。
> 接下來從 A-005 開始。
>
> **更新（2026-11-13，本機 session）**：做完兩項。（一）期貨商對程式交易的六項要求（A-010，裁決 D-223）。
> （二）A-005 把狀態寫給網站：`replay --status-out` 寫狀態檔 → `autotrade/tools/push_status.py` 寫進
> `serving.autotrade_status` → 網站「台指期 → 程式交易」（只讀、只給管理員）。細節與沒驗證的部分在
> `autotrade/SPEC.md` A-005 的完成紀錄。**接下來從 A-006 手機通知開始**；寫雲端資料庫要使用者自己執行。
>
> **更新（2026-11-13 晚，本機 session）**：A-006 手機通知做完（裁決 D-224，使用者選 Telegram）。
> 引擎事件 → `notify`（轉成白話、分一般／注意／要處理三級、同一件事合併）→ `delivery`（背景佇列、照順序重試、
> 沒送出去的留紀錄）→ Telegram。真單模式現在一定要有 `[notify]`。2026-11-13 晚上用當天的模擬單真的送過 4 則到使用者的 Telegram，四則都送達；
> 機器人與 `.env` 的金鑰、收件代號使用者已經自己設好；換電腦或換機器人時照 `autotrade/README.md` 重做。
> 同晚加了第二管道（D-224 修訂）：「要處理」也送網站 App 推播，訂閱先用 `tools/pull_push_subs.py` 拉成本機檔案；使用者 11-13 晚在網站按了 🔔，測試推播已送出（訂閱檔在主 checkout `data/autotrade/push_subscriptions.json`）。
> 細節與還沒有的部分在 `autotrade/SPEC.md` A-006。**接下來是 A-007 獨立看門程式**（D-223：它做完之前不上真單）；
> 看門程式要自己會發通知，可以直接用 `delivery`（不依賴引擎）。
>
> **更新（2026-11-13 晚，本機 session）**：使用者要求「緊急停止要遠端可以操作」→ 裁決 D-225、規格 A-011，做完了。
> 網站只能叫停；交易程式那個程序不連資料庫，由另一支中繼程式（`python -m autotrade relay`）負責，兩邊交換本機檔案。
> 設計經過兩輪獨立審查（先審設計、再審程式）。**現在沒有任何程式在聽**（要等有盤中常駐的執行程式，A-008）；
> A-008 要做的接線清單在 A-011 的「現在的限制」。遠端停止的事件種類是 `control`／`control_link`，已經接進手機通知。

> **更新（2026-11-13 深夜）**：本機後台 A-012（裁決 D-226）做好：`python tools/run.py admin …`，改設定、開演練，只聽 127.0.0.1；雲端網站仍然只能叫停。演練程式 `drill` 與端到端測試 `tools/e2e_remote_stop.py` 在 PR #326。網站 11-13 晚上兩次部署都因為 Cloudflare 自己故障（Workers API 503）失敗，等恢復後重跑 `deploy-web`。

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

A-005 模擬結果寫給網站（2026-11-13 完成）→ A-006 手機通知（2026-11-13 完成，Telegram）→ A-007 獨立看門程式（元大國內期貨沒有停損單，停損靠程式盯價）
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
- 手機通知（A-006）：
  - 會連到 Telegram 的指令（`notify-setup`、`notify-test`、`replay --notify send`）由使用者自己執行；
    AI 要看通知長怎樣用 `replay --notify print`（不連網）。整套測試不連網，任何連線嘗試都會讓測試失敗（`tests/conftest.py`）。
  - 引擎多了新的事件種類時，`test_ac3_notify_knows_every_kind_the_engine_can_log` 會紅：要在 `config.NOTIFY_KINDS`
    與 `notify.py` 補白話範本，不要把引擎的事件文字直接丟給手機。
  - 之後接即時行情的主迴圈（A-008）要在每筆行情後呼叫 `Notifier.poll(now)`，合併的通知才會準時送出。
  - 通知裡叫使用者「在電腦上解除」，但給人用的解除／緊急停止指令還沒有（A-007）。
