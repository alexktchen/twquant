# CLOUD — 全雲端架構總覽(D-030)

本機關機後 twquant 的完整運行圖。散在 DECISIONS/TASKS/OPS-archive 的雲端資訊統整於此,
**這份是雲端相關的入口文件**;細節仍以各來源為權威(下方標註)。

- 決策依據:[DECISIONS.md](../DECISIONS.md) D-030(架構)、D-031~D-034(待裁決)
- 任務與進度:[TASKS.md](../TASKS.md) T-053~T-063、GitHub Project
  [twquant 雲端搬遷](https://github.com/users/alexktchen/projects/1)
- 歸檔操作:[OPS-archive.md](OPS-archive.md)(dump/還原/rclone 逐步指令)

## 1. 架構:四個雲端服務,零本機常駐

```
GitHub Actions（唯一常駐排程）
  平日 19:05 + 22:05 台北 · .github/workflows/daily-pipeline.yml
  擷取 → 品質閘門 → 券商分點 → 訊號(PG源) → 供應層物化 → 選股池
       → broker_flow 聚合 → 增量歸檔上傳 Drive → 滾動窗口修剪
        │                              │
        ▼                              ▼
Supabase（展示層 DB）            Google Drive（raw 歸檔·保命線）
  serving schema · 446/500 MB      base dump + daily/YYYY/YYYYMMDD/
        │
        ▼
Vercel（同一專案）
  React 靜態前端  +  /api/* → api/index.py（FastAPI serverless function）
```

各元件為何是它:GHA 是唯一免費且能跑 Python 的常駐排程(Supabase 不能跑 Python);
Supabase Free 只有 500 MB 故只放滾動窗口;全史靠 Drive(**券商分點過期不可再取得,
歸檔鏈絕不能斷**);前後端同域部署免去 CORS,前端程式碼零改動。

## 2. 資料分布:哪些在雲端、哪些只在歸檔

| 資料 | Supabase 保留 | 理由 |
|---|---|---|
| `prices_daily` | 260 交易日(四碼普通股) | 硬下限＝`price.high_52w` 回看 260 |
| `broker_trades`(分點明細) | 10 日 | 量最大;250 日折線改讀聚合表 |
| `broker_flow_daily`(日聚合) | 250 日 | T-055 新增,供 P6 折線 |
| `factor_values` | **20 日**(D-034 過渡) | 60 日實測 361 MB 會爆 |
| `composite_daily` | 120 日(D-034 過渡) | 同上 |
| `screener_daily` | 250 日 | |
| 法人/融資券/月營收/集保/stock_meta | **全史** | 小表,合計 < 90 MB |
| 因子表全史、raw 全史 | ❌ 不在雲端 | 需要時還原 Drive dump(見 OPS-archive §6) |

窗口定義在 `configs/serving.yaml` 的 `cloud.windows`,並有靜態測試
(`tests/test_cloud.py`)確保窗口 ≥ 吃該表的因子最大回看。

## 3. 雲端可用範圍(T-059 實測)

以「空資料湖 + Supabase」實測 = 等同雲端:

| 功能 | 狀態 |
|---|---|
| 個股頁全套(K線/法人/融資券/營收/因子/券商/綜合分) | ✅ `source: pg` |
| 選股池 screener(宇宙 641 檔) | ✅ |
| 策略清單與回測 | ✅(範圍受窗口限制,見 D-031) |
| `/api/health`、`meta`、`stocks`、`stock-meta` | ✅ |
| 大盤K線、因子IC、指數、容量面板 | ⚠️ 仍讀資料湖 → 雲端留白(**T-063**) |
| `/api/snapshot`(舊重端點) | ⚠️ 503;前端正常渲染不呼叫它 |

## 4. 秘密與憑證(黃金規則 9:值只在下列位置,永不進版控)

| 名稱 | 存放 | 用途 |
|---|---|---|
| `TWQUANT_SUPABASE_DSN` | 本機 `.env` / GitHub Secrets / Vercel 環境變數 | Supabase transaction pooler(**埠 6543**,密碼含 `@` 須 `%40` 編碼) |
| `RCLONE_CONF` | 本機 `%APPDATA%\rclone\rclone.conf` / GitHub Secrets | Drive 歸檔上傳(scope `drive.file`,最小權限) |

⚠️ rclone 的共用 Google client_id **2026 年內退役**,屆時需自建 client_id 並更新
本機 conf 與 GHA Secret(見 OPS-archive §4)。

## 5. 踩過的雷(重複踩會很痛,務必先讀)

1. **Supabase 超量進唯讀保護**:首灌 725 MB → `ReadOnlySqlTransaction` + `DiskFull`。
   解法:session `SET default_transaction_read_only = 'off'` 後清資料;
   且 **TRUNCATE 必須獨立 commit**,否則會隨後續失敗的 COPY 一起回滾、空間沒釋放。
2. **transaction pooler 不支援 prepared statements**:`store.connect` 一律帶
   `prepare_threshold=None`(對本機 PG 無害)。
3. **Supabase 看不到表**:表在 `serving` schema,Table Editor 預設只顯示 `public`,
   左上角切換 schema 即可。
4. **repo 的 GitHub Actions 預設可能是停用狀態**:workflow 會排隊數小時且 **0 個 job**
   被建立(與分鐘數耗盡的症狀不同——後者會有 job 但無 runner)。
   查:`gh api repos/<owner>/<repo>/actions/permissions`。
5. **Vercel 框架自動偵測**:根 `pyproject.toml` 有 fastapi 依賴時,Vercel 會嘗試把整包
   當 FastAPI app 部署、要求 `[tool.vercel] entrypoint`,與「靜態前端 + function」
   佈局衝突 → `vercel.json` 設 `"framework": null`。
6. **serverless 依賴要從匯入鏈推導,不能憑印象**:`pandera` 看似只在擷取層,實際
   `api.py → warehouse.py → schemas.py` 模組層就 import,漏裝直接
   `FUNCTION_INVOCATION_FAILED`。驗證法:建只裝 `api/requirements.txt` 的乾淨 venv
   跑 `from twquant.monitoring.api import app`。
7. **cron 不準時**:GHA 常態晚發車 3–15 分鐘;pipeline 必須冪等可重跑。
8. **平日國定假日**無行情 → `--fail-on-degraded` 會紅燈(每年約 15–20 次),屬預期噪音。

## 6. 切換到全雲端前的驗收清單(T-062)

- [ ] T-058:GHA 與本機平行跑 ≥5 個交易日,逐日對帳一致
- [ ] Vercel 端點全通(含 T-063 修好後的大盤/因子面板)
- [ ] 歸檔鏈連續無斷日(Drive `daily/` 逐日檔齊全)
- [ ] Supabase 保活:Free 方案**閒置 7 天會暫停**,長假(如春節 9 天)需 no-op job
- [ ] 停本機 compose 後,Docker volume 與 base dump 保留 ≥1 個月

## 7. 常用指令

```bash
# 手動觸發雲端 pipeline（取代後台「一鍵跑今日全部資料」）
gh workflow run daily-pipeline.yml -f date=2026-07-29

# 本機 → 雲端重灌滾動窗口（TRUNCATE 重灌，冪等）
python -m twquant serving seed-cloud

# 雲端窗口修剪（500 MB 防線）
python -m twquant serving prune

# 歸檔（增量必須在 serving sync 之後跑，否則歸檔到空切片）
python -m twquant archive-daily --date 2026-07-29
python -m twquant archive-restore --date 2026-07-29
```
