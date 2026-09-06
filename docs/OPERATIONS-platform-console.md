# 研究平台操作手冊（T-215）

對應 [SPEC-014](specs/SPEC-014-platform-console.md) 與 [PRD-003](PRD-003-platform-console.md)。
這份文件只講**怎麼跑起來、怎麼餵資料、壞掉時怎麼分辨**；設計理由看 SPEC，裁決看 DECISIONS。

## 1. 平台由哪些東西組成

| 元件 | 是什麼 | 怎麼起 |
|---|---|---|
| API | FastAPI，唯讀 JSON（唯一寫入端點是既有的一鍵補抓） | `twquant serve --host 0.0.0.0 --port 8000` |
| 前端 | React + Vite 單檔打包，nginx 靜態伺服並反代 `/api` | `docker compose up -d frontend` |
| 資料 | `configs/system.yaml` 的 `data_dir` | 見 §2 的**絕對路徑**警告 |
| 供應層 | PostgreSQL `serving` schema | `docker compose up -d pg` |

平台頁面全部走 hash 深連結（`#/stocks/0050?date=…`），不需要改反向代理。

## 2. ⚠ 兩個 `data/`

`configs/system.yaml` 的 `data_dir: data` 是**相對路徑**。在 worktree 執行時它指向 worktree 自己的
`data/`，不是主 checkout 的正本（T-084）。**任何會寫檔的指令都明確給絕對路徑**：

```bash
uv run --all-groups python -m twquant recommendations-import <檔> --out D:/project/twquant/data
```

平台 API 讀的是 `configs/system.yaml` 解析後的 data_dir，並以 repo 根為基準補成絕對路徑，
所以 API 的工作目錄不影響它讀哪一份資料。

## 3. 餵資料進平台（三條入口，全部是受控匯入，不會啟動研究）

平台**只讀已經產生的東西**。以下指令都不訓練、不推論、不啟動新研究試驗，
所以不會增加 DSR 試驗計數。

```bash
uv run --all-groups python -m twquant ml export-recommendations --date 2026-09-04 --model <模型目錄> --out <data_dir>
```
把**既有**的 `scores_<date>.parquet` 輸出成推薦快照。`run_id` 由 model_id＋推論日決定性導出，
所以同一個推論日只會有一份快照；重跑相同內容是冪等的，內容不同會**拒絕覆寫**。
輸出一律 `mode=research`、`published_at=null`——訓練成功不等於取得發布資格。

```bash
uv run --all-groups python -m twquant recommendations-import <快照.json> --out <data_dir>
```
匯入外部產生的推薦快照（合約見 SPEC-014 §4）。只驗證與保存。

```bash
uv run --all-groups python -m twquant insights-import <證據包.json> --out <data_dir>
```
匯入洞悉證據包（合約見 SPEC-014 §6）。**引用不存在的 evidence，或引用了截點之後才可得的
evidence，會被拒絕**；沒有分析時必須寫明 `analysis_absent_reason`。

檔案落點：`<data_dir>/recommendations/<run_id>.json`、`<data_dir>/insights/<insight_run_id>.json`。

## 4. 頁面與它們的真實來源

| 頁面 | 讀哪個端點 | 沒有資料時顯示什麼 |
|---|---|---|
| 今日總覽／ML 選股 | `/api/platform/recommendations` | 未產生／合法零筆／讀取失敗／快照過期，四種分開 |
| 個股研究 | 既有 `/api/stocks/*` ＋ `/api/platform/stocks/{sid}/recommendations` | 讀取失敗明示，**不退回離線快照** |
| 產業研究 | 既有 `/api/sector-flows` ＋ `/api/stock-meta` | 標明窗口、資料日、涵蓋檔數；分類為**當前分類非歷史 PIT** |
| 洞悉與證據 | `/api/platform/insights` | LLM 離線只顯示證據與缺席原因，不生成分析 |
| 模型中心／回測中心 | `/api/platform/models`、`/backtests` | 舊 artifact 未記錄執行歷程時明示「未記錄」 |
| 我的追蹤 | 本機 localStorage `twquant.watchlist` | 舊格式可讀；損毀資料保留原值並提示 |
| 資料與工作 | 既有 ingest 端點 | 三個頁籤，狀態存 URL，輪詢不會重設 |

**沒有任何頁面會用 `frontend/data.json` 冒充缺席的模型、推薦、訓練、回測或洞悉。**

## 5. 打包與部署

前端在乾淨的 Linux node 容器內打包（繞開 Windows toolchain）：

```bash
docker compose build frontend
```

```bash
docker compose up -d api frontend
```

- 前端：<http://localhost:8899>
- API：<http://localhost:8000/api>（`/api/docs` 有 OpenAPI）
- nginx 把 `/api/` 反代到 `api:8000`，所以前端同源、免 CORS。

API 容器把 `${TWQUANT_DATA_HOST:-./data}` 掛成 `/app/data`。**要讓平台看得到快照，
匯入時的 `--out` 必須就是這個掛載來源**，否則頁面會正確地顯示「尚未產生」。

## 6. 開發時的閘門

後端閘門不能取代前端檢查，兩邊都要跑：

```bash
PYTEST_ADDOPTS="--basetemp=C:/t/pt" TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant" uv run --all-groups python -m twquant check
```

```bash
cd frontend && npm test && npm run test:browser
```

`npm run test:browser` 會先 `npm run build`（含 `tsc -b` 型別檢查），再用**固定 HTTP fixture**
跑完整旅程；測試全程不連網。若 mypy 報出看起來不合理的錯誤，先 `rm -rf .mypy_cache` 再跑一次
——本輪遇過兩次陳舊快取造成的假錯誤。

## 7. 壞掉時怎麼分辨

平台刻意把這三件事分開顯示，回報問題時請照抄畫面上的字：

1. **尚未接入**——這個頁面還沒有資料來源。
2. **真正零筆**——來源回應成功，但這個條件下就是沒有資料（例如某個 run 合法地沒有候選）。
3. **讀取失敗**——來源掛了。這**不是**「沒有訊號」，也不會用其他資料替代。

「快照過期」是第四種：有資料，但它的截點比行情資料日舊，代表這是舊快照而不是今天的推薦。
