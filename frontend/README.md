# twquant 前端儀表板（React + Vite）

台股量化平台的監控儀表板：市場 K 線、因子健檢、策略回測、法人/營收/容量。
交易終端風格（深色 + 等寬數字），**台股慣例紅漲綠跌**。資料為快照（不連即時資料湖）。

技術：React 18 + TypeScript + Vite 5 + Tailwind + 自繪 SVG 圖表（K 線含 hover 十字游標與
全部/120/60 日切換、權益曲線、水平長條）。

## 用 Docker 打包（推薦）

Windows 本機 Node toolchain 常卡（Parcel 路徑 bug、Vite `#module-sync-enabled`）。
用乾淨 Linux 容器一次打包成自包含單檔：

```bash
cd frontend
docker build --target artifact --output "type=local,dest=./dist" .
# 產出 dist/twquant_dashboard.html（自包含，直接用瀏覽器開或部署）
```

`dist/twquant_dashboard.html` 已附一份預建版本。

## 更新資料快照

從 **twquant repo 根**（需 `uv` 環境與資料湖）重新匯出，再重新打包：

```bash
uv run python frontend/scripts/export_snapshot.py   # → frontend/src/data.json
cd frontend && docker build --target artifact --output "type=local,dest=./dist" .
```

快照含：等權市場指數日 OHLC、成交值前 8 檔個股 OHLC、11 因子 RankIC、回測權益曲線與指標、
投信買超、月營收 YoY、容量分析。非有限值（NaN/Inf）會被清成 null、無資料因子（如 52 週高）會被略過。

## 本機開發（若 Node 環境正常）

```bash
cd frontend && npm install && npx vite dev
```
