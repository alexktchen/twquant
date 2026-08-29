# 交接：ML 橫斷面排序層（T-065~T-070，SPEC-010）

> 這份文件的用途：把雲端 session 的全部進度、實測數字、未結問題與下一步，
> 完整交給**本機**接手的 session（或人）。讀完這份 + SPEC-010 就能繼續，
> 不需要回頭翻對話紀錄。
>
> 分支：`claude/data-running-check-8b2zil`
> 交接時點：2026-08-29

---

## 1. 一句話現況

ML 橫斷面排序層（LightGBM 對 ~25 個既有因子做 meta-factor）**已完成 T-065~T-068，
T-069 評估階梯第 1~3 級通過**；第 4 級（deflated Sharpe）與第 5 級（金庫期最終
評估）未做，**尚未取得部署許可**，T-070（接每日 pipeline）仍 blocked。

## 2. 為什麼要交接到本機

SPEC-010 §0 已裁決執行環境分工：

| 軌 | 工作 | 執行環境 |
|---|---|---|
| 研究軌 | `ml train`／`ml eval`／`ml backfill-scores`／`backtest` | **本機** |
| 生產軌 | `ml score`（每日推論，秒級） | 雲端 GHA |

雲端 runner 的限制（16 GB 記憶體、60 分鐘上限、每次要花 10 分鐘從 Drive
還原 1.4 GB dump）逼出了兩個**只在 CI 才需要的妥協**，本機都不需要：

1. **修剪 `prices_daily`** 到「回測起日 −120 曆日」（907 萬 → 60 萬列），
   繞過 `universe_ids` 逐訊號日全表重算。安全但邊際只有 18 個交易日。
2. **只跑得動短區間**，訓練窗數受限。

本機有完整 15 GB 庫、無時限，這兩個妥協直接消失。

## 3. 已完成的實測數字（可直接拿來對照本機重跑）

**決定性保證**：seed／`deterministic=true`／`force_row_wise=true`／`num_threads=4`
都在程式內鎖死。同 config 同資料，本機應重現**完全相同**的 model_id 與 IC
（CI 已實測重跑重現至小數第 10 位）。

### 模型

```
model_id       lgbm_h10_20260112_2f5a21a9
config_hash    2f5a21a9b7a2f4c1
訓練資料       2023-01-03 ~ 2026-07-29
train_end      2026-01-12   （金庫期 2026-01-13 之後完全未動）
特徵           25 個（chip 7 / fundamental 2 / price 13 / volume 2 / …）
lightgbm       4.7.0
oos_rank_ic    +0.0814394411934806
window_ics     +0.1176 / +0.0554 / +0.0905 / +0.0539   （4 窗全正）
```

### 階梯第 1~2 級：OOS RankIC 與分位單調（43.8 萬筆共同樣本、inner join 同宇宙）

| | RankIC | IR | 勝率 | NW t | 分位頭尾 | 嚴格單調 |
|---|---|---|---|---|---|---|
| **ML（LightGBM）** | **+0.0815** | 0.975 | 87.6% | **6.76** | +0.0860 | **是** |
| 基準A 全因子等權 | −0.0183 | −0.168 | 41.2% | −1.09 | −0.0209 | 否 |
| 基準B D-020 有效子集 | +0.0369 | 0.375 | 70.0% | 3.15 | +0.0380 | 否 |

判定取**最強基準（B）**：ML 勝出 RankIC +0.0446（2.2 倍）。
基準B 本身顯著（t 3.15）→ D-020 篩選正確，ML 是在有訊號的對手上勝出。

⚠ 誠實標註：樸素 t = 14.87，但 10 日標籤重疊使日 IC 自相關 lag1 = 0.72，
**以 Newey-West t = 6.76 為準**。

### 階梯第 3 級：成本後回測（2025-02-05 ~ 2026-01-12，233 個交易日）

分數來源是 walk-forward 的**樣本外**分數（`ml backfill-scores`）——
用最終模型回頭對全期推論會是前視。

| | CAGR | Sharpe | Sortino | Calmar | MDD | 勝率 | 年化單邊周轉 | 成交 | 未成交 |
|---|---|---|---|---|---|---|---|---|---|
| **ML `ml_rank`** | **+23.71%** | **+1.27** | +1.45 | +1.34 | −17.68% | 39.7% | **1820%** | 814 | 2 |
| 對照 `reversal_revenue` | −8.90% | −0.42 | −0.44 | −0.46 | −19.48% | 39.7% | 2659% | 1150 | 16 |

（總報酬：ML +21.63%、對照 −8.23%）

**結論**：IC 優勢確實穿透了含 0.3% 證交稅的來回成本，不是紙上訊號。

⚠ **保留而非無條件通過**——見 §5 未結問題。

## 4. 程式與配置在哪

