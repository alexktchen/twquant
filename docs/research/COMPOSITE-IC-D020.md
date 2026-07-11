# 綜合分（composite_daily）預測力研究 — D-020 / T-038

**日期**：2026-07-11　**任務**：T-038　**裁決條目**：[DECISIONS.md](../../DECISIONS.md) D-020
**程式碼版本**：worktree `stock-factor-dashboard-prd-bf0024`（21 註冊因子）
**資料**：`D:\project\twquant\data` 資料湖，`prices_daily` 涵蓋 2020-01-02 ~ 2026-07-09（941 交易日）

## 0. 一句話結論

生產綜合分 `composite_daily.signed_z_mean`（全註冊因子 signed_z 等權平均）**實證無橫斷面
預測力**（5 日 IC=−0.0072、20 日 IC=−0.0016，樣本外 t≈0）。原因是動能/量能因子在台股
的實證 IC 與其宣稱 direction 相反、與反轉/營收因子的正 IC 互相抵銷。**選項 B（縮減為
實證有效子集）在樣本外最穩健**（兩個 horizon 皆過 D-006 promote 門檻 t≥3），**選項 A
（滾動 IC 加權）在 20 日樣本外完全崩潰**（過擬合），**選項 C（前端標示）應無論如何先行**。

## 1. 方法

- **PIT 安全性**：因子為因果函數（`assert_no_lookahead` 保證 `f(data)[:t] == f(data[:t])`），
  故用 `Warehouse.view(END)` 一次算全期、再切分析窗，等價於逐日 PIT，且遠快於逐日重算。
- **宇宙**：四碼普通股 `^[1-9][0-9]{3}$`，`close > 0`。
- **標準化**：`standardize`（winsorize_mad k=5 → 逐 (date,factor) z-score），與生產
  `materialize.py` 完全同路徑；`signed_z = z × direction`。
- **RankIC**：`factors/ic.py` 的 `rank_ic_series`（逐日 Spearman，橫斷面 n≥5 才計）。
  前向報酬 `forward_returns` 用 raw close shift（見 §5 caveat）。
- **樣本切分**：樣本內 IS = 2023-01-01 ~ 2025-06-30；樣本外 OOS = 2025-07-01 ~ 2026-07-09。
- **視窗**：horizon ∈ {5, 20} 交易日。
- **選項實作**：
  - **Baseline（生產）**：全因子 signed_z 等權平均。
  - **B**：有效子集 signed_z 等權平均。兩版——(a) 預設候選（短反轉＋殘差反轉＋月營收 YoY，
    使用者提案 + STRATEGY.md §1 先驗）；(b) 資料驅動（IS |t|≥2 且 signed IS/OOS 同為正）。
  - **A**：滾動實證 IC 加權。權重 `w_i(t) =` 因子 i 於**已實現**過去 252 交易日的 h 日
    RankIC 滾動均值（day d 的 IC 於 d+h 才可知，故對訊號日 t 只用 idx ≤ t−h 的 IC，
    `shift(h)`）。組合 `= Σ w_i·z_i / Σ|w_i|`（用未 signed 的 z，IC 符號自帶方向）。PIT 安全。

## 2. 單因子 RankIC（signed 後；正 = 宣稱方向正確）

signed_t = direction × raw_z 的 IC t-stat。**負值＝該因子在台股與宣稱方向相反**。

### horizon 5d

