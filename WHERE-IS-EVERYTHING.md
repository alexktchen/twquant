# 東西在哪（2026-09-06）

> **給下一個 session 的第一份文件。** 讀完這頁就能動手，不必翻其他文件。
> 深入的來龍去脈在 [docs/HANDOFF-2026-09-06-tracks-and-gates.md](docs/HANDOFF-2026-09-06-tracks-and-gates.md)，
> 需要時再讀。

## 0. 三十秒版本

- 程式碼在 worktree：`D:/project/twquant/.claude/worktrees/strange-kalam-3ec702`
- **資料一律在主 checkout**：`D:/project/twquant/data/`（不進 git，但都在本機）
- 報告：`D:/project/twquant/data/reports/backtest_report.html`
- 現況：**83 筆回測完成，沒有任何策略打敗 0050 含息（+40.08%／Sharpe 1.63）**

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

## 7. 還在跑的東西

- **長天期回補**：融資融券 `data/staging/finmind_hist_margin`，約 3.9 小時（其餘六個資料集已滿）
- **連續作業**：`scripts/overnight2.sh`，日誌 `data/overnight.log`

## 8. 沒有備份

`data/`（60MB 結果 + 213MB 模型 + 462MB staging）與 PG（27GB）**都沒有備份**。
結果包可從 PG 重跑產生（數小時），PG 本身重建則要重抓資料。
