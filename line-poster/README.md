# line-poster — LINE 群組記事本發文（T-423／D-204）

照 [lt-foods/new_erp](https://github.com/lt-foods/new_erp) 的做法（`tools/line-note-scraper`、Edge Function
`line-note-worker`）：用一支**備用 LINE 帳號**在一般群組的**記事本**發文，再把貼文卡片**分享到群組聊天**。
每天四種：每日名單、族群資金流入、盤前晨報、盤後快訊（內容與開關在 `configs/line_group.yaml`）。

> ⚠️ 走的是 LINE **非官方**客戶端協定（[linejs](https://github.com/evex-dev/linejs)），違反 LINE 使用條款、帳號可能被停權。
> **只能用備用帳號**，不要用本人或公司主帳號。

這個資料夾只負責跟 LINE 講話；排隊、組文字、存憑證都在 Python（`twquant line-group …`，
`src/twquant/monitoring/line_group.py`）。Python 用子程序呼叫 `node src/cli.mjs <login|groups|send>`：
stdin 一份 JSON、stdout 一行一個事件（NDJSON）、QR 與錯誤細節走 stderr。

## 第一次設定（本機做一次）

1. 準備備用 LINE 帳號（手機上能登入）。轉接器模擬「第二台裝置」（`ANDROIDSECONDARY`），手機那邊不會被登出。
2. 裝套件、掃 QR 登入（憑證存到雲端 `web.line_account`，需要 `.env` 裡的 `TWQUANT_SUPABASE_DSN`）：
   ```bash
   cd line-poster && npm ci && cd ..
   uv run --env-file .env python -m twquant line-group login
   ```
   用備用帳號的手機掃終端機上的 QR，再在手機輸入畫面上的 PIN。
3. 用手機把備用帳號拉進要發的群組，然後列群組（順便接受邀請），抄下 `c…` 開頭的 id：
   ```bash
   uv run --env-file .env python -m twquant line-group groups
   ```
4. 在 GitHub repo 的 Settings → Secrets → Actions 新增 `TWQUANT_LINE_GROUP_MIDS`＝那個 id（多個群組用逗號隔開）。
   本機試發要的話 `.env` 也加一行同名的。**沒設這個 secret 時排程整段略過**，所以這一步就是開關。
5. 試發：
   ```bash
   uv run --env-file .env python -m twquant line-group enqueue --kind picks --date 2026-10-21 --dry-run  # 只看文字
   uv run --env-file .env python -m twquant line-group enqueue --kind picks --date 2026-10-21            # 排隊
   uv run --env-file .env python -m twquant line-group send                                             # 送出
   uv run --env-file .env python -m twquant line-group status                                           # 看結果
   ```

## 每天怎麼跑

| 排程 | 排隊 | 送出 |
|---|---|---|
| `daily-pipeline`（17:33 主跑、21:13 補跑） | `enqueue --kind picks`、`--kind groups` | `send` |
| `morning-brief`（06:30 晨報、18:00 盤後快訊） | `enqueue --kind morning`／`evening` | `send` |

- 同一天同一種同一群組只排一次（21:13 補跑、備援班不會重發）。
- 送出前先看記事本上有沒有同內文的貼文（上一輪發了但沒記到），有就直接認，不發第二篇。
- LINE 叫我們慢一點（「限制使用」停 10 分鐘、`code=701` 停 2 分鐘）→ 帳號先停，同一步最多等 11 分鐘再試，
  再不行就留給下一班；排太久（`stale_hours`）就作廢，晚到的「今日名單」沒有意義。
- 兩個排程同時跑時用 `web.line_account.busy_until` 輪流，不會一起發、一起換 token。

## 打不通的時候

- `AUTHENTICATION_FAILED`／`MUST_REFRESH_V3_TOKEN`：憑證失效，本機重跑 `line-group login`。
- GitHub Actions 的機器在美國；new_erp 的經驗是**從國外 IP 上線容易被鎖**。真的被鎖的話，改在台灣的機器跑
  `line-group send`（本機排程或台灣 VM），或把 `send` 搬去 Supabase Edge Function（new_erp 正式版的做法）。
- 記事本 API 沒有 wire trace：程式依序試 host × 路徑前綴 × channel，第一組回 `code: 0` 的就記住。
  全部失敗時錯誤訊息會列出每次嘗試；也可以用環境變數硬指定：

| 變數 | 說明 |
|---|---|
| `LINE_DEVICE` | 模擬的裝置，預設 `ANDROIDSECONDARY`（`DESKTOPWIN` 拿不到 channel token） |
| `LINE_NOTE_HOST` | 記事本 host，候選：登入用的 endpoint、`gw.line.naver.jp`、`ga2.line.naver.jp` |
| `LINE_NOTE_PREFIX` | 路徑前綴：群組 `/mh` 或 `/ext/note/nt` |
| `LINE_NOTE_CHANNEL` | channel id：HOME `1341209850`、TIMELINE `1341209950`、NOTE `1655599932` |

## 測試

`npm test`（純函式，不用 `npm install`）；Python 那邊 `uv run pytest tests/test_line_group.py`。
