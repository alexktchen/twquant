# 東西在哪（2026-09-06，含研究平台上線）

> **給下一個 session 的第一份文件。** 讀完這頁就能動手，不必翻其他文件。
> 深入的來龍去脈在 [docs/HANDOFF-2026-09-06-tracks-and-gates.md](docs/HANDOFF-2026-09-06-tracks-and-gates.md)，
> 需要時再讀。

## 0. 三十秒版本

- 程式碼在 worktree：`D:/project/twquant/.claude/worktrees/strange-kalam-3ec702`
  （平台工單 T-200~T-215 在 `.claude/worktrees/beautiful-elbakyan-405956`，branch `claude/codex-platform-console-cb66ad`）
- **資料一律在主 checkout**：`D:/project/twquant/data/`（不進 git，但都在本機）
- 報告：`D:/project/twquant/data/reports/backtest_report.html`
- 現況：**83 筆回測完成，沒有任何策略打敗 0050 含息（+40.08%／Sharpe 1.63）**
- **研究平台已上線**：<http://localhost:8899>（前端）、<http://localhost:8000/api>（API）。
  操作看 [docs/OPERATIONS-platform-console.md](docs/OPERATIONS-platform-console.md)

## 1. ⚠ 最容易踩的陷阱：有兩個 `data/`

| 路徑 | 內容 |
|---|---|
| `D:/project/twquant/data/` | **正本**。所有結果、模型、報告、研究登記簿、staging |
| `<worktree>/data/` | **不要用**。`configs/system.yaml` 的 `data_dir: data` 是相對路徑，在 worktree 裡跑會寫到這裡 |

**今晚已經踩過一次**：`ml train` 把重訓的模型寫進 worktree 而不是模型庫，加 `--out` 才修好。
worktree 那份的內容已在 2026-09-06 全部合併回主 checkout。

**規則：任何寫檔的指令都明確給絕對路徑**（`--out`／`--models`／`--data-dir`）。
未修的相關任務：**T-084**。

## 2. 資料在哪

| 內容 | 路徑 | 量 |
|---|---|---|
| **回測結果包** | `data/ladder/`（80M）、`data/ladder10m/`（10M）、`data/tracks/`（雙軌）、`data/tracks_c*/`（C 軌）、`data/tracks_m*/`（M 系列）、`data/tracks_g/`（G 矩陣）、`data/tracks_mg/`（MG） | 約 60MB／104 個 JSON |
| **報告** | `data/reports/backtest_report.html`（含三張圖）、`all_backtests.json`（唯一資料源） | — |
| **模型** | `data/models/lgbm_net_h20_20260225_6a798962`（**現行**） | 213MB（含舊模型） |
| **研究登記簿（DSR）** | `data/research_log/` | 241 檔／1,884 筆 |
| 長天期回補原始檔 | `data/staging/finmind_hist_*` | 462MB |
| **推薦快照** | `data/recommendations/<run_id>.json` | 2026-09-04 一份／1943 檔候選 |
| **洞悉證據包** | `data/insights/<insight_run_id>.json` | 目前 0 份（頁面正確顯示尚未產生） |
| 執行日誌 | `data/*.log`、`data/*/run.log` | — |
| **PG 供應層** | `postgresql://twquant:twquant@localhost:5433/twquant`，schema `serving` | 約 27GB |

**歸檔但刻意保留的**（是證據，不要刪）：
- `data/ladder/_pre-T142-T149/`、`data/tracks/_pre-T142-T149/` — 舊的 10M 批次，D-070 的證據
- `data/models/_archive/…__pre-D064-data-defects/` — 汙染資料訓練的舊模型
- `data/models/_rejected/…__vault-eroded-3-sessions/` — 越過保險庫邊界的作廢模型

## 3. 文件在哪（全部在 worktree，進 git）

| 檔 | 讀它的時機 |
|---|---|
| `CLAUDE.md` | **動手前必讀**。十條黃金規則 |
| `TASKS.md` | 領任務。157 項、剩 41 項 |
| `DECISIONS.md` | **改設計前必讀**。D-001~D-076；六條事前登記不得事後修改 |
| `CHANGELOG.md` | 想知道某個決定為什麼被做 |
| `docs/HANDOFF-2026-09-06-tracks-and-gates.md` | **本輪完整交接**：進度、五個未解問題、產物清單 |
| `docs/reports/backtest-results.md` | 結果的版控版（與 HTML 同源） |
| `docs/specs/SPEC-013-two-track-strategies.md` | 雙軌策略的規格 |

## 4. 常用指令（都在 worktree 執行）

```bash
cd D:/project/twquant/.claude/worktrees/strange-kalam-3ec702
```

