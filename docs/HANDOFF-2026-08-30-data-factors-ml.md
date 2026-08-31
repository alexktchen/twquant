# 交接文件：2026-08-30 資料補齊、全歷史因子物化與 ML 重訓就緒

- **日期**：2026-08-30
- **分支**：`main`（已同步最新 `origin/main`，無未提交代碼）
- **主要環境**：Local Docker PG (`localhost:5433`，`twquant-pg-1`)
- **DSN**：`postgresql://twquant:twquant@localhost:5433/twquant`

---

## 1. 執行總結

本日完成 **2026 年 8 月全台股資料補齊**、**因子物化修復**、以及 **2020~2022 年全歷史因子斷層回補（方案 B）**。

### A. 2026-08 增量資料補齊（100% 完成，零缺口）
1. **月營收**：2026-07 上市 (993) + 上櫃 (860) 營收全數補入。
2. **每日行情/三大法人/融資券**：
   - 上櫃（TPEx）：08-03 ~ 08-28（20 交易日）全部補齊。
   - 上市（TWSE）：克服 WAF 暫態封鎖後，08-24 ~ 08-28 及 07-29 ~ 07-31 融資券全數補齊。
3. **指數與集保**：大盤/櫃買指數 07~08 月、TDCC 股權分散表 50,610 筆已存入。
4. **PG 供應層同步**：`serving sync` 推進至 2026-08-28，全表對齊。
5. **選股池快照**：08-01 ~ 08-28 每日快照（`serving.screener_daily`）以完整上市+上櫃宇宙重建。

### B. 歷史因子物化回補（方案 B，解決 2020-05~2022-12 歷史洞）
原本 `serving.factor_values` 在 2020-05~2020-12 與 2021-03~2022-12 有大斷層，導致 ML 只能從 2023-01 起訓（僅 3.5 年，避開 2020 疫情崩盤與 2022 空頭）。
- **2020-05-01 ~ 2020-12-31**（169 交易日）：**已全數完成（耗時 27.2 分鐘）**。
- **2021-03-01 ~ 2021-07-14**（93 交易日）：**已完成**。
- **2021-07-15 ~ 2022-12-31**（357 交易日）：目前分 3 個 Shard 本機並行物化中（進度約 35%~40%，預估 2026-08-30 22:15~22:20 完成）。

---

## 2. 背景任務進度（當前正在跑的任務）

| 任務 ID | 區間 | 交易日數 | 狀態 | 指令 |
|---|---|---|---|---|
| `task-260` | 2021-07-15 ~ 2021-12-31 | 118 天 | 進行中 (~45/118) | `uv run python scripts/backfill_factors.py D:/project/twquant/data 2021-07-15 2021-12-31` |
| `task-263` | 2022-01-01 ~ 2022-06-30 | 117 天 | 進行中 (~35/117) | `uv run python scripts/backfill_factors.py D:/project/twquant/data 2022-01-01 2022-06-30` |
| `task-265` | 2022-07-01 ~ 2022-12-31 | 124 天 | 進行中 (~30/124) | `uv run python scripts/backfill_factors.py D:/project/twquant/data 2022-07-01 2022-12-31` |

> 註：三個 Shard 採用 `serving.factor_values` 的主鍵 `(date, factor, stock_id)` 進行 upsert，無任何鎖衝突或順序相依性。

---

## 3. 接手後操作 SOP（Step-by-Step）

### Step 1：確認背景因子物化完成
在 PowerShell 執行：
```powershell
$env:TWQUANT_PG_DSN = "postgresql://twquant:twquant@localhost:5433/twquant"
uv run python -c "
import psycopg
conn = psycopg.connect('$env:TWQUANT_PG_DSN')
cur = conn.cursor()
cur.execute('''
    SELECT date_trunc('month', date)::date AS mo, count(DISTINCT date) AS days, count(*)/count(DISTINCT date) AS rows_per_day
    FROM serving.factor_values
    WHERE date >= '2020-01-01'
    GROUP BY 1 ORDER BY 1
''')
print('=== 逐月因子覆蓋天數 ===')
for r in cur.fetchall():
    print(f'{r[0]}: {r[1]:2d} days, {r[2]:6d} rows/day')
conn.close()
"
```
**驗收標準**：2020-01 至 2026-08 每個月的 `days` 均在正常交易日範圍（春節月 11~15 天，正常月 19~23 天），無任何 `< 10` 的斷層。

---

### Step 2：啟動 6.5 年全歷史 ML 模型訓練
```powershell
$env:TWQUANT_PG_DSN = "postgresql://twquant:twquant@localhost:5433/twquant"
uv run python -m twquant ml train --from 2020-01-01 --to 2026-08-28
```
- **配置**：`configs/ml.yaml`（`horizon_days: 10`, `train_min_years: 2.0`, `test_months: 3`, `embargo_days: 10`, `vault_months: 6`）。
- **特徵**：因 `features: []`，自動讀取全部已物化因子。
- **產出落點**：`data/models/lgbm_h10_<train_end>_<hash8>/`，包含 `model.txt`、`meta.json`、`oos_scores.parquet`。
- **記錄登記**：自動寫入 `research_log`。

---

### Step 3：將樣本外分數寫入 PG 供應層
```powershell
$MDIR = (Get-ChildItem data/models -Directory | Select-Object -Last 1).FullName
uv run python -m twquant ml backfill-scores --model $MDIR
```

---

### Step 4：執行成本後策略回測（T-069 第 3 級）
```powershell
# 1. ML 排序純策略回測
uv run python -m twquant backtest --strategy configs/strategies/ml_rank.yaml `
  --start 2022-07-01 --end 2026-02-28 --pg

# 2. 對照基準策略回測（同期間同成本）
uv run python -m twquant backtest --strategy configs/strategies/reversal_revenue.yaml `
  --start 2022-07-01 --end 2026-02-28 --pg
```

---

## 4. 關鍵文件索引
- `docs/specs/SPEC-010-ml.md`：ML 排序層規格書（合約、切分規則、評估階梯）
- `docs/ML-local-train.md`：本機 ML 訓練操作手冊
- `configs/ml.yaml`：ML 超參數與切分設定
- `configs/serving.yaml`：供應層 PG 與因子配置
