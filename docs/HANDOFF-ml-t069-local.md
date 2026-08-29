# 交接：ML 排序層本機接手（T-071 完成、T-069 續跑中）

> 前一份是 `docs/HANDOFF-ml-t069.md`（雲端 session → 本機）。**那份仍然有效**，
> 只是它列的「未結問題 §5.1」已經查明並修掉，數字也被本機重跑取代。
> 讀順序：本文件 → `docs/HANDOFF-ml-t069.md`（背景與方法論）→ `SPEC-010`。
>
> 分支：`claude/ml-t069-continue`（從 `origin/main` c504d0d 開）
> PR：[#62](https://github.com/alexktchen/twquant/pull/62)（T-071，待審）
> 交接時點：2026-08-29 22:50

---

## 1. 一句話現況

D-037（衛生濾網把農曆年休市誤判為資料事件）**已裁決並修好**（T-071，PR #62 待審）；
第 1~2 級已在本機用完整庫重現；**第 3 級尚未重跑**；正在回補 2020~2022 的
`factor_values` 以把訓練期從 3.5 年拉到 6.5 年——**回補完成後前面所有數字都要重跑**。

## 2. 這一輪做完的事

### 2.1 D-037 → T-071（已修，PR #62）

`strategy/universe.py` 的缺口判準從**曆日**改為**交易日**：市場日曆＝`prices_daily`
的日期聯集（與 `data/calendar.py` 同源），缺口＝相鄰兩列之間漏掉的開市日數。
配置 `max_gap_days`（曆日 10）→ `max_gap_sessions`（開市日 5）。

一併修靜默回零：宇宙過濾後 0 檔改 raise `DataError`（含訊號日與五個濾網門檻）。
**追加證據**（本機完整未修剪庫）：`reversal_revenue` 2025-02-05~2025-03-12 同樣
0 成交／0 未成交／報酬 0%、exit 0，**且照常寫入 research_log**——確認此現象與 CI
的資料修剪無關，且無效試驗會污染第 4 級 DSR 的 trials 母體。

測試（先寫並確認皆紅再實作）：`test_ac1_market_wide_holiday_is_not_a_gap`／
`test_ac2_single_stock_suspension_still_excluded`／
`test_ac3_empty_universe_raises_instead_of_silent_zero`。
閘門：ruff／ruff format／mypy（152 檔）／pytest 409 passed 全綠。

### 2.2 本機重現第 1~2 級（用本機完整庫，非 CI 修剪庫）

```
model_id      lgbm_h10_20260115_2f5a21a9   （CI 是 …20260112_…；config_hash 同為 2f5a21a9）
train_end     2026-01-15                    （CI 2026-01-12）
oos_rank_ic   +0.0818                       （CI +0.0814）
窗 IC          +0.1176 / +0.0554 / +0.0905 / +0.0575   （前三窗與 CI 一字不差）
```

**model_id 不同不是 bug**：`config_hash` 相同（configs/ml.yaml 沒被動過），前三窗 IC
完全相同（決定性鎖死有效、特徵資料一致）。差別在本機該區間的交易日比 CI 那份 Drive
dump 多 3 天，`train_end = days[-127]` 的金庫期邊界因此後移，窗 4 的結束日不同。

第 1~2 級（444,336 筆共同樣本）：

| | RankIC | IR | 勝率 | NW t | 分位頭尾 | 嚴格單調 |
|---|---|---|---|---|---|---|
| **ML** | **+0.0818** | 0.984 | 87.7% | **6.88** | +0.0866 | **是** |
| 基準A 全因子等權 | −0.0190 | −0.175 | 40.7% | −1.14 | −0.0216 | 否 |
| 基準B D-020 子集 | +0.0367 | 0.376 | 70.3% | 3.18 | +0.0381 | 否 |

`ml_scores` 已回填 444,382 列（2025-02-05 ~ 2026-01-15、2,012 檔）。

### 2.3 資料補齊

- Drive `daily/` 31 天（2026-07-17~08-28）已全部下載並 `archive-restore` 進本機 PG。
  新增：prices +128,628、broker_trades +1,073,989、margin +25,869、institutional +24,005 列。
  各表最新日一律 **2026-08-28**。
- 八月因子（2026-08-03~08-28）正在本機物化——歸檔只含原始表，因子是衍生資料不在歸檔內。

## 3. 遠端有什麼（別再重查一次）

| 位置 | 內容 | 對本機有用嗎 |
|---|---|---|
| Drive `base/twquant-full-20260729.dump`（1.4 GB） | **從本機 PG dump 出去的**備份（OPS-archive §2） | ❌ 比本機舊，還原只會退步 |
| Drive `daily/2026/`（31 天） | 原始表增量 CSV，2026-07-17~08-28 | ✅ 已全部還原完畢 |
| Supabase（`TWQUANT_SUPABASE_DSN`） | factor_values **只有 20 個交易日**（2026-08-03~08-28、59.7 萬列）；prices 260 天；composite 120 天 | ❌ D-034 滾動窗口，是展示快取不是歷史庫 |

結論：**本機才是全史來源**，遠端沒有本機缺的歷史。

## 4. 本機資料現況（2026-08-29）

逐年交易日覆蓋（補完 Drive 增量後，`factor_values` 為八月物化前的狀態）：

```
年份   prices  factor_values  institutional  margin  broker_trades
2020    245         60             0           0          0
2021    244         33             0           0          0
2022    246          0            25           2        246
2023    239        239             0           0        239
2024    242        242             3           2        241
2025    243        243           142         141        243
2026    138+20     138            36+18       35+20      138+20
```

## 5. 重大發現：25 個特徵實際只有 11~12 個在訓練期有資料

逐因子覆蓋（`serving.factor_values` 的 distinct date 數）：

```
~900-950 天（2020 起）  price.mom_1d/20d/60d, reversal_5d, residual_reversal_5d,
                        intraday_ret_20d, overnight_ret_20d, vol_ratio_5d,
                        turnover_z_20d, revenue_yoy, revenue_accel   ← 只有這 11 個
704 天                  price.high_52w, price.vol_20d
166 天（2024-01 起）    chip.foreign_streak, chip.trust_streak
159 天（2025-07 起）    chip.inst_net_10d, chip.foreign_net_10d, chip.trust_net_10d
127 天（2026-01-02~）   chip.concentration_×4, main_force_net_5d, main_cost_gap,
                        stability_10d, broker_count_diff   ← 幾乎全在 train_end 之後
32 天                   price.rs_market_20d
10 天（2026-07-20~31）  price.box_breakout_60d, double_bottom_120d, hs_top_120d
7 天                    price.flag_hold_50d
```

**IC +0.0818 是價量＋月營收打出來的**，型態與籌碼特徵幾乎沒進到模型（整欄 null）。
任何對外敘述都不能講成「25 個因子的成果」。

- 型態那 4 個是**純價格因子** → 物化就能補到 2020（正在做）。
- 籌碼那 7 個要補 `institutional_daily`／`margin_daily` 的 2022~2025 歷史，
  只能回頭抓 TWSE 免費來源（**禁用 FinMind**）。使用者裁示：**先不補**。

## 6. 進行中：2020~2022 `factor_values` 回補

```powershell
uv run python -m twquant serving materialize --from 2020-01-01 --to 2020-12-31   # 逐年，可恢復
```

分年跑（2020→2021→2022），中斷只需從沒跑完的那年重來。實測速度約
**27 秒/交易日**（八月 20 天約 9 分鐘），730 個交易日推估 **5~6 小時**。

補完的預期效果：訓練期 3.5 年 → 6.5 年、walk-forward 窗 4 → 8~9、
OOS/回測期 233 天 → 約 950 天且涵蓋 2020 疫情崩盤與 2022 空頭、
第 4 級 DSR 的 `n_obs` 233 → ~950（現在的期數本來就很難顯著）。

⚠ **IC 掉下來是正常的**：現行 +0.0818 取自單一多頭段（2025-02~2026-01）。
加進兩個空頭段後下降才是誠實估計；掉到輸給基準 B 就依 SPEC-010 §7 整包止步，
**不得換基準或換區間**。

## 7. 待辦順序（回補完成後）

1. `ml train --from 2020-01-01 --to 2026-07-29` 重訓（`model_id` 必然改變，算一次新 trial）
2. `ml eval --model <新目錄>`（第 1~2 級，對照基準 A/B）
3. `ml backfill-scores --model <新目錄>`
4. 第 3 級成本後回測：`ml_rank` 與對照 `reversal_revenue` 各一次（**修好 D-037 後的第一次**，
   死區應消失、有效交易日回到全期）；周轉率要以有效期重算
5. **第 4 級 DSR——CLI 還沒寫**。工具都在 `backtest/validation.py`
   （`deflated_sharpe` / `expected_max_sharpe` / `deannualize_sharpe`），
   `reporting/research_log.py` 有 `trials_count`，但**回測路徑沒有 DSR 報告指令**
   （目前只有 IC 路徑用了 `trials_count`，見 `cli.py:1369`）。AC-10 要求報告含 trials 數
   且等於登記簿列數。這是 T-069 剩下的實作工作。
   注意：`deflated_sharpe` 要的是 **trial_sharpes 清單**不是個數；輸入須為**每期**
   （非年化）SR（先 `deannualize_sharpe`）；`n_obs` = 報酬期數。
6. 第 5 級金庫期最終評估（train_end 之後的資料，至今**完全沒被碰過**）
7. DECISIONS.md 部署裁決 → 通過才解鎖 T-070

## 8. 已知未修（各自另立任務，別夾帶）

- **`engine.py::_execute` 靜默丟單**：對「當日面板查無該股價格」「未達整股」的訂單
  既不計成交也不計未成交，違反黃金規則 5。T-071 只修了宇宙那條路徑。
- **`universe_ids` 逐訊號日全表重算**（前一份 §5.3）：每個訊號日對整張 `prices_daily`
  做 sort + `rolling_median(20).over` + 兩輪 `cum_sum().over`。本機 910 萬列下，
  一次全期回測要跑很久（CI 上 48 個訊號日 46 分鐘被砍）。根治＝只取
  `signal_date − 回看窗` 的切片。影響所有 PG 回測。
- **籌碼歷史缺口**：`institutional_daily`／`margin_daily` 2022~2025 幾乎空白
  （2023 年 0 天、2024 年 3 天）。雲端 pipeline 現在每天都有收，缺的是歷史。
- **rclone 共用 client_id 2026 年內退役**（OPS-archive §4 已註記），到期前要換自己的。

## 9. 環境（本機接手必讀）

```powershell
# 這個 worktree
cd D:\project\twquant\.claude\worktrees\archived-session-location-9474eb
git checkout claude/ml-t069-continue

uv sync --dev --all-groups          # lightgbm 在內
uv run --all-groups python -m twquant check    # 閘門（需 TWQUANT_PG_DSN）
```

- DSN 在 `D:\project\twquant\.env`（gitignore，**不得進 repo**）。本專案**不自動載
  .env**，跑 CLI 前要自己把環境變數帶進 shell。
- 本機 PG：docker `twquant-pg-1`（`localhost:5433`），健康、資料完整
  （broker_trades 5,566 萬列、factor_values 2,305 萬列、prices 910 萬列）。
- 模型與 research_log 落在**這個 worktree 的** `data/`（`data/models/`、
  `data/research_log/`）——`D:\project\twquant\data\` 底下**沒有** models／research_log，
  前一份交接文件 §6 假設的「本機累積的 trials 母體」其實不存在，
  DSR 的 trials 要從現在開始誠實累積。
