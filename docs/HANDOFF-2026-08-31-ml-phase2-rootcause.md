# 交接：Phase 2 的 −0.0188 是缺陷產物，不是實驗結果（T-072 續）

> 前一份是 `docs/HANDOFF-2026-08-31-ml-phase2.md`。**那份的「下一步」已失效**：
> 它假設把 OOS 分數寫完就去跑零成本回測判 D-039 門檻，但那批分數 100% 是數值垃圾。
> 讀順序：本文件 → `docs/HANDOFF-2026-08-31-ml-phase2.md`（候選定義與已完成工作）
> → `SPEC-011`。
>
> 分支：`claude/handoff-2026-08-31-ml-phase-2-c618a1`（從 `main` 43b9ea9 開）
> 交接時點：2026-08-31 22:40
> 金庫期 2026-02-26~2026-08-28 **維持未開封**。本文件不含任何金庫期資料。

---

## 1. 一句話現況

`ml/dataset.py:112` 的 `_gross` 除法沒擋 `_entry <= 0`，2021 年 33 個交易日的
`open = 0` 壞列因此變成 `inf`，再被同行的當日市場均值擴散到整個橫斷面；
LightGBM 在 `±inf` 標籤上訓練，葉節點值到 `1e36` 量級，
**`lgbm_net_h20_20260225_9a575e97` 的每一個分數都是數值噪音**。
`oos_rank_ic = -0.0188` 與「16/16 全負」不承載任何關於 H20 成本感知標籤的資訊。
**H20 這個候選還沒有被真正測試過。**

## 2. 根因

### 2.1 缺陷位置

同一個函數 `cost_adjusted_return_labels` 內，成本路徑有正值防護、報酬路徑沒有：

```python
# dataset.py:87 — 成本路徑，有擋
if entry <= 0.0 or exit_ <= 0.0 or adv <= 0.0:
    return None

# dataset.py:112 — 報酬路徑，沒擋
(pl.col("_exit") / pl.col("_entry") - 1.0).alias("_gross")

# dataset.py:117 — 把單列汙染擴散到當日整個橫斷面
(pl.col("_gross") - pl.col("_gross").mean().over("date")).alias("_excess")
```

`train.py:50` 只濾 `pl.col("label").is_not_null()`；`-inf` **不是 null**，直接進 LightGBM。

上游 `data/serving/ml_store.py` 的 `read_prices_training` 只濾 `open IS NOT NULL`，
沒有 `open > 0`。而 `schemas.py:45` 把 `open` 定義為 `_nonneg`（`Check.ge(0)`），
所以 `open = 0` 在本專案的契約下是**合法值**——這條路徑上沒有任何一關會攔它。

### 2.2 證據鏈（每一環都已實測，非推論）

| 環節 | 實測結果 | 驗證方式 |
|---|---|---|
| 觸發條件存在 | `open = 0` 共 **955 列**；四位數普通股宇宙 **497 列** | `select count(*) from serving.prices_daily where open = 0` |
| 汙染日數 | **33 個交易日**，2021-01-04 ~ 2021-02-26 | 同上加 `count(distinct date)` |
| 擴散 | 該日**整個橫斷面**變 `-inf` | `mean().over("date")` |
| 過濾未攔截 | `-inf` 不是 null，通過 `is_not_null()` | `train.py:50` |
| 模型被毀 | tree 0 葉節點 **−5.99e36** | `grep -m1 '^leaf_value=' model.txt` |
| 分數全毀 | **100.00%** 的 `\|score\| > 1e10`，std **1.21e36** | 讀 `oos_scores.parquet` |

複現指令（唯讀，不改任何狀態）：

```powershell
$env:TWQUANT_PG_DSN = "<從 .env 載入，勿輸出>"
.\.venv\Scripts\python.exe -c @'
import polars as pl
df = pl.read_parquet("data/models/lgbm_net_h20_20260225_9a575e97/oos_scores.parquet")
s = df["score"]
print("rows", df.height, "std %.3e" % s.std())
print("frac |score|>1e10:", df.filter(pl.col("score").abs() > 1e10).height / df.height)
print("label min/max", df["label"].min(), df["label"].max())
'@
```

