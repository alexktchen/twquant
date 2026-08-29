# ML 本機訓練 runbook（T-067/T-069，SPEC-010）

研究軌訓練在本機跑的操作手冊（Windows / PowerShell）。訓練＝讀本機 PG 的
factor_values（特徵）＋prices_daily（標籤）→ LightGBM walk-forward → 模型落地
`data/models/` ＋ research_log 登記。決定性：同 config 同資料，本機與 CI 結果
bit-identical（seed／deterministic／4 執行緒已在程式內固定）。

## 1. 前置

```powershell
git fetch origin
git checkout claude/data-running-check-8b2zil   # 或合併後的 main
uv sync --dev --all-groups                       # 會安裝 lightgbm
```

資料庫二選一：

**A. 原本機 docker PG 還在（最快）**——15 GB 全史庫直接用：

```powershell
docker compose up -d pg          # 或 docker start twquant-pg-1
# TWQUANT_PG_DSN 用原本 .env 的本機 DSN（configs/serving.yaml dsn_env 指的環境變數）
```

**B. volume 已清——從 Drive base dump 還原**：

```powershell
rclone copy gdrive:twquant-archive/base/twquant-full-20260729.dump .
docker run -d --name twquant-pg-ml -e POSTGRES_USER=twquant -e POSTGRES_PASSWORD=twquant `
  -e POSTGRES_DB=twquant -p 5432:5432 postgres:16-alpine
docker cp twquant-full-20260729.dump twquant-pg-ml:/tmp/full.dump
docker exec twquant-pg-ml pg_restore -U twquant -d twquant --no-owner -j 4 /tmp/full.dump
$env:TWQUANT_PG_DSN = "postgresql://twquant:twquant@localhost:5432/twquant"
```

## 2. 確認訓練區間（factor_values 有歷史洞，先看再訓）

首訓 CI（run #3/#4，2026-08-29）實測：factor_values **連續段＝2023-01 起**；
2020-05~2020-12、2021-03~2022-12 整段缺失（2020-21 僅零星月＋小宇宙）——逐因子
min/max 會騙人，訓練起點必須在最後一個洞之後。注意春節月正常只有 12~13 個
交易日，判洞閾值用「月資料日 < 10」。逐月盤點：

```powershell
psql $env:TWQUANT_PG_DSN -c "SELECT date_trunc('month', date)::date AS mo, count(DISTINCT date) AS days, count(*)/count(DISTINCT date) AS rows_per_day FROM serving.factor_values GROUP BY 1 ORDER BY 1"
```

實測結論：`--from 2023-01-01`。長期解＝補洞：`serving materialize --from ...
--to ...` 回跑 2020~2022 缺段（與歷史 chip 因子回補併同另列任務）。

## 3. 訓練與推論

```powershell
uv run python -m twquant ml train --from 2023-01-01 --to 2026-07-29
# 過程逐窗印 OOS RankIC；產出 data/models/lgbm_h10_<train_end>_<cfg8>/
#   model.txt / meta.json（train_end、config_hash、oos_rank_ic、window_ics）
#   oos_scores.parquet（全部樣本外分數，T-069 評估階梯的輸入）
# research_log 自動 append 一列（trials 計數供 DSR）

uv run python -m twquant ml score --date 2026-07-29 --pg
# 自動選 train_end < 該日的最新模型（違反前視直接 raise）；--pg 直寫 serving.ml_scores
```

參數全在 `configs/ml.yaml`（改 lgbm 參數＝一次新實驗，research_log 會以
config_hash 區分；embargo < horizon 會在載入配置時就被擋）。

## 4. 之後接什麼

- 分數進策略：`ml.lgbm_rank_10d` 因子已註冊（讀 serving.ml_scores 的 rank_pct），
  策略 YAML 把它列入 signals 即可（SPEC-010 §6）。
- T-069 評估階梯：OOS RankIC → 分位單調 → 成本後回測 → DSR → 金庫期，全過才
  裁決部署（SPEC-010 §7）。
