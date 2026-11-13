# autotrade — 獨立的程式交易系統

跟線上的 twquant 分開的一套程式交易系統（D-222）。**不 import twquant**；只把狀態單向輸出給網站。
規格、模組清單、進度與風控總表都在 [SPEC.md](SPEC.md)。

現況（2026-11-13）：帳務與風控（A-001）、模擬券商＋委託管理＋期交所逐筆重播＋主引擎（A-002）、
事件日誌與當機後還原（A-003）、動能順勢轉接（A-004，真實比對 41 天全部一致）、
期貨商對程式交易的六項要求（A-010：下單頻率、單筆口數、停損、斷線重連、被阻擋時暫停、部位比對與修正）、
把狀態單向寫給網站（A-005：重播後寫狀態檔 → `tools/push_status.py` 寫進資料庫 → 網站「台指期 → 程式交易」）、
遠端緊急停止（A-011，D-225：網站只能叫停；交易程式只讀寫本機檔案，跟資料庫溝通的是另一支中繼程式 `python -m autotrade relay`）、
成交與異常通知手機（A-006：Telegram 收全部，「要處理」另送網站 App 推播；2026-11-13 兩個管道都真的送到手機過）。
**還沒接元大行情與下單。** 接手請先讀 `docs/HANDOFF-2026-11-12-autotrade.md`。

```bash
cd autotrade
python -m ruff check . && python -m ruff format --check . && python -m mypy && python -m pytest
```

設定範例：`configs/tmf_sim.toml`（微台、只做多、最多 1 口）。真單模式沒設每日虧損上限、沒有通知設定、或沒有 `[control]`（遠端緊急停止），會拒絕啟動。

## 後台（改設定、開演練）

在交易電腦上用瀏覽器操作，只有這台電腦連得到。在 `autotrade` 資料夾執行：

```bash
python tools/run.py admin --config-dir configs --data-dir D:/t/autotrade --ticks-dir D:/project/twquant/data/research_ms/taifex_ticks/TMF --env-file D:/project/twquant/.env
```

會自動打開 `http://127.0.0.1:8765/`。`D:/t/autotrade` 換成任何一個放演練紀錄的資料夾（要先建好）。
「設定」分頁：改了按存檔，不合格的不會存；改了要重新啟動交易程式才生效。模擬／真單不能在這裡切換。
「演練」分頁：選哪一天的價格、從幾點開始、幾倍速、中繼程式連雲端還是本機，按「開始演練」；到網站按「緊急停止」試試看。

## 演練遠端緊急停止

全程模擬、不碰券商。開兩個視窗，都在 `autotrade` 資料夾裡（`D:/t/drill` 換成任何一個空資料夾）：

```bash
python tools/run.py relay --config configs/tmf_sim.toml --dir D:/t/drill --consumer sim --dsn-env TWQUANT_SUPABASE_DSN --env-file D:/project/twquant/.env
```

```bash
python tools/run.py drill --config configs/tmf_sim.toml --ticks D:/project/twquant/data/research_ms/taifex_ticks/TMF/Daily_2026_11_12.csv --day 2026-11-12 --dir D:/t/drill
```

第一個是中繼程式（連雲端資料庫），第二個是演練程式（買 1 口抱著，最多 10 分鐘）。網站「台指期 → 程式交易」的「模擬」那一行會變成「在聽」，
按「緊急停止」之後依序變成「等它收到」→「已收到」→「已停手，沒有部位」；演練程式的視窗也會印出每一步。

自動跑完整條路的端到端測試（只連本機資料庫）：

```bash
python tools/e2e_remote_stop.py --ticks D:/project/twquant/data/research_ms/taifex_ticks/TMF/Daily_2026_11_12.csv --day 2026-11-12
```

## 手機通知（Telegram）

成交與異常會通知手機。第一次要做下面五步，之後不用再碰。

1. 手機裝 Telegram，在裡面搜尋 **@BotFather**（名字旁有藍色勾勾的那個），傳 `/newbot` 給它，
   照它問的取一個顯示名稱，再取一個結尾是 `bot` 的帳號。它會回一串像 `1234567890:AAH…` 的**金鑰**。
