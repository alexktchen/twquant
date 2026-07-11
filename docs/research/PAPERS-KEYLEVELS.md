# PAPERS-KEYLEVELS — 價量關鍵價位文獻總表（第三批研究）

2026-07-11 單代理網路調查產出（4 搜尋角度 → 逐篇核對出處/卷期，**未跑多票對抗驗證**）。
標記沿用 PAPERS.md 慣例：✓ 出處與關鍵發現已核、△ 存在但細節有修正、○ 來源已讀但
數字未完成驗證——實作遇 ○ 級數字請先覆核。

範圍：「價格×成交量的關鍵價位」三條研究脈絡——(A) 行為錨定與未實現損益（可直接
因子化）、(B) 支撐/壓力價位的直接統計檢驗、(C) Volume/Market Profile 實務。
排除已在 PAPERS.md 的 George/Hwang 2004（52 週高）、Lee/Swaminathan 2000、
Gervais/Kaniel/Mingelgrin 2001（高量溢酬）、Brock 1992/Sullivan 1999/Bajgrowicz 2012
（技術規則總檢驗）。

## A. 行為錨定與未實現損益（核心——「量堆在哪些價位」的學術形式化）

| 狀態 | 文獻 | 關鍵發現 | 因子對應 |
|---|---|---|---|
| ✓ | Grinblatt & Han (2005), "Prospect theory, mental accounting, and momentum," *JFE* 78(2):311-339（前身 NBER w8734） | 提出 **CGO（capital gains overhang）**：以周轉率加權的歷史價格估全體持有人參考成本 R，CGO=(P−R)/P。高未實現獲利股後續報酬較高；CGO 進迴歸後**動能效應消失**——動能的行為真身。原文用週頻、5 年回看 | `price.cgo_250d`（STRATEGY §4a）。權重 ω 即「目前流通股最後一次成交在該日」的機率——**volume profile 的機率化版本** |
| ✓ | An (2016), "Asset Pricing When Traders Sell Extreme Winners and Losers," *RFS* 29(3):823-861（前身 "The V-shaped Disposition Effect"） | **V 型處分效果**：未實現獲利與損失幅度愈大、淨賣壓愈強（獲利端強度約為損失端 **5.9 倍**）；把 overhang 拆成 gain/loss 兩段，**同時有大 gain 與大 loss 的股票下月報酬更高**（策略月 alpha 0.5–1%、Sharpe≈1.5）；**吃掉 CGO 效應**；損失端的預測方向與動能相反 | `price.gain_overhang_250d`(+1)、`price.loss_overhang_250d`(−1)。**公式細節（週頻/跳過近期窗）實作時以刊出版 §2 為準**（工作論文 PDF 離線無法解析，僅結構已核） |
| ✓ | Kaustia (2010), "Prospect Theory and the Disposition Effect," *JFQA* 45(3) | 實證：投資人**賣出傾向在參考價位（成本價）處跳升**，越過後對利得幅度不敏感——處分效果非純 prospect theory 偏好可解釋 | 佐證「特定價位=行為關鍵價位」的微觀機制；支持 overhang 類因子的賣壓解釋 |
| ✓ | Barber, Lee, Liu & Odean (2007), "Is the Aggregate Investor Reluctant to Realize Losses? Evidence from Taiwan," *European Financial Mgmt* 13(3) | 1995–1999 台灣證交所**全市場**帳戶資料：整體投資人（尤其散戶）顯著不願實現損失，處分效果強 | **台股在地證據**：錨定/賣壓機制在散戶佔比高的台股應更強——T-038 的先驗依據 |
| ○ | TEJ（2023–2024），「Capital Gain Overhang 因子研究 Part 1/2」 | 以 TEJ 資料在**台股**實作 GH 版 CGO 因子並驗證選股力 | 台股實務先例（付費資料）；我們以資料湖自建，結果可互相對照 |

## B. 支撐/壓力價位的直接統計檢驗

| 狀態 | 文獻 | 關鍵發現 | 因子對應 |
|---|---|---|---|
| ✓ | Osler (2000), "Support for Resistance: Technical Analysis and Intraday Exchange Rates," *FRBNY Economic Policy Review* 6(2):53-68 | 用 6 家外匯商**實際發給客戶**的支撐壓力價位驗證：關鍵價位**顯著預測日內趨勢中斷**（bounce 而非突破），預測力因幣別/廠商而異 | 「關鍵價位有效」最硬的早期證據；但標的是日內外匯，對台股週頻選股僅方向性參考 |
| ✓ | Zapranis & Tsinaslanidis (2012), "Identifying and evaluating horizontal support and resistance levels," *Applied Financial Economics* 22(19):1571-1585 | 美股 20 年日線、以局部極值自動偵測水平 S/R：**支撐比壓力有效**；有統計預測力（趨勢中斷）但**扣除買進持有後無超額報酬** | 潑冷水組：S/R 當獨立訊號不過閘門；當**特徵/條件變數**另計（下列） |
| ○ | (MDPI) *Mathematics* (2022) 10(20):3888, "Support Resistance Levels towards Profitability in Intelligent Algorithmic Trading Models"（作者未逐篇核） | 把 S/R 做成 ML 輸入特徵：8 個貨幣對加總獲利**提升約 65%**、分布差異統計顯著 | S/R 的現代用法=特徵而非訊號；65% 數字未覆核（○） |
| ○ | "Evidence and Behaviour of Support and Resistance Levels in Financial Time Series"（2021 預印本，作者未核） | 提出 S/R 區帶的統一數學框架與預測，Precision/Recall 比極值基線**高 10–16pp** | 若日後要自動偵測 S/R 區帶可參考其形式化 |

## C. Volume Profile / Market Profile（實務工具，學術稀少）

Steidlmayer（CBOT，1980s）的 Market Profile/TPO 與其產物 **POC**（最大量價位）、
**HVN/LVN**（高低量節點）、**Value Area**：實務教材極多、但**幾乎沒有通過同儕審查的
獲利性檢驗**。與 A 類的關係：volume-at-price 分布正是 GH/An 參考價權重的原料——
學術界把「量堆在哪個價位」形式化成 CGO/overhang 後才有嚴謹的橫斷面證據。

**結論**：POC/HVN 適合作前端視覺化（K 線副圖的量價密集區）與人工確認層，
**不作為獨立預測因子**；預測力走 A 類因子路線。

## D. 因子落地對應（T-038，規格見 STRATEGY §4a）

| 因子 | 方向 | 依據 |
|---|---|---|
| `price.cgo_250d` | +1 | GH 2005；台股佐證 Barber et al. 2007 + TEJ 實務 |
| `price.gain_overhang_250d` | +1 | An 2016 獲利端賣壓 |
| `price.loss_overhang_250d` | −1 | An 2016 損失端（**與 GH/動能符號相反，以台股 RankIC 定奪**） |

資料前提與風險檢查點（詳 STRATEGY §4a.3、D-020）：還原價（T-011 待補項）為硬前置；
周轉率需在外流通股數（D-020）；2017 當沖稅改灌水周轉率（§5a 結構斷點）；漲跌停鎖死
日的量權重畸低；與 `price.high_52w`/`mom_20d`/`reversal_5d` 的相關矩陣必查（An 聲稱
吃掉 CGO、與動能部分重疊）。