```
src/twquant/ml/
  splits.py     walk_forward_splits()  purged walk-forward ＋ embargo ＋ 金庫期
  dataset.py    forward_open_returns() / excess_rank_labels() / cross_rank_pct() / assemble()
  train.py      fit_ranker / walk_forward_train / make_model_id / save_model / log_training_run
  score.py      load_model（train_end >= as_of 直接 raise）/ latest_model_dir
  evaluate.py   daily_rank_ic / ic_summary（含 Newey-West）/ decile_table / is_monotonic / spread
src/twquant/factors/ml.py            薄因子 ml.lgbm_rank_10d（只讀 ml_scores，lookback 0）
src/twquant/data/serving/ml_store.py upsert_ml_scores / read_factor_frames / read_prices_open / read_factor_long
configs/ml.yaml                      所有 ML 參數（改任一項＝一次新實驗，config_hash 會變）
configs/strategies/ml_rank.yaml      受測策略：唯一訊號 ml.lgbm_rank_10d、top20、週MON、next_open、單檔上限 5%
docs/specs/SPEC-010-ml.md            規格（§0 執行分工、§2 資料事實、§7 評估階梯與 AC）
docs/ML-local-train.md               本機 runbook（PowerShell）
```

測試：`tests/test_ml_{splits,dataset,train,evaluate,factor}.py`（先寫測試後實作，
AC 編號對應在測試名稱裡）。

## 5. 未結問題（接手第一順位）

### 5.1 【已定位】smoke 回測 0 成交

CI 的 1 個月 smoke（2025-02-05 ~ 2025-03-12，25 個交易日）跑出
**成交 0 筆、未成交 0 筆、周轉 0%**，同起日的全期（233 天）卻有 814 筆成交。

**已經用資料排除的原因**：

- ✗ 不是分數缺漏。`oos_scores.parquet` 在 2025-02-05 當天就有 **1,730 檔**有分數
  （全期 233 天、43.86 萬列，起訖 2025-02-05 ~ 2026-01-12）。
- ✗ 不是修剪造成的。修剪步驟在 smoke 與全期**之前**就跑完了，兩者共用同一張
  已修剪的 `prices_daily`。
- ✗ 不是訊號日排不出來。`signal_dates` 的 weekly 規則（`configs/strategies/ml_rank.yaml`
  用 MON）在 2025-02-05（週三）起算的 25 個交易日內會排出約 5~6 個訊號日。

**現象的邏輯含意**：成交 0 筆**且**未成交 0 筆。若目標權重有產出但買不到，
`未成交` 應該 > 0。兩者同時為 0，指向 `build_targets()` 在每個訊號日都回了空表
（`engine.py` 的 `pending` 始終是空 dict）——**除非** `_execute()` 對「當日面板查
無該股價格」是靜默略過（既不計成交也不計未成交），那就是另一條路徑，而且依
黃金規則 5 那本身就是個 bug。

**最可能的根因（待證實）**：`build_targets()` 先呼叫 `universe_ids()`，宇宙空就
直接回空表。`universe_ids` 的衛生濾網需要 60 個交易日回看（`shift(60)`）加 20 日
ADV。若 warehouse 讀到的價格範圍被 `--start/--end` 限制在回測區間內，25 個交易日
就湊不出 60 天回看 → 宇宙全空 → 每個訊號日都回空表；而 233 天的全期沒這個問題。
這能一致解釋所有觀察。

**接手要做的**：確認 `cli.py` 的 backtest 指令是否把 `--start/--end` 傳進
warehouse 的讀取範圍。若是，短窗回測就是**沉默地回傳無意義的全零結果**——
這比 0 成交本身更嚴重，因為它不會報錯（違反黃金規則 5），任何人跑短區間
smoke 都會被騙。修法是短窗時明確 raise，或讓 warehouse 讀取範圍自動涵蓋
策略宣告的最大 lookback。

⚠ **這個異常不推翻全期結果**——全期 233 天遠超過任何回看需求，其 814 筆成交與
指標本身自洽。但**部署前必須解釋清楚**。


### 5.2 年化單邊周轉 1,820%

約合每年換手 18 次、平均持有約 13 個交易日——**與 10 日標籤視窗同構**，
屬結構性而非設定失誤。成本已扣仍賺，但這使淨值對費率假設**極度敏感**：
費率若上修，優勢會等比例被吃掉。

降周轉的候選手段（分數 EMA 平滑、buffer-zone 換股）**都要另行驗證並計入
DSR 的 trials 預算**——不得事後挑最好看的參數。這是 SPEC-010 §7 的登記簿紀律。

### 5.3 `universe_ids` 逐訊號日全表重算（所有 PG 回測共通）

每個訊號日對整張 `prices_daily` 做 sort + `rolling_median(20).over` + 兩輪
`cum_sum().over`。CI 上 48 個訊號日重複 48 次 → 46 分鐘沒跑完被砍。
薄因子本身只佔 1.85 s/次，**不是瓶頸**。

根治：改成只取 `signal_date − 回看窗` 的切片。這影響所有 PG 回測
（`reversal_revenue` 亦然），**應另立任務**，不要塞進 T-069。

### 5.4 `factor_values` 歷史洞

連續可用段＝**2023-01 起**（3.5 年）。2020-05~12、2021-03~2022-12 整段缺失，
2020-21 僅零星月＋小宇宙。標籤（來自 prices）有 2020-01 起 6.5 年，但特徵沒有。

