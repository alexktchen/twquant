# twquant 前端儀表板（React + Vite）

台股量化平台的**唯一監控儀表板**：市場 K 線、因子健檢、策略回測、法人/營收/容量。
交易終端風格（深色 + 等寬數字），**台股慣例紅漲綠跌**。（原 Streamlit 儀表板已於
D-016 退役，改由本前端 + 唯讀 FastAPI 取代。）

**資料來源：即時 API（優先）+ 內嵌快照（fallback）。** 前端啟動即 fetch 後端
`/api`（`twquant serve`，見 repo 根 `monitoring/api.py`）取即時資料；API 不可達時
自動退回內嵌的 `src/data.json` 快照渲染（離線單檔仍可用，但個股 150 檔選單、擷取
失敗明細、回測期間重跑等即時功能停用）。API 位址預設同源（nginx/vite 反代 `/api`），
可用 `VITE_API_BASE` 覆寫。

技術：React 19 + TypeScript + Vite 5 + Tailwind + **TradingView lightweight-charts**
（K 線 + 成交量 + 十字游標 legend + 拖曳縮放、權益曲線區域圖）；因子/法人/營收的
水平長條為自繪 SVG（非時間序列）。

## 本機開發（即時）

```bash
# 1) 起後端 API（repo 根，需 uv 環境與資料湖）
uv run twquant serve                       # http://127.0.0.1:8000/api
# 2) 起前端 dev（另一個終端；vite 反代 /api → :8000，同源免 CORS）
cd frontend && npm install && npx vite dev # http://localhost:5173
# 後端在別的位址：VITE_API_TARGET=http://host:port npx vite dev
```

## Docker（推薦；一鍵起 API + 前端）

```bash
docker compose up -d api frontend
# React 儀表板 http://localhost:8899（nginx 反代 /api → api:8000）
# JSON API   http://localhost:8000/api（/api/docs 有互動文件）
```

## 打包離線單檔（fallback bundle）

自包含單檔 `dist/twquant_dashboard.html`：優先仍 fetch `/api`，API 不可達時用內嵌
的 data.json 快照渲染，故可離線直接用瀏覽器開。Windows 本機 Node toolchain 常卡
（Parcel 路徑 bug、Vite `#module-sync-enabled`），用乾淨 Linux 容器打包：

```bash
cd frontend
docker build --target artifact --output "type=local,dest=./dist" .
# 產出 dist/twquant_dashboard.html（已附一份預建版本）
```

## 更新內嵌快照（fallback 用）

fallback 的 data.json 從 **twquant repo 根**（需 `uv` 環境與資料湖）重新匯出，再重新打包：

```bash
uv run python frontend/scripts/export_snapshot.py   # → frontend/src/data.json
cd frontend && docker build --target artifact --output "type=local,dest=./dist" .
```

快照含：等權市場指數日 OHLC、成交值前 8 檔個股 OHLC、11 因子 RankIC、回測權益曲線與指標、
投信買超、月營收 YoY、容量分析。非有限值（NaN/Inf）會被清成 null、無資料因子（如 52 週高）會被略過。
