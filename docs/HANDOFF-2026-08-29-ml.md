# 交接：ML Phase 1（T-065~T-069）與 claude/data-running-check-8b2zil 分支

日期：2026-08-29 · 交接來源 session「資料有跑嗎」（`session_01LJi9REPkiyjHhXb3Ynfasx`）

> ⚠ **本文件的前提已過時（2026-08-29 稍晚更新）**：下方「沒有 PR 承接、尚未進 main」
> 的敘述在 **PR #60 合併後不再成立**——那批 commit 已進 main。
> 另外，第 3 級「保留」的原因之一（smoke 回測 0 成交）**已查明根因**，
> 見 `DECISIONS.md` 的 **D-037**：衛生濾網把農曆年全市場休市誤判為資料事件，
> 使回測前 58/233 個交易日宇宙全空。這改變了第 3 級數字的讀法（CAGR 係在
> 175 個有效日內取得、周轉以有效期重算約 2,423%），**第 3 級需重跑**。
> 接手請以 **`docs/HANDOFF-ml-t069.md`** 為準，本文件僅供追溯。

一句話現況（原文，已過時）：**T-065~T-068 已完成、T-069 評估階梯第 1-3 級已通過
（第 3 級保留），但這批工作的 20 個 commit 全部只存在於
`claude/data-running-check-8b2zil` 分支上，沒有 PR 承接，尚未進 main。**

---

## 1. 為什麼需要這份文件

原 session 於 2026-08-29 01:25 開始，任務是「daily-pipeline 8/27-8/28 沒跑」。
該問題已於 PR #58 修復並合併進 main（merge commit `1b6f859`，08-29 10:39 台北）。
**但 PR 合併之後，同一個 session 繼續在同一條分支上做完了整個 ML Phase 1**
（02:41 → 12:45 UTC，20 個 commit），而已合併的 PR 無法再承接新 commit，
於是這批工作懸在分支上沒有出海口。

原 session 的逐字對話無法匯出（沒有任何工具可讀取他人 session 的 transcript），
但該 session 的紀律良好：**所有結論都已寫進 repo 的持久紀錄**
（TASKS.md 完成註記、CHANGELOG.md、SPEC-010、docs/ML-local-train.md）與兩份
artifact。本文件是這些來源的索引與現況彙整，不是新的結論。

## 2. 座標

| 項目 | 值 |
|---|---|
| 分支 | `claude/data-running-check-8b2zil`（head `17f71fd`） |
| 相對 main | 領先 20 commit、落後 1（＝PR #58 的 merge commit）；`git merge-tree` 測試**零衝突** |
| PR | **無**。唯一的 PR #58 已 closed/merged，內容只到 T-068 為止 |
| 差異規模 | 15 檔、+831 / −290 |
| 原 session | `session_01LJi9REPkiyjHhXb3Ynfasx`（title「資料有跑嗎」，iOS 起始，PENDING/disconnected） |
| Artifact 1 | 台股 ML 研究藍圖 — <https://claude.ai/code/artifact/dffda522-9170-4cee-9552-5def67072e07> |
| Artifact 2 | 首訓成績單（含所有圖表） — <https://claude.ai/code/artifact/8742d44b-2e0f-4350-88bf-81b2c6333c49> |

### 權威紀錄在哪（接手先讀這幾份，不要重讀 commit log）

1. `TASKS.md` 完成紀錄 — T-065 / T-067 / T-068 / T-069 各級的完整實測數字與保留事項
2. `docs/specs/SPEC-010-ml.md` §0（研究軌/生產軌執行環境分工，使用者裁決）、§2（訓練可用期間）
3. `docs/ML-local-train.md` — 本機訓練/評估/回測的逐步 runbook（Windows/PowerShell）
4. `CHANGELOG.md` — 2026-08-29 六條
5. 上表兩份 artifact — 視覺化成績單與方法論藍圖

## 3. 已完成（在分支上，尚未進 main）

| 任務 | 狀態 | 產出 |
|---|---|---|
| T-065 資料深度審計 | done | SPEC-010 §2 回填；`scripts/audit_data_depth.py` |
| T-066 資料集組裝＋切分器 | done（已於 #58 進 main） | `ml/dataset.py`、`ml/splits.py` |
| T-067 訓練/推論 CLI | done | `ml train` / `ml score`、首訓實測通過 |
| T-068 ml_scores 合約＋薄因子 | done（已於 #58 進 main） | `factors/ml.py`、`ml_store.upsert_ml_scores` |
| T-069 評估階梯 | **in_progress** | 第 1-3 級通過（第 3 級保留）；第 4-5 級未動 |
| T-070 每日推論接入 | blocked | 等 T-069 部署裁決 |

### T-069 實測結果（樣本外，233 個交易日 2025-02-05~2026-01-12）

第 1 級 — RankIC 三方對照（43.8 萬筆共同樣本、inner join 同宇宙）：

| | RankIC | IR | 勝率 | NW t | 分位頭尾 | 嚴格單調 |
|---|---|---|---|---|---|---|
| ML（LightGBM） | +0.0815 | 0.975 | 87.6% | 6.76 | +0.0860 | 是 |
| 基準A 全因子等權 | −0.0183 | −0.168 | 41.2% | −1.09 | −0.0209 | 否 |
| 基準B D-020 有效子集 | +0.0369 | 0.375 | 70.0% | 3.15 | +0.0380 | 否 |

判定取最強基準 B：ML 勝出 +0.0446（2.2 倍）。第 2 級（分位單調）通過。

第 3 級 — 成本後回測（含 0.3% 證交稅、漲跌停、跳價拒單）：

