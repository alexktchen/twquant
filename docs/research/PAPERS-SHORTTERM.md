# PAPERS-SHORTTERM — 短線操作文獻總表（第二批研究）

2026-07-08 由 deep-research 多代理調查產出（5 搜尋角度 → 25 來源 → 116 claim → 對抗驗證）。
驗證中途撞到用量上限：**11 條 claim 完成 3 票對抗驗證、14 條僅完成來源抓取**；引用書目
另以人工搜尋逐篇補核。標記沿用 PAPERS.md 慣例：✓ 引用與關鍵數字已驗證、△ 存在但
細節有修正（以修正欄為準）、○ 來源已讀但未完成全數驗證——實作遇 ○ 級數字請先覆核。

範圍：(a) 盤中～次日（當沖/隔夜）、(b) 數日～兩週短線反轉。排除已在 PAPERS.md 的
Jegadeesh 1990、Lehmann 1990、Lee/Liu/Roll/Subrahmanyam 2004、Barber/Lee/Liu/Odean
當沖系列、Gervais/Kaniel/Mingelgrin 2001、Lee/Swaminathan 2000、Llorente et al. 2002、
Nagel 2012。

## A. 短期反轉的跨市場證據與驅動機制

| 狀態 | 文獻 | 關鍵發現 | 因子對應 |
|---|---|---|---|
| △ | Butt, Högholm & Sadaqat (2021), "Reversal returns and expected returns from liquidity provision: Evidence from emerging markets," *J. of Multinational Financial Mgmt* 59, 100664 | 21 個新興市場的 1 月期反轉報酬顯著為正且多模型 alpha 存活；集中在**小型/低流動性/高波動股**；**市場波動高時反轉報酬更高**（與公司特徵無關）；「主動投資人退出」多的股票反轉更強 → 支持流動性提供解釋。修正：搜尋初報 Kolari/2020，逐篇驗證為 Högholm/2021（DOI 10.1016/j.mulfin.2020.100664，3-0 票確認） | 現有 `price.reversal_5d` 延伸：與 size/turnover/波動交乘分層；加市場波動 regime 條件（台指已實現波動高 → 反轉權重加碼）。免費 OHLCV ✓ |
| ✓ | Da, Liu & Schaumburg (2014), "A Closer Look at the Short-Term Return Reversal," *Management Science* 60(3):658-674 | 把近期報酬拆成「基本面新聞」與「殘差」：**只有殘差部分會反轉**；過濾掉基本面驅動的漲跌後，風險調整後報酬是原始反轉策略的 **4 倍**（3-0 票逐字確認） | `price.residual_reversal_5d`：5 日報酬先對產業報酬（stock_meta 產業別可用）或月營收意外殘差化，再取負向。免費資料可近似 ✓（原文用分析師修正，台股可用營收 YoY 加速代理） |
| ✓ | Neszveda, Till, Timár & Varga (2022), "Is short-term reversal driven by liquidity provision in emerging markets? Evidence from China," *Finance Research Letters* 50:103220 | 中國短期反轉普遍存在，**但與流動性約束無關**——美式 Nagel 流動性提供解釋在散戶主導市場不成立 | 警示：波動/流動性條件化在散戶主導市場（台股類似）效果需實測，勿直接假設美式機制成立 |
| ✓ | Gao, Jiang, Xiong & Xiong (2023, rev. 2025), "Daily Momentum and New Investors in an Emerging Stock Market," NBER WP 31839（SZSE 帳戶級資料 2005-2019） | **日頻動能**：按前一日報酬排序的多空組合次日 +0.37%（年化 ~92%），第 2 日起反轉；**14/21 新興市場（含台灣）呈顯著正日動能**，已開發市場 16/21 反而日反轉；機制 = 新手散戶追漲（日周轉 18.12% vs 一般 8.03%/大戶 3.26%），其買入預測數週後反轉（多票逐字確認，含台灣點名） | `price.day_mom_1d`（前 1 日報酬，+1，持有 1 日）——周轉極高，先當研究/擇時 overlay 而非週頻 sleeve；「1 日續勢 → 2-5 日反轉」與現有 `reversal_5d`(−1) 相容。免費 OHLCV ✓ |