2. 打開電腦上的 `D:/project/twquant/.env`，加一行，等號後面貼那串金鑰：`AUTOTRADE_TELEGRAM_BOT_TOKEN=`
3. 回到手機，點 BotFather 訊息裡的連結打開你的機器人，按「開始」。
4. 在電腦執行下面第一個指令。機器人會在 Telegram 裡回你一串數字（你的**收件代號**；不會印在電腦畫面上）。
   把它也加進 `.env`：`AUTOTRADE_TELEGRAM_CHAT_ID=`
5. 執行第二個指令，手機應該收到一則「測試通知」。

```bash
python tools/run.py notify-setup --config configs/tmf_sim.toml --env-file D:/project/twquant/.env
```

```bash
python tools/run.py notify-test --config configs/tmf_sim.toml --env-file D:/project/twquant/.env
```

兩個指令都在 `autotrade` 資料夾裡執行（`tools/run.py` 讓 PowerShell、cmd、bash 都能用同一行，不用先設 `PYTHONPATH`）。
想看「出事時」長什麼樣（會響）：第二個指令後面加 `--level act`。
想看某一天實際會收到哪些通知，不用連網：重播那一天加 `--notify print`（印在電腦畫面上）；
改成 `--notify send` 就真的送到手機，標題會標「重播」。

金鑰等於這個機器人的密碼：只放 `.env`，不要貼到別的地方。萬一外流，回 BotFather 傳 `/revoke` 換一把。

通知分三種：

| 開頭 | 意思 | 例子 |
|---|---|---|
| （沒有記號，靜音送達） | 一般 | 成交、已解除停手、連線恢復、被風控擋下 |
| ⚠️ 注意 | 出了狀況，程式自己處理完了 | 當日虧損到上限而停手、停損平倉、委託太久沒成交而刪單 |
| 🚨 要處理 | 程式停下來等你，或要你去看部位 | 緊急停止、部位跟券商對不上、暫停送單、有部位時斷線 |

同一件事一直發生（例如斷線期間策略每秒都想下單）只會先通知一則，其餘合併成一則、5 分鐘後送。
通知送不出去時交易照常進行，程式會一直重試；最後沒送出去的會留紀錄，下一則送成功的通知也會說前面漏了幾則。
要通知哪些事、要不要靜音、合併的時間，都在 `configs/tmf_sim.toml` 的 `[notify]`。

**限制**：電腦當機、斷網或斷電時，這裡發不出通知——那要靠之後的獨立看門程式（A-007）。

## 第二管道：網站 App 的推播

Telegram 照舊收全部；網站 App（加到主畫面的 twquant）另外只收「🚨 要處理」的通知，點了打開台指期分頁。交易程式執行時不碰網站與資料庫：先把你開了通知的裝置拉成本機檔案，執行時只讀那個檔。

1. 手機打開網站 App，按右上角帳號旁的 🔔，開通知（iPhone 要先用 Safari「分享 → 加入主畫面」，從主畫面打開）。
2. `.env` 要有 `TWQUANT_VAPID_PRIVATE_KEY`（網站推播的金鑰，已經有了）、`TWQUANT_SUPABASE_DSN`（已經有了）、`AUTOTRADE_PUSH_EMAIL`（你網站登入的帳號）。
3. 在 `autotrade` 資料夾執行下面兩個指令：先拉訂閱（只讀資料庫，只印台數），再送一則測試推播。

```bash
python tools/pull_push_subs.py --out D:/project/twquant/data/autotrade/push_subscriptions.json --env-file D:/project/twquant/.env
```

```bash
python tools/run.py notify-test --config configs/tmf_sim.toml --channel push --level act --push-subs D:/project/twquant/data/autotrade/push_subscriptions.json --env-file D:/project/twquant/.env
```

重播時兩個管道一起送：`replay … --notify send --push-subs <同一個檔>`。換手機、重新開通知、或通知說「這台裝置的通知已經失效」時，重新拉一次。要網站也收「⚠️ 注意」，把 `configs/tmf_sim.toml` 的 `[notify_push] min_level` 改成 `warn`。