- 判洞閾值用「月資料日 **< 10**」——用 `< 15` 會誤殺春節月（正常只有 12~13 天）。
- 長期解＝補洞：`serving materialize --from ... --to ...` 回跑 2020~2022，
  與歷史 chip 因子回補併同另列任務。補完可把訓練資料從 3.5 年拉到 6.5 年。

### 5.5 CI 修剪手法的脆弱處（若日後重用）

以曆日換算交易日不穩，遇長年假或補班會使邊際趨近 60。改用交易日精確定界：

```sql
SELECT min(date) FROM (
  SELECT DISTINCT date FROM serving.prices_daily
  WHERE date < :bt_from ORDER BY date DESC LIMIT 90
) t
```

## 6. 下一步：階梯第 4 級（DSR）怎麼走

工具**已經實作好了**，在 `src/twquant/backtest/validation.py`：

```python
deflated_sharpe(observed_sharpe, trial_sharpes, n_obs, skew=0.0, kurtosis=3.0) -> float
expected_max_sharpe(trial_sharpes) -> float
deannualize_sharpe(annual_sharpe) -> float      # 年化 SR ÷ √252
```

`reporting/research_log.py` 提供 `trials_count(data_dir, trial_scope)`。

**關鍵注意事項（會決定結論誠不誠實）**：

1. `deflated_sharpe` 要的是 **trial_sharpes 這個「清單」**，不是 trials「個數」——
   它需要各試驗 SR 的變異數才能估期望最大 SR（REVIEW M11 已明確要求）。
2. 輸入必須是**每期（非年化）SR**：先 `deannualize_sharpe(1.27)`。
   `n_obs` = 報酬期數 = 233。
3. **trials 要誠實**。CI 那份 research_log 是全新 checkout 產生的，只有 10 列
   （1 次 ml_train + 3 次 backtest）。真正的 trials 母體在**你本機累積的
   research_log**，而且應該把所有跑過的 ML 配置都算進去。用 CI 那份會讓 DSR
   幾乎不打折，結論會偏樂觀——那等於自欺。
4. AC-10 要求「DSR 報告含 trials 數且 trials ＝ 登記簿列數」。目前 CLI 只在
   IC 報告路徑用了 `trials_count`（`cli.py:1369`），**回測路徑還沒有 DSR 報告
   指令**，需要補一個小 CLI（這是 T-069 剩下的實作工作）。
5. DSR 不顯著即**止步**，不得放寬（黃金規則 7）。

第 4 級過了才進第 5 級：**金庫期最終評估**。金庫期是 2026-01-13 之後的資料，
到目前為止**完全沒有被任何訓練或選擇碰過**——這是最後一道、也是唯一一道
真正乾淨的樣本外檢驗。跑完才輪到 DECISIONS.md 的部署裁決，通過才解鎖 T-070。

## 7. 本機怎麼接手

```powershell
git fetch origin
git checkout claude/data-running-check-8b2zil
git pull
uv sync --dev --all-groups          # 會裝 lightgbm

# 基線閘門先確認是綠的（黃金規則：基線紅不是你的問題，記 DECISIONS.md）
uv run python -m twquant check
```

資料庫：本機 docker PG（`docker compose up -d pg`），DSN 走
`configs/serving.yaml` 的 `dsn_env` 指的環境變數，值放 `.env`（已 gitignore，
**任何真實 token 不得進 repo**）。若 volume 已清，從 Drive base dump 還原——
步驟見 `docs/ML-local-train.md` §1-B。

重跑第 1~3 級（驗證交接無損；本機不需要 CI 的修剪妥協）：

```powershell
uv run python -m twquant ml train --from 2023-01-01 --to 2026-07-29
$MDIR = (Get-ChildItem data/models -Directory | Select-Object -Last 1).FullName
uv run python -m twquant ml eval --model $MDIR
uv run python -m twquant ml backfill-scores --model $MDIR
uv run python -m twquant backtest --strategy configs/strategies/ml_rank.yaml `
  --start 2025-02-05 --end 2026-01-12 --pg
uv run python -m twquant backtest --strategy configs/strategies/reversal_revenue.yaml `
  --start 2025-02-05 --end 2026-01-12 --pg
```

對照 §3 的數字。model_id 應該一模一樣是 `lgbm_h10_20260112_2f5a21a9`；
若不同，**先查為什麼**（資料版本？configs/ml.yaml 被改過？）再往下走。

## 8. 紀律提醒（給接手的 AI）

- 黃金規則 6/7：`tests/golden/` 不可改；不准放寬 assert、刪測試、加 skip 來讓
  結果好看。要改基準走 DECISIONS.md。
- 黃金規則 8：參數放 `configs/`，`.py` 裡不准出現魔術數字。
- 黃金規則 5：不准靜默處理——`except: pass`、自動填 NaN 成 0、drop 掉驗證失敗
  的列，全部禁止。
- 評估階梯**任一級輸給既有線性基準即整包止步**，不要為了讓 ML 過關而換基準
  或換區間。每換一次配置就是一次 trial，要登記，DSR 會扣。