## B. 隔夜 vs 盤中報酬分解（台股可直接算）

| 狀態 | 文獻 | 關鍵發現 | 因子對應 |
|---|---|---|---|
| ✓ | Lou, Polk & Skouras (2019), "A Tug of War: Overnight versus Intraday Expected Returns," *JFE* 134(1):192-213 | 按上月**隔夜報酬**排序：次月隔夜 alpha **+3.47%/月**（t=16.8）、盤中 alpha **−3.02%/月**（t=−9.7）——同時段續勢、跨時段反轉，效果持續數年 | `price.overnight_ret_20d` = Σln(open/前日close)、`price.intraday_ret_20d` = Σln(close/open)。只需日 OHLC ✓（○ 級數字：驗證票未跑完，抓取代理已對 PDF 原文核過） |
| ○ | Ho, Hsiao, Lo & Yang (2023), "Momentum investing and a tale of intraday and overnight returns: Evidence from Taiwan," *Pacific-Basin Finance J.* 82:102151 | **台股直接證據**：盤中報酬動能（IMOM）顯著為正、隔夜報酬動能（OMOM）顯著為負，效果最長達一年；解釋：投資人對盤中資訊反應不足、對隔夜資訊過度反應 | 台股版雙因子：`price.intraday_mom`（+1）、`price.overnight_mom`（−1）。與 Lou et al. 方向一致，台股已複製 ✓ 免費 OHLC |
| ✓ | Bogousslavsky (2021), "The Cross-Section of Intraday and Overnight Returns," *JFE* 141(1):172-194 | 錯價因子白天賺、**收盤前虧**：套利者因隔夜保證金/借券費在尾盤減倉，錯價尾盤反而擴大 | 執行層指引：因子訊號的進出場時點避開尾盤效應；完整利用需盤中資料（旗標：非免費日頻可作） |
| ✓ | Berkman, Koch, Tuttle & Zhang (2012), "Paying Attention: Overnight Returns and the Hidden Cost of Buying at the Open," *JFQA* 47(4):715-741 | 隔夜正報酬→盤中反轉的型態**集中在近期吸引散戶注意的股票**（開盤價被散戶買盤推高）；開盤買進的隱性成本常超過半價差 | 條件化：對「高注意力」股（前日爆量/漲停/新聞股）做空隔夜強勢開盤。註意力代理 = 異常量+前日大漲。免費 OHLCV ✓ |
| ✓ | Lu, Malliaris & Qin (2023), "Heterogeneous liquidity providers and night-minus-day return predictability," *JFE* 148(3):175-200 | 理論+實證：特徵排序組合隔夜/盤中報酬**系統性反號**——開盤資訊不對稱高，快套利者索取大價差吸收失衡，盤中衰減 | 支持 night-minus-day 分解為一整類因子家族（機制檢驗需 HFT 資料，訊號本身免費 OHLC ✓） |
| ✓ | Baltussen, Da & Soebhag (2024), "End-of-Day Reversal," SSRN 5039009 | 美股**尾盤 30 分鐘反轉**：當日早段輸家在最後 30 分鐘可靠跑贏贏家（t>10）；驅動 = 散戶注意力買盤+空方尾盤風控 | 需盤中資料——**免費日頻不可作**，列為 T-016 級後續（若接入盤中快照可做尾盤擇時） |
| ○ | "Overnight returns and investor sentiment: Further evidence from the Taiwan stock market," *Pacific-Basin Finance J.* 80 (2023), S0927538X23001646（作者未逐篇核） | **台股**隔夜報酬造成短期持續+長期反轉，兩者皆由投資人情緒驅動 | 佐證 B 類因子在台股成立；情緒代理（融資/量能）可作條件變數。免費 ✓ |

## C. 散戶/當沖行為與訂單流（含台灣制度事件）