預期輸出：`rows 1719847`、`std 1.212e+36`、`frac ... 1.0`、
`label min/max -1.9723 67.9537`。

### 2.3 為什麼是 16/16 全負，而不是隨機散開

汙染日**全部落在 2021 年**，而開發期 OOS 是 **2022-02-08 ~ 2026-02-25**。
在 expanding-window 切分下，2021 這段永遠屬於**訓練區塊**，從不進入任何一個窗的測試集。
因此：

- 16 個 booster **共用同一批毒訓練資料**，不是 16 次獨立抽樣，退化結構相同，
  逐窗 Spearman 的符號自然一致——這就是「16/16 全負」的來源。
- `oos_scores.parquet` 的 `label` 欄**乾淨**（−1.97 ~ 67.95，無 inf、無 NaN），
  因為測試集沒有汙染日。**汙染在儲存的輸出裡完全看不見**，只能從
  `score` 的量級或 `model.txt` 的葉節點值察覺。

這一點很重要：任何只檢查輸出檔 label 欄的健檢都抓不到這個缺陷。

## 3. 對 D-039 / T-072 的意義

- `oos_rank_ic = -0.0188` **不是有效讀數**。用它判斷「H20 成本感知標籤行不行」
  在統計上沒有內容。
- 已停止的零成本回測**不應該再跑**在這批分數上。跑出來無論通過或未達
  Sharpe 0.5 門檻，都不構成對候選的證據。
- `ml:net_h20` 這個 trial 是否該計入 DSR 母體，需要裁決（見 §7 的 D-040）。
  傾向：缺陷產生的 run 不是對假設的檢定，計入會讓多重檢定懲罰失真；
  但 D-038/D-039 的既有立場是「保守計入」。**由使用者裁決，不得自行決定。**

## 4. backfill 已停止且完整回滾（已驗證）

- 程序已不存在；孤兒 PG backend 亦已終止。
- `select count(*) from serving.ml_scores where model_id = 'lgbm_net_h20_20260225_9a575e97'`
  → **0**。沒有半套分數。
- 兩組 Phase 1 分數完好：`lgbm_h10_20260225_2f5a21a9` 1,737,542 列、
  `lgbm_h10_20260115_2f5a21a9` 444,382 列。`serving.ml_scores` 458 MB。
- 前一份提到的 `upsert_ml_scores` 逐列 `executemany` 過慢仍然成立，
  但**修好標籤之前不需要處理它**——分數要重算。

## 5. 修復清單（都是缺陷修正，不是改方向）

依 CLAUDE.md §2「先寫測試」的順序：

1. **先加會紅的測試**：餵一列 `open = 0` 給 `cost_adjusted_return_labels`，
   斷言該日標籤有限（或整批 raise）。現有 `tests/test_ml_cost_aware.py`
   **沒有任何一條**能抓到這個失效模式（審查確認，見 §6）。
2. **`dataset.py:112`**：比照 `:87`，`_entry <= 0` 時產出 null 而非 `inf`。
   注意 null 保留、不得填 0（黃金規則 5）。
3. **`ml_store.py` 的 `read_prices_training`**：`open = 0` 屬於壞資料，
   依黃金規則 5 應 **raise 並帶上下文**（哪些日期、哪些股票），不是靜默流下去。
   這是把防護放到正確的層級——`dataset.py` 那層只是最後一道網。
4. **考慮加一條標籤健檢**：訓練前斷言 label 全為有限值，否則 raise。
   這會把整類「數值汙染」缺陷變成機器可攔。
5. 修好後重跑 `ml train`，得到的才是這個候選的**第一個真實 IC 讀數**。
   它可能仍是負的、可能接近零、也可能是正的——現在無從得知。

## 6. 審查的其他發現（40 項確認、3 項被駁回）

完整清單見本次工作的審查輸出。critical 6 項、high 9 項，以下是**不在 Phase 2 範圍
但會影響其他工作**的幾項，各自另立任務、不要夾帶進修復：