| | CAGR | Sharpe | MDD | 年化單邊周轉 | 成交筆數 |
|---|---|---|---|---|---|
| ML `ml_rank` | +23.71% | +1.27 | −17.68% | **1820%** | 814 |
| 對照 `reversal_revenue` | −8.90% | −0.42 | — | 2659% | — |

模型：`lgbm_h10_20260112_2f5a21a9`（25 特徵、4 窗、train_end 2026-01-12、金庫期未動）。
決定性（AC-4）於真實資料實測通過：獨立重跑重現同一 model_id 與 IC 至小數第 10 位。

## 4. 未完成 — 接手清單（依優先序）

1. **開 PR 把 20 個 commit 併進 main**。零衝突，可直接開。順帶效果：分支上刪掉了
   `audit-data-depth.yml`、`backfill-0827-0828.yml`、`ml-first-train.yml` 三個一次性
   workflow，而這三檔隨 PR #58 進了 main 至今仍在（244 行死程式碼）——合併即清除。
   注意：`ci.yml` 只在 `pull_request` 觸發，**這 20 個 commit 從未跑過 CI**
   （commit 訊息宣稱本機閘門 384 passed 全綠，但無 CI 紀錄背書），開 PR 即補驗。
2. **T-069 未結項：一個月 smoke 回測 0 成交／0 周轉，全期卻有 814 筆成交**。
   尚未查明。不推翻全期結果（全期自洽），但部署前必須解釋清楚。
3. **T-069 未結項：年化單邊周轉 1,820%**（約 13 個交易日持有，與 10 日標籤同構，
   屬結構性而非設定失誤）。成本已扣仍賺，但淨值對費率假設極敏感。降周轉手段
   （分數 EMA 平滑、buffer-zone 換股）**都要另行驗證並計入 DSR 的 trials 預算**，
   不得事後挑最好看的參數。
4. **T-069 第 4 級 DSR**（trials 取 `research_log` 登記數）與**第 5 級金庫期最終評估**
   （最後 6 個月凍結、只打開一次）——皆未動。**四級全過才進部署裁決，現在仍不得部署。**
5. **daily-pipeline 的修法尚未被真實班次驗證**。8/29 是週六，最後一次排程 run 是 #47
   （8/28，紅）。下一次真實驗證要等 **8/31（一）**。
6. 另立任務的技術債（原 session 已標註，未開任務）：
   - `universe_ids` 每個訊號日對整張 `prices_daily`（907 萬列）重算 — 所有 PG 回測共通的
     效能問題，正解是逐訊號日只取 `signal_date − 回看窗` 的切片。
   - `factor_values` 歷史洞：2020-05~12、2021-03~2022-12 整段缺失。補洞後訓練期可從
     3.5 年擴到 6.5 年，**結果可能改變**。
   - 富邦分點系因子物化僅 2026 起（raw `broker_trades` 有 2022 起），歷史 chip 回補。

## 5. 踩過的雷（別重蹈）

- **研究軌一律跑本機，不要再送上 GHA**。SPEC-010 §0 已由使用者裁決定案。實測教訓：
  在 GHA 跑回測三次皆敗——runner 記憶體被砍、容器 `/dev/shm` 僅 64 MB 使 VACUUM 失敗、
  以及為繞過限制而引入的修剪步驟本身。且每次實驗前固定付出「下載 1.4 GB dump + 還原」
  約 10 分鐘。一次性 GHA workflow 只是權宜，不得沉澱為常態路徑。
- **回測前修剪 `prices_daily` 的手法是脆弱的**（僅為繞過 GHA 記憶體而生）。經逐項驗證
  不影響準確度，但保留窗只餘約 18 個交易日邊際，以曆日換算交易日遇長年假就會失效。
  本機不需要這一步。細節見 TASKS.md 該條完成註記。
- 首訓五次失敗的根因依序是：整表拉 2,300 萬列 OOM → 改逐因子 server 端過濾；
  標籤宇宙含權證 → 四碼普通股過濾；`factor_values` 歷史洞 → 逐月密度判定；
  判洞閾值 <15 誤殺春節月（2026-02 僅 12 交易日）→ 改 <10。
- 逐日 IC 的樸素 t=14.87 **不可用**（10 日標籤在相鄰交易日重疊，實測一階自相關 0.72）。
  一律採 Newey-West 校正後的 t=6.76。
- 基準不可挑軟柿子：全因子等權正是 D-020 已裁決「無預測力」的 composite，打贏它
  不算數。判定一律對**最強基準**。

## 6. 怎麼接手

```bash
# 1) 取得分支
git fetch origin claude/data-running-check-8b2zil
git checkout claude/data-running-check-8b2zil

# 2) 確認基線是綠的（CLAUDE.md §7）
python -m twquant check

# 3) 本機重現 T-069 的第 1-3 級：照 docs/ML-local-train.md 走
#    §3 訓練 → §4 ml eval 基準對照 → §5 backfill-scores + 成本後回測
```

`docs/ML-local-train.md` 已含完整指令（含 `TWQUANT_PG_DSN` 設定、訓練區間確認的
SQL、`ml backfill-scores` 與兩支對照回測），不在此重複。

## 7. 這份交接拿不到的東西

原 session 的逐字對話。目前沒有工具能讀取另一個 session 的 transcript，該 session
也不在可用 `SendMessage` 的名單上（雲端、已斷線）。若需要原始對話，只能由使用者
自己在 claude.ai/code 打開該 session 檢視。

不過該 session 的**結論**沒有遺失——它把每一項發現都寫進了 TASKS.md 的完成註記與
CHANGELOG，粒度細到根因與棄用理由。§2 的五個來源合起來就是完整的技術交接。
