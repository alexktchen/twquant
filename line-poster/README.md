# line-poster — LINE 群組每日圖卡（T-423／D-204）

用一支**備用 LINE 帳號**，在一般群組與**社群（OpenChat）**的**記事本**發一篇圖卡貼文，再**分享到聊天**。每天四種：每日名單、族群資金流入、
盤前晨報、盤後快訊（開關與點圖打開哪一頁在 `configs/line_group.yaml`）。登入、記事本發文與分享照
[lt-foods/new_erp](https://github.com/lt-foods/new_erp)（`supabase/functions/_shared/lineNote.ts`、`tools/line-note-scraper`）。

> ⚠️ 走的是 LINE **非官方**客戶端協定（[linejs](https://github.com/evex-dev/linejs)），違反 LINE 使用條款、帳號可能被停權。
> **只能用備用帳號**，不要用本人或公司主帳號。

這個資料夾只負責跟 LINE 講話；畫圖、排隊、存憑證都在 Python（`twquant line-group …`，
`src/twquant/monitoring/line_group.py`、`line_card.py`）。Python 用子程序呼叫 `node src/cli.mjs <login|groups|send>`：
stdin 一份 JSON、stdout 一行一個事件（NDJSON）、QR 與錯誤細節走 stderr。

## 怎麼發出去

1. 排程寫好名單／晨報後，Python 畫一張 PNG 圖卡（深色、同網站配色），存在雲端 `web.line_cards`。
   網站 `/pub/line-card/<32 位亂數>` 把它公開給 LINE 的伺服器抓（`/pub` 不經 Cloudflare Access，網址猜不到）。
2. **記事本＋分享**（`via_note`，`src/note.mjs`）：在記事本發一篇（那張圖轉 JPEG＋摘要＋網址，網址在記事本裡點得開），
   再用 LINE 的「分享貼文」卡片分享到聊天（同手機上按「分享到聊天」）。群組的記事本在群組上；社群的記事本在整個社群上，
   主聊天、子聊天共用一篇。網站標「記事本＋分享」。
   - 不用 linejs 內建的 `timeline.createPost`（網址寫死 LEGY 閘道、`fetch failed`）；自己打記事本 REST，第一次依序試 host × 路徑 × channel，
     **只用唯讀的 list 探路**，打通了才發文、分享（不然探路途中會發出好幾篇）。
   - 分享時被 LINE 叫停：那篇記在 `web.line_posts.note_post_id`，下一輪只分享、不再發一篇。
   - 每篇之間等 `post_gap_seconds`（20 秒）：同帳號連發十幾篇會被擋（`code=701`）。一次最多 `max_posts_per_run` 筆，多的接著送。
3. 記事本不通 → 用 LIFF 分享（`api.line.me/message/v3/share`）發一則 **Flex 訊息**：整則就是那張圖，點圖打開網站（`kinds.*.link`）。社群沒有這招。
4. Flex 也發不出去（LIFF 出問題，沒填自己的 `liff_id` 時幾乎都是）→ **直接發那張圖**（一般圖片訊息，不需要 LIFF），
   後面再發一行「看完整內容：網址」。一般圖片點了只是放大、不會開網站，所以網址另外給。網站標「改發圖片＋網址」。
   圖由 Python 從 `web.line_cards` 讀出放進請求，轉接器不用去抓網站。開關 `configs/line_group.yaml` 的 `fallback_image`。
5. 圖也發不出去 → 改發一則「摘要＋網址」的文字訊息，網站標「改發文字」。

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
6. （要「點圖就開網站」才需要）在 LINE Developers 建一個自己的 LIFF app（LINE Login channel、scope 勾 `chat_message.write`），
   把 LIFF ID 填到 `configs/line_group.yaml` 的 `liff_id`。空著的話用 linejs 內建的那個（別人的 LIFF app，在 GitHub Actions 上拿不到授權），
   這時會改發「圖片＋一行網址」。

## 社群（OpenChat）

1. 用備用帳號的**手機**加入社群（每個社群各自取暱稱）。轉接器**不會自己加入、也不接受社群邀請**——社群多半禁廣告、禁機器人，要不要進去由人決定。
2. 網站「LINE 群組」分頁按「同步群組」：社群的主聊天與子聊天（「社群 › 聊天」）會跟群組列在一起、標「社群」，勾了就發。
   私訊、已刪除／停權、還在等管理員核准、已退出的聊天不列。清單來自加入的社群（`getJoinedSquares`）＋自己的事件流
   （`fetchMyEvents`）＋逐間 `getSquareChat`——**`getJoinedSquareChats` 已被 LINE 下架**（回 `NOT_IMPLEMENTED`，new_erp 實測）。
3. 社群收到的是**社群記事本的一篇＋分享卡片**。記事本不通時改發**那張圖＋一行「看完整內容：網址」**（LIFF 分享不支援社群，沒有 Flex），
   圖走 obs 的 reqseq 上傳（伺服器收到就自己發那則訊息），文字走 `square.sendMessage`；社群的訊息不加密。

⚠️ 社群發太快或被檢舉，LINE 會限制這支帳號使用社群（`code=403 您已被限制使用社群`，轉接器會停 10 分鐘）；管理員也可能把帳號踢出。
公開社群裡有不認識的人，天天發選股名單在台灣可能被認為是未經許可提供投資建議——發到哪些社群自己判斷（D-204 §二之四）。

## 手動發送

網站「LINE 群組」分頁的「手動發送」：選哪一種、哪一天，按「發到 N 個群組」→ GitHub Actions `line-login.yml`
（`task=post`）畫好圖卡、排隊、送出，約 1～2 分鐘後出現在「最近發的圖卡」。同一天同一種同一群組照樣只發一次；
那天還沒有內容（例如名單還沒寫好）就不會發。

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
- 一直「改發圖片＋網址」：圖卡那段（LIFF）不通（`Failed to get LiffToken`）——內建 LIFF app 在無頭環境拿不到授權。
  圖照樣看得到；要點圖就開網站，填自己的 `liff_id`。同一輪第一個群組失敗後，其他群組不再試 LIFF。
- 「改發文字」：連圖都發不出去，錯誤在網站「最近發的圖卡」那列（`last_error`，`圖片也發不出去：…`）。
  `圖片上傳失敗（obs HTTP …）`＝LINE 的圖片伺服器拒收（轉接器擋下來，不會發一張破圖）。
- `E2EE Key has not been saved`：登入建立的端對端加密金鑰沒接到送出那一步。已修（`web.line_account.storage_json` 跨 run 保存整份 session）；
  還遇到就重新登入一次讓它重存。
- GitHub Actions 的機器在美國；new_erp 的經驗是**從國外 IP 上線容易被鎖**。真的被鎖的話，改在台灣的機器跑
  `line-group send`（本機排程或台灣 VM）。
- `LINE_DEVICE` 環境變數可換模擬的裝置（預設 `ANDROIDSECONDARY`）。

## 測試

`npm test`（純函式，不用 `npm install`）；Python 那邊 `uv run pytest tests/test_line_group.py`；
網站那邊 `cd frontend && npm test`（`tests/cloud-line-group.test.ts`）。
