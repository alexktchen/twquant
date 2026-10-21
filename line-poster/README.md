# line-poster — LINE 群組每日圖卡（T-423／D-204）

用一支**備用 LINE 帳號**在一般群組的**聊天**發一張圖卡，點圖打開網站。每天四種：每日名單、族群資金流入、
盤前晨報、盤後快訊（開關與點圖打開哪一頁在 `configs/line_group.yaml`）。登入方式照
[lt-foods/new_erp](https://github.com/lt-foods/new_erp) 的 `tools/line-note-scraper`。

> ⚠️ 走的是 LINE **非官方**客戶端協定（[linejs](https://github.com/evex-dev/linejs)），違反 LINE 使用條款、帳號可能被停權。
> **只能用備用帳號**，不要用本人或公司主帳號。

這個資料夾只負責跟 LINE 講話；畫圖、排隊、存憑證都在 Python（`twquant line-group …`，
`src/twquant/monitoring/line_group.py`、`line_card.py`）。Python 用子程序呼叫 `node src/cli.mjs <login|groups|send>`：
stdin 一份 JSON、stdout 一行一個事件（NDJSON）、QR 與錯誤細節走 stderr。

## 怎麼發出去

1. 排程寫好名單／晨報後，Python 畫一張 PNG 圖卡（深色、同網站配色），存在雲端 `web.line_cards`。
   網站 `/pub/line-card/<32 位亂數>` 把它公開給 LINE 的伺服器抓（`/pub` 不經 Cloudflare Access，網址猜不到）。
2. 轉接器用 LIFF 分享（`api.line.me/message/v3/share`，手機上 LIFF「分享給好友」那一條）發一則
   **Flex 訊息**：整則就是那張圖，點圖打開網站（`kinds.*.link`）。
3. Flex 發不出去（LIFF 出問題）時，改發一則「摘要＋網址」的文字訊息，網站「LINE 群組」分頁會標「改發文字」。

## 第一次設定

1. 準備備用 LINE 帳號（手機上能登入）。轉接器模擬「第二台裝置」（`ANDROIDSECONDARY`），手機那邊不會被登出。
2. **從網站登入**：網站「LINE 群組」分頁按「登入」→ 約 1 分鐘後頁面出現 QR 碼 → 用備用帳號的手機 LINE 掃 →
   頁面出現 PIN → 在手機輸入 → 頁面顯示「已登入」。背後是 GitHub Actions `line-login.yml`（等掃描最多 3 分鐘）。
   第一次要先準備一把 GitHub token 讓網站觸發得了它：GitHub → Settings → Developer settings → Fine-grained tokens，
   只選 `alexktchen/twquant`、權限 **Actions: Read and write**；把它加成 repo secret `TWQUANT_DISPATCH_TOKEN`，
   再手動跑一次 Actions 的 `deploy-web`（它會寫進網站的祕密）。
   也可以在本機登入（QR 印在終端機）：
   ```bash
   npm ci --prefix line-poster
   uv run --env-file .env python -m twquant line-group login
   ```
3. 用手機把備用帳號拉進要發的群組，然後在網站「LINE 群組」分頁按「**同步群組**」（約 1 分鐘，順便接受群組邀請；
   登入成功時也會自動同步一次，每一班排程也會）。本機也可以：
   ```bash
   uv run --env-file .env python -m twquant line-group groups
   ```
4. 到網站「**LINE 群組**」分頁勾要發的群組。只有有權限的帳號看得到這個分頁
   （`frontend/functions/_lib/roles.ts` 的 `line_admin`，目前是 cktalex@gmail.com）。新群組預設不發。
5. 先看圖長怎樣（不排隊、不發）：
   ```bash
   uv run --env-file .env python -m twquant line-group enqueue --kind picks --date 2026-10-21 --preview D:/tmp/picks.png
   ```
6. （建議）在 LINE Developers 建一個自己的 LIFF app（LINE Login channel、scope 勾 `chat_message.write`），
   把 LIFF ID 填到 `configs/line_group.yaml` 的 `liff_id`。空著的話用 linejs 內建的那個（別人的 LIFF app）。

## 每天怎麼跑

| 排程 | 做什麼 |
|---|---|
| `daily-pipeline`（17:33 主跑、21:13 補跑） | `groups` → `enqueue --kind picks`、`--kind groups` → `send` |
| `morning-brief`（06:30 晨報、18:00 盤後快訊） | `groups` → `enqueue --kind morning`／`evening` → `send` |

- 同一天同一種同一群組只發一次（補跑、備援班不會重發）。
- LINE 叫我們慢一點（「限制使用」停 10 分鐘、`code=701` 停 2 分鐘）→ 帳號先停，同一步最多等 11 分鐘再試，
  再不行就留給下一班；排太久（`stale_hours`）就作廢，晚到的「今日名單」沒有意義。
- 送到一半程式中斷的那一則標「不確定有沒有發出去」，**不會自動重送**（寧可漏一則也不要同一張圖發兩次）。
- 兩個排程同時跑時用 `web.line_account.busy_until` 輪流，不會一起發、一起換 token。
- 看狀態：網站「LINE 群組」分頁，或 `uv run --env-file .env python -m twquant line-group status`。

## 打不通的時候

- `AUTHENTICATION_FAILED`／`MUST_REFRESH_V3_TOKEN`：憑證失效，本機重跑 `line-group login`。
- 一直「改發文字」：LIFF 那段不通（`Failed to get LiffToken`），填自己的 `liff_id` 試試。
- GitHub Actions 的機器在美國；new_erp 的經驗是**從國外 IP 上線容易被鎖**。真的被鎖的話，改在台灣的機器跑
  `line-group send`（本機排程或台灣 VM）。
- `LINE_DEVICE` 環境變數可換模擬的裝置（預設 `ANDROIDSECONDARY`）。

## 測試

`npm test`（純函式，不用 `npm install`）；Python 那邊 `uv run pytest tests/test_line_group.py`；
網站那邊 `cd frontend && npm test`（`tests/cloud-line-group.test.ts`）。