| 因子 | dir | signed_t 全期 | signed_t IS | signed_t OOS | 判讀 |
|---|---:|---:|---:|---:|---|
| volume.turnover_z_20d | +1 | **−9.10** | −7.25 | −6.00 | 量能，實證反向 |
| price.mom_20d | +1 | **−7.58** | −7.29 | −2.37 | 動能，實證反向 |
| price.rs_market_20d | +1 | **−6.57** | −6.27 | −2.37 | 相對強弱，反向 |
| chip.inst_net_10d | +1 | **−5.89** | −0.73 | −6.09 | 法人合計，反向 |
| volume.vol_ratio_5d | +1 | **−5.48** | −4.40 | −3.63 | 量比，反向 |
| price.mom_60d | +1 | **−5.11** | −6.01 | −0.18 | 動能，反向 |
| price.high_52w | +1 | **−4.67** | −3.04 | −4.11 | 52 週高，反向 |
| chip.foreign_net_10d | +1 | −2.55 | −0.11 | −2.72 | 外資，反向 |
| price.intraday_ret_20d | +1 | −1.55 | −0.47 | −2.28 | 弱 |
| chip.short_margin_ratio | +1 | +1.20 | −2.32 | +2.11 | 不穩 |
| chip.margin_velocity_5d | −1 | +2.42 | +2.11 | +1.78 | 融資增速反向（弱正） |
| price.vol_20d | −1 | **+4.20** | +3.85 | +1.91 | 低波，正 |
| fundamental.revenue_accel | +1 | **+5.56** | +5.16 | +2.17 | 營收加速，正 |
| price.overnight_ret_20d | −1 | **+6.42** | +8.02 | −0.35 | 隔夜反轉，IS 強 OOS 崩 |
| fundamental.revenue_yoy | +1 | **+9.17** | +7.30 | +5.57 | 月營收 YoY，**最穩** |
| price.reversal_5d | −1 | **+12.26** | +10.33 | +6.85 | 短反轉，**強** |
| price.residual_reversal_5d | −1 | **+13.88** | +11.43 | +8.61 | 殘差反轉，**最強** |
| chip.foreign_streak / trust_net_10d / trust_streak / price.mom_1d | | nan | nan | nan | 無覆蓋（見 §5） |

**5d 統計**：顯著反向（t<−2）8 個因子 vs 顯著正向（t>2）7 個 → 等權相抵。

### horizon 20d（摘要）

同型態：`price.high_52w`（−8.10）、`chip.inst_net_10d`（−6.62）、`price.mom_20d`（−4.79）
仍顯著反向；`fundamental.revenue_yoy`（+10.36）、`price.residual_reversal_5d`（+9.23）、
`price.reversal_5d`（+6.50）、`price.vol_20d`（+4.56）顯著正向。動能在 20d **樣本外**部分翻正
（`mom_60d` OOS +4.32），顯示動能符號本身不穩、隨 regime 變號——這正是等權綜合分無力的深層原因。

## 3. 綜合分 RankIC 對照（核心結果）

IC = 平均 RankIC；t = 時序 t-stat。D-006 門檻：初篩 |t|≥2、上線 |t|≥3。

### horizon 5d

| 方案 | 全期 IC / t | IS IC / t | **OOS IC / t** |
|---|---|---|---|
| **Baseline 等權（21 因子）** | −0.0072 / −1.89 | −0.0082 / −1.68 | **−0.0047 / −0.88** ✗ 無力 |
| B 預設子集（reversal_5d, residual_reversal_5d, revenue_yoy） | +0.0639 / +14.32 | +0.0676 / +11.83 | **+0.0549 / +8.58** ✓ promote |
| B 資料驅動子集（+margin_velocity, revenue_accel, vol_20d） | +0.0626 / +16.30 | +0.0670 / +13.76 | **+0.0520 / +9.03** ✓ promote |
| A 滾動 IC 加權（win=252） | +0.0678 / +13.69 | +0.0772 / +12.03 | **+0.0457 / +6.78** ✓ promote（IS→OOS 衰減 41%） |

### horizon 20d

| 方案 | 全期 IC / t | IS IC / t | **OOS IC / t** |
|---|---|---|---|
| **Baseline 等權（21 因子）** | −0.0016 / −0.46 | −0.0024 / −0.55 | **+0.0005 / +0.09** ✗ 無力 |
| B 預設子集 | +0.0394 / +9.72 | +0.0447 / +8.55 | **+0.0256 / +4.88** ✓ promote |
| B 資料驅動子集 | +0.0394 / +10.68 | +0.0477 / +10.36 | **+0.0177 / +3.24** ✓ promote |
| A 滾動 IC 加權（win=252） | +0.0365 / +7.71 | +0.0514 / +8.63 | **+0.0003 / +0.04** ✗ **完全崩潰** |