| 做什麼 | 指令 |
|---|---|
| **閘門**（改任何程式碼後必跑） | `PYTEST_ADDOPTS="--basetemp=C:/t/pt" TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant" uv run --all-groups python -m twquant check` |
| **更新報告** | `uv run --all-groups python scripts/report_bundle.py && uv run --all-groups python scripts/build_report.py` |
| **靜默失效靜態檢查** | `uv run --all-groups python scripts/audit_silent.py` |
| **資料稽核** | `uv run --all-groups python scripts/audit_data.py --since 2022-07-01 --tables prices_daily` |
| 跑階梯 | `uv run --all-groups python scripts/run_ladder.py --capital 80000000 --out D:/project/twquant/data/ladder` |
| 跑軌別 | `uv run --all-groups python scripts/run_tracks.py --tracks C1,C3 --out D:/project/twquant/data/tracks_c` |
| 判定 | `scripts/t146_verdict.py`（D-066）、`scripts/d071_verdict.py`（D-071）、`scripts/d067_edges.py`（D-067） |
| **前端閘門**（後端 gate 不涵蓋） | `cd frontend && npm test && npm run test:browser` |
| **產生洞悉證據包** | `uv run --all-groups python -m twquant insights-run --run <run_id> --top 30 --out D:/project/twquant/data` |
| **平台部署** | `docker compose build frontend api && TWQUANT_DATA_HOST=D:/project/twquant/data docker compose up -d api frontend` |
| **把推論輸出成推薦** | `uv run --all-groups python -m twquant ml export-recommendations --date <日> --model <模型目錄> --out D:/project/twquant/data` |

**所有指令都要 `uv run --all-groups`**，直接 `python` 會缺套件（polars 等）。

## 5. 現在的狀態

| | CAGR | MDD | Sharpe |
|---|---|---|---|
| **0050 含息（基準）** | **+40.08%** | −27.5% | **1.63** |
| B2（廣泛市值加權） | +27.79% | −30.9% | 1.35 |
| B1（大型增強） | +23.68% | **−24.0%** | 1.28 |
| C1（52 週高點突破） | +26.74% | −36.7% | 0.93 |
| L0-new（ML 基準） | +13.65% | −46.1% | 0.58 |

**ML 判定為 D-066 乙層**：保留為零件，**明文禁止重啟特徵工程或調參**。改投組建構屬架構，允許。

## 6. 動手前一定要知道的三件事

**① DSR 試驗計數已達 175**（實測自 `data/research_log/` 241 檔、1,884 筆的 distinct `params_hash`，非估算）。試驗數一多，「最好那格」有很大
一部分是選擇偏誤。**不得挑冠軍**；任一格要晉升須另立事前登記、獨立重跑。

**② 事前登記不得事後修改。** D-066／D-067／D-071~D-075 的門檻都是在看到結果前寫死的。
結果難看就改門檻，等於沒有登記。

**③ 稀疏閘門相乘會歸零**（D-076）。每日 0~5 檔的事件乘上任何 30% 的池子，
交集期望 0~1.5 檔。**疊閘門前先算交集大小**——本輪已因此作廢兩批實驗（M2/M3 曝險 3%、GALL 0%）。

**④ `ml score` 挑錯模型——已修（T-224）。** 現有 8 個模型的 train_end **全部是 2026-02-25**，
舊版 `latest_model_dir` 同分時取排序最前者＝`lgbm_dd_h60`（D-075 的**回撤風控**模型）當選股器。
現在候選不唯一會 **raise 並列出候選**，並可用 `--purpose` 過濾。每日推論用哪個模型由
`configs/ml.yaml` 的 **`production_ranking_model`** 明寫（目前 `lgbm_net_h20_20260225_6a798962`）
——換模型改這一行。L0 產生鏈已掛進每日 pipeline（T-225）。

**⑤ 重跑「已擷取過的某一天」的 pipeline 會 OOM（2026-09-07 實測）。** 正常的每日前進式
執行沒問題——排程器 2026-09-04 那天跑完整條線（供應層鏡射同步＋物化 28,000 因子列、
選股池 381 列）。但把同一天再跑一次，**供應層步驟會被 OOM killer 砍掉**（容器 exit 137），
訊號生成後 RSS 已 1.7 GB，鏡射同步再推一次大 delta 就爆。這不是新步驟造成的：
ML 推薦名單與洞悉分析排在供應層之後，根本沒被執行到。

**更危險的是 `docker compose run` 會回報 exit 0**，即使容器其實是被 SIGKILL（137）殺掉的。
用 `docker run` 才看得到真正的 137。任何靠 exit code 判斷成功的包裝都會被騙。未修，見 T-226。

## 7. 還在跑的東西

- **長天期回補**：融資融券 `data/staging/finmind_hist_margin`，約 3.9 小時（其餘六個資料集已滿）
- **連續作業**：`scripts/overnight2.sh`，日誌 `data/overnight.log`

## 8. 沒有備份

`data/`（60MB 結果 + 213MB 模型 + 462MB staging）與 PG（27GB）**都沒有備份**。
結果包可從 PG 重跑產生（數小時），PG 本身重建則要重抓資料。
