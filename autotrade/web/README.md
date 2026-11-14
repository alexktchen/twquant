# autotrade/web——程式交易的遙控網站

從 twquant 網站拉出來的獨立網站（A-018／D-230）。只有一頁：程式交易的狀態、遠端緊急停止、遙控按鈕
（啟動／停止、平倉、暫停／恢復自動交易、啟動／結束交易程式）。
「啟動／停止」（A-019）是叫常駐的交易 agent 在待命與運作之間切換，走訊息表 `web.autotrade_messages`（`functions/_lib/autotrade_bus.ts`）。

- 部署在 Cloudflare Pages，專案名 `twquant-autotrade`；靜態頁面＋`functions/` 底下四支 API。
- 資料庫跟 twquant 網站同一個（同一個 Hyperdrive），所以本機的交易程式、中繼程式、後台都不用改。
- **登入**：Cloudflare Access 擋在整個網站最前面，沒登入連頁面都看不到。端點自己還會再驗一次。
- 這裡沒有、也不要加：手動買賣、改設定、解除緊急停止。

## 第一次上線要做的事

合併之後 GitHub Actions（`deploy-autotrade-web`）會自己建立 Pages 專案並部署。
**這時候網站是關著的**（每支 API 回 503「登入還沒設定好」），要做完下面三步才打得開：

1. **建 Access 應用**：Cloudflare 後台 → Zero Trust → Access → Applications → Add an application → Self-hosted。
   網域填部署出來的網址（預設 `twquant-autotrade.pages.dev`；Actions 的執行摘要裡有寫實際的網址）。
   政策選 Allow，條件選 Emails，填你自己的 email。
2. **抄下 AUD**：建好之後，應用的 Overview 頁有一串 Application Audience (AUD) Tag。
3. **把兩個設定給網站**，任選一種做法，做完重新部署一次（Actions 頁手動執行 `deploy-autotrade-web`）：
   - 設成 GitHub 的 repo secrets：`AUTOTRADE_ACCESS_AUD`（上一步那一串）、`AUTOTRADE_ADMINS`（你的 email；多個用逗號分隔）。
   - 或直接寫進 Pages：

     ```bash
     npx wrangler pages secret put ACCESS_AUD --project-name twquant-autotrade
     ```

     ```bash
     npx wrangler pages secret put AUTOTRADE_ADMINS --project-name twquant-autotrade
     ```

`ACCESS_TEAM`（Zero Trust 組織的網址）不是祕密，寫在 `wrangler.toml` 的 `[vars]`。

三個設定少一個，整個網站就關著——不是開著。email 與 AUD 都不寫進程式。

## 開發

```bash
npm install
```

```bash
npm test
```

```bash
npm run build
```

- `npm run dev` 只有頁面、沒有後端（會顯示進不去）；要連真的後端用 `npx wrangler pages dev`，
  並先設環境變數 `CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE`（連線字串，不要寫進檔案）。
- 看版面用 `tests/shot.cjs`（回應全是假的，不碰資料庫；要在裝了 playwright 的地方跑）。
- 資料表的定義、作廢的秒數要跟交易程式那一份一樣，`autotrade/tests/test_a018_web.py` 守著。

## 跟 twquant 網站的關係

`frontend/` 裡原本的「台指期 → 程式交易」分頁這一輪還留著（同樣的頁面、同樣的端點）。
新網站驗過之後再把舊的拿掉；**在那之前改這一頁要兩邊一起改**。