- **`config.py:123` — Phase 1 artifact 無法重現（critical）**：新增的三個
  `MlLabelConfig` 欄位改變了 `MlConfig.model_dump()`，因而改變**未修改的**
  `configs/ml.yaml` 的 `config_hash` 與 `make_model_id()`。
  本次提交自己在 `configs/strategies/ml_rank.yaml` pin 的
  `lgbm_h10_20260225_2f5a21a9` 已無法由預設路徑重新產生。
  這同時使 SPEC-011 §3「維持向下相容」與 AC-6 的敘述不成立。
- **`strategy/build.py:88` / `factors/ml.py:26` — pin 的 model_id 查無資料會靜默退化（high）**：
  回傳空 frame → 零成交回測，與「策略當天沒訊號」無法區分。
  這與 D-037 修掉的宇宙全空是同一類缺陷，應 raise。
- **`dataset.py:92` — `participation` 無上限（high）**：
  `position_notional_twd / adv` 沒有封頂，滑價項 `sqrt(participation)` 也沒有 clamp，
  薄量股的估計成本可超過 100%（adv=1 TWD 時達 354%），
  而回測引擎根本不會成交這種單。標籤與它要模擬的引擎不一致。
- **`test_ml_cost_aware.py:111` — AC-3 測試自我抵消（critical）**：
  `position_notional_twd=50.0` 對價格 100.0 使 `shares=0`，
  該測試的「全 null」斷言至少有三個獨立成因，拿掉 ADV 窗防護測試仍全綠。
- **`factors/ml.py` 新薄因子未呼叫 `assert_no_lookahead`（high）**：直接違反 CLAUDE.md §4。
- **`cli.py:2067` — 負 IC 被當成綠燈**：`ml train` 對 OOS RankIC 的符號與逐窗分布
  沒有任何 guard，反向預測自身標籤的模型照樣 exit 0、印成功、存 artifact、登記 trial。
  這正是本次缺陷能一路走到交接文件的原因。

被駁回的 3 項（不必再追）：「rank 轉換遺失導致單列汙染整個橫斷面」被更精確的
§2 取代；「embargo 差一天」與「單一 null amount 毀掉 20 個 session」經查不成立。

## 7. 待裁決（D-040 草案，已寫入 DECISIONS.md）

修復本缺陷是否受 D-039「不得改 direction、不得反轉分數、不得換特徵、
不得調 LightGBM、不得增加 trial」的凍結約束？以及修復後重跑算不算新 trial？
選項與影響見 `DECISIONS.md`「待裁決」段落。**裁決前不要動 `dataset.py`。**

## 8. 環境雜項

- **殭屍程序**：PID 31368 / 20308 的
  `serving materialize --from 2020-01-01 --to 2020-12-31` 自 2026-08-29 23:20 起
  掛了 47 小時，CPU 累計 **0.03 秒**、記憶體 3 MB，完全沒有進展；
  並在 PG 上持有一個 **1 天 23 小時未關閉的 transaction**
  （`SELECT * FROM serving.prices_daily`），會擋住 vacuum 並累積膨脹。
  `docs/HANDOFF-2026-08-30-data-factors-ml.md` §1B 已記載 2020 年因子回補
  「已全數完成（27.2 分鐘）」，故此程序是被取代的殘留，可安全清除。
- 本機 PG：docker `twquant-pg-1`（`localhost:5433`）。
  DSN 在 `D:\project\twquant\.env`（gitignore，**不得進 repo**），本專案不自動載入。
- `data/models/`、`data/research_log/` 在 `/data/` gitignore，只存在本機。
- `cloudDailyReport/` 是無關的未追蹤資料，不要加入。

## 9. 視覺化

兩代模型的完整對照（逐窗 RankIC 鏡像、逐年 IC、十分位單調性、成本組成、
根因證據鏈）已做成互動報告，資料全部取自本 repo 的提交內容：
<https://claude.ai/code/artifact/86559a68-e0be-4be5-b931-e2a477d7f0b5>