| 狀態 | 文獻 | 關鍵發現 | 因子對應 |
|---|---|---|---|
| ✓ | Boehmer, Jones, Zhang & Zhang (2021), "Tracking Retail Investor Activity," *J. of Finance* 76(5):2249-2305 | 週頻散戶淨買失衡正向預測次週報酬約 **+10bp**（美股 2010-2015，sub-penny 識別法） | 識別法美股限定；台股散戶失衡代理 = 融資變化、或(全市場成交−三大法人)淨額。方向注意：是**續勢**非反轉 |
| ✓ | Kelley & Tetlock (2013), "How Wise Are Crowds?" *J. of Finance* 68(3):1229-1265 | 散戶淨買 [1,20] 日累積預測力：市價單 35.6bp、限價單 29.8bp（年化多空 ~8.0%/7.1%），**無反轉證據**；市價單=資訊管道（預測財報意外）、限價單=流動性提供管道 | 打臉「無腦反著散戶做」：散戶失衡在美股是正向訊號。台股方向必須用 RankIC 實測定奪（與 STRATEGY §1-2 散戶虧損結論的張力來自台灣 vs 美國市場結構差異） |
| ✓ | Barber, Lin & Odean (2024), "Resolving a Paradox: Retail Trades Positively Predict Returns but Are Not Profitable," *JFQA* 59(6):2547-2581 | 悖論解答：等權失衡預測報酬，但散戶實際虧錢——買盤集中在注意力股；重散戶股的失衡多空**年化 −15.3%**、其餘股 +6.8% | 「散戶擁擠度」負向條件因子：融資增速 × 異常量交乘，剔除/反向重散戶注意力股。免費資料 ✓ |
| ○ | Chen, Ching-Tse (JMP ~2023-24), "The Economic Consequences of Lower Retail Trading Costs"（TWSE 帳戶級） | **台灣當沖降稅自然實驗**：2017/4/28 現股當沖證交稅 30bp→15bp（僅普通股當沖；自營商 2018/4/28 才適用）——乾淨的制度斷點 | 非因子、是**回測制度斷點**：當沖相關訊號（量比、日內波動）樣本應以 2017/4/28 分段 |
| ✓ | Cheng, Chen, Huang, Ni & Liang (2023), "The impact and profitability of day trading following the relaxation of day trading restrictions in Taiwan," *Heliyon* 9(4) | 2016/2/1 放寬現股當沖後**當沖量約 2.7 倍**；高 VIX 期間當沖獲利性上升 | 第二個制度斷點 2016/2/1；當沖強度（可用 TWSE 每日當沖統計）作市場狀態變數 |
| — | （既有集）Barber/Lee/Liu/Odean 當沖系列補充數字：TSE 1992-2006 當沖者平均**每日毛 −7bp / 淨 −23.9bp**（含手續費+30bp 證交稅），僅 1992 年例外 | 已在 PAPERS.md，此處僅補逐字數據 | 反散戶當沖流的結構性 alpha 佐證 |

**隔日沖分點缺口**：本輪未找到針對「隔日沖分點行為」的嚴謹學術論文（僅實務來源，已在
STRATEGY §3.1 黑名單清單 ○ 級）。最接近的是 Chen JMP 的當沖稅制研究。學術驗證仍缺,
維持 T-016 以 broker_trades 資料自行統計標籤的路線。

## D. 法人群聚短窗效應（台股）

| 狀態 | 文獻 | 關鍵發現 | 因子對應 |
|---|---|---|---|
| △ | "Individual and institutional herding and the impact on stock returns: Evidence from Taiwan stock market," *Int'l Review of Financial Analysis* 29 (2013):175-188（作者欄未在本輪核實，RePEc 確認題名/卷期） | 台股高頻資料：法人群聚強於散戶；**法人買群聚股其後 20 日跑贏賣群聚股 +1.009%**；散戶買群聚股其後 20 日 −0.829%；法人群聚集中在小型/低周轉股、正回饋 | 直接支持現有 `chip.trust_net_10d`/`foreign_net_10d` 的 10-20 日窗設計；散戶群聚負向 → 與 C 類散戶擁擠度因子互相印證 |

## E. 高周轉策略的成本/容量紀律（實作前必讀）