## 4. 判讀與建議

1. **發現成立**：等權綜合分樣本外 t≈0（5d −0.88、20d +0.09），**無預測力**，忠實重現使用者
   回報（5d≈−0.009、20d≈−0.004）。機制＝動能/量能實證反向、與反轉/營收正 IC 抵銷。
2. **B 最穩健**：預設子集與資料驅動子集在**兩個 horizon 樣本外皆過 promote 門檻**
   （5d t≈8–9、20d t≈3–5），且 IS→OOS 衰減溫和。子集非純資料探勘——短反轉、殘差反轉、
   月營收 YoY 皆有 STRATEGY.md §1 明確先驗（台股反轉主導、月營收 PEAD）。
3. **A 風險高**：5d 樣本外雖過門檻（t=6.78）但衰減達 41%；**20d 樣本外從 IS t=8.63 崩到
   t=0.04**——滾動 IC 估計在長 horizon 噪音大、隨 regime 反覆，典型過擬合。A 引入的估計
   機制未換來穩健性，反在 20d 全數蒸發。
4. **建議**：採 **B（實證有效子集）作為綜合分重定義** + **C（前端明確標示）無論如何先行**；
   **不建議 A**。若日後要走加權路線，應以更強的收縮/貝式框架、更長樣本、且以 walk-forward
   （T-019 `walk_forward`）逐窗驗證 promote，而非固定 252 窗直接上線。

## 5. Caveats（誠實揭露）

1. **21 vs 19 因子**：worktree 註冊 21 個因子；使用者稱 19，差異來自
   `chip.foreign_streak / chip.trust_net_10d / chip.trust_streak / price.mom_1d` 在分析窗
   **IC 全 NaN**（下述覆蓋缺口 + mom_1d 橫斷面近退化），實際貢獻綜合分者約 17–18 個。
   多算的 `overnight_ret_20d`（+）、`residual_reversal_5d`（+）使 21 因子等權 IC（−0.0072）
   較 17 因子版（−0.015）更接近 0——結論（無力）不變。
2. **法人/融資券覆蓋缺口**：`institutional_daily` / `margin_daily` 分區在 **2023–2024 為 0**、
   僅 2022 與 2025–2026 有（法人 190 日、融資券 167 日）。故 chip 因子在分析窗大多缺席，
   綜合分實質由 price/volume/fundamental 主導；但納入的 chip 因子（inst_net/foreign_net）
   本身也 signed 為負（反轉型），其加入不會救回綜合分。生產近端（2025–2026）chip 因子在場，
   綜合分近端行為可能與全窗略異——建議上線前於 2025–2026 子期複核。
3. **未還原價**：`prices_daily.close` 為 raw（還原價 T-011 待補），除息日附近動能/反轉會失真。
   與生產因子同基礎，故本比較公平；但 IC 絕對量帶此 caveat。
4. **A 為單一實作選擇**：252 窗、IC 大小加權、`Σ|w|` 正規化為其中一種；其他變體（短窗、
   純符號、收縮）未測。20d 崩潰足證 A 家族過擬合風險，但不代表所有 A 變體皆不可行。

## 6. 重現

```bash
# 於主 repo，PYTHONPATH 指向 worktree src（含 21 因子），資料用主 repo 資料湖
cd D:\project\twquant
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONPATH="D:\project\twquant\.claude\worktrees\stock-factor-dashboard-prd-bf0024\src"
.venv\Scripts\python.exe <scratchpad>\research_d020.py
```

腳本為一次性研究工具（非生產程式碼、不入 `src/`）；如需納入 repo 應改寫為
`reporting/` 下的離線分析並補測試（見 T-038 後續）。