| 狀態 | 文獻 | 關鍵發現 | 因子對應 |
|---|---|---|---|
| ○ | Frazzini, Israel & Moskowitz, "Trading Costs of Asset Pricing Anomalies"（SSRN 2294498，~1 兆美元實盤成交 1998-2011） | **短期反轉是最吃成本的策略**：損益兩平基金規模僅 ~$9B（美）/$13B（全球），對照 size $103B/value $83B/momentum $52B | 短線因子回測**必須**過成本後閘門（LOOP.md 已有）；容量遠小於中長線因子 |
| ✓ | Novy-Marx & Velikov (2016), "A Taxonomy of Anomalies and Their Trading Costs," *RFS* 29(1):104-147 | 單邊月周轉 <50% 的異常多數扣成本後仍顯著；**高周轉異常多數不行**，但成本緩解設計（限大市值、緩衝帶換股）能救回部分 | 短線 sleeve 引入**緩衝帶（banding）換股**與參與率上限；周轉預算寫進策略 config |
| ✓ | De Groot, Huij & Zhou (2012), "Another look at trading costs and short-term reversal profits," *J. of Banking & Finance* 36(2):371-382 | 標準反轉被成本吃掉主因是極端分位被小型/高波動/高成本股主導；**限大市值宇宙後反轉扣成本仍獲利** | 台股短線反轉限制在 ADV 門檻以上宇宙（呼應 STRATEGY Sleeve C 的 ADV 紀律），與 A 類「反轉集中在小型股」發現正面衝突——**alpha 大但做不了 vs alpha 小但做得了**，用成本後 Sharpe 裁決 |

## 台灣制度斷點時間表（短線回測分段用）

| 日期 | 事件 | 影響 |
|---|---|---|
| 2016-02-01 | 放寬現股當沖標的 | 當沖量 ~2.7×（Heliyon 2023） |
| 2017-04-28 | 當沖證交稅 30bp→15bp（散戶） | 當沖占比躍升（Chen JMP） |
| 2018-04-28 | 降稅擴及自營商 | 第二段斷點 |

## 落地優先序建議（以現有免費資料）

**P0（純日頻 OHLCV，馬上可算）**
1. `price.overnight_ret_20d`（−1，隔夜累報，Lou/Polk/Skouras + Ho et al. 台股複製）
2. `price.intraday_ret_20d`（+1，盤中累報，同上）——與現有 `mom_20d` 相關性需檢查
3. `price.residual_reversal_5d`（−1，產業殘差化反轉，Da/Liu/Schaumburg）
4. `price.day_mom_1d`（+1，1 日持有——先進因子健檢看 RankIC 型態，未必進組合）

**P1（需交乘/條件化）**
5. 反轉 × 市場波動 regime（Butt et al.）；反轉限 ADV 宇宙（De Groot et al.）
6. 散戶擁擠度負向條件：`margin_velocity` × `vol_ratio` 交乘（Barber/Lin/Odean、Berkman et al.）

**需新資料（旗標）**
- 尾盤 30 分反轉、Bogousslavsky 尾盤效應 → 盤中資料
- 真散戶訂單失衡 → 逐筆/帳戶級（台股可用當沖統計+融資近似）
- 隔日沖分點 → broker_trades（T-016）

## 與現有因子庫的張力（實測前須知）

1. **散戶失衡方向**：美股文獻（Kelley/Tetlock、Boehmer et al.）是續勢正訊號,
   STRATEGY §1-2（台股散戶虧損）暗示負訊號——台股散戶結構不同,方向交給 RankIC 實測。
2. **日動能 vs 5 日反轉**:Gao et al. 說台灣有 1 日續勢,現有 `reversal_5d` 是 5 日反轉—
   —兩者相容(1 日續、2 日起反),但意味 `reversal_5d` 的視窗起點可實驗 t-1 剔除。
3. **反轉的甜蜜點矛盾**:alpha 集中在小型股(Butt et al.)但成本也集中在小型股
   (De Groot et al.)——一律以成本後指標裁決,參照 Sleeve C 的 ADV 3% 參與率紀律。
