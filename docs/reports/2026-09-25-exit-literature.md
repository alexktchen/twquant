# 停利與出場策略：文獻回顧（2026-09-25）

> 使用者要求：「查查論文關於停利策略」。由 AI 代理檢索，**每篇都打開摘要頁或原文核對**；需要保留的地方標 ⚠。
> 用途：第十四輪（D-153 §〇：模型出場）的登記依據。本文不含任何本專案的新回測數字，最後一節除外。

## 一、論文清單

### A. 停損、移動停損與動能

1. **Kaminski, K. M. & Lo, A. W. (2014). *When do stop-loss rules stop losses?* Journal of Financial Markets 18, 234–254.** [doi:10.1016/j.finmar.2013.07.001](https://doi.org/10.1016/j.finmar.2013.07.001)
   - 股價是隨機漫步時，0/1 停損一定降低期望報酬；報酬有動能（正自相關）才可能加值，均值回歸時一定有害。
   - S&P 期貨對 10 年債期貨（1993–2011 日資料）：窗口短、逐日評估的停損在大範圍參數下都是負貢獻；一個月以上窗口較好，其中一組報酬 +1.5%、波動 −5%、Sharpe 最多 +20%。[工作論文版](https://ideas.repec.org/p/hhs/sifrwp/0063.html)用美股月資料（1950–2004）：停損期間每月多 50–100 bp。
   - **對我們**：論文把「每單位時間的差距」與「總報酬差距」分開算——比較 S0 與 S1 也要這樣分（見第四節）。
   - 另見 Clare, Seaton, Smith & Thomas (2013), *Breaking into the blackbox*, J. Asset Management 14(3):182–194（[doi](https://doi.org/10.1057/jam.2013.11)）：200 日均線規則上加停損沒有加值；月底才決策勝過更頻繁交易。
2. **Lo, A. W. & Remorov, A. (2017). *Stop-loss strategies with serial correlation, regime switching, and transaction costs.* JFM 34, 1–15.** [SSRN](https://doi.org/10.2139/ssrn.2695383)
   - 大量美國個股：緊停損因交易成本，在均值–變異架構下輸給買進持有；只有報酬自相關夠高的個股能贏，降低下檔風險的效果也有限。
   - **對我們**：與「個股、來回成本 0.4%」最接近的證據。
3. **Han, Y., Zhou, G. & Zhu, Y. *Taming Momentum Crashes: A Simple Stop-Loss Strategy.* SSRN WP 2407199（2014 初稿，2016 修訂）。** [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2407199) ⚠ 未找到期刊版。
   - 美股 1926–2013，持股自月初價跌 10% 就賣、其餘時間放國庫券。多空（WML）最大單月虧損 −49.79% → −11.36%，月報酬 0.99% → 1.69%。
   - 多方單腿（原文 Table 1）：不停損 1.24%/月（最差月 −33.06%）；停損 5% 1.53%（−8.48%）；10% 1.27%（−12.87%）；15% 1.05%（−16.82%）。
   - 限制：改善主要來自放空腿；假設盤中剛好在停損價成交（跳空才用開盤價），停損越緊越樂觀。
4. **Dai, B., Marshall, B. R., Nguyen, N. H. & Visaltanachoti, N. (2021). *Risk reduction using trailing stop-loss rules.* International Review of Finance 21(4), 1334–1352.** [doi](https://doi.org/10.1111/irfi.12328)
   - 美股 1926–2016，離高點 1–20% 的移動停損：平均報酬低於買進持有，但下檔風險顯著降低（對後來下市的股票特別有效）。
   - **對我們**：移動停損買到的是保險，不是報酬。

### B. 停利與最適賣出（理論）

5. **Dybvig, P. H. (1988). *Inefficient Dynamic Portfolio Strategies or How to Throw Away a Million Dollars in the Stock Market.* RFS 1(1), 67–88.** [連結](https://academic.oup.com/rfs/article-abstract/1/1/67/1601406)
   - lock-in（漲到目標就換成債券，即停利）與停損都是劣勢策略；最壞情況一年效率損失約 0.8%（lock-in）與接近 1%（停損）。停利的代價是結構性的，不是參數沒調好。
   - 另見 Acar & Toffel (2000), *Stop-loss and Investment Returns*（[PDF](https://www.actuaries.org.uk/system/files/documents/pdf/stop-loss-and-investment-returns.pdf)）⚠ 研討會草稿：有漂移的隨機漫步下停利降低期望報酬，只有盤整（負自相關）時條件式停利才贏；S&P 500 日內實測停利是反效果。
6. **du Toit, J. & Peskir, G. (2009). *Selling a stock at the ultimate maximum.* Annals of Applied Probability 19(3), 983–1014.** [doi](https://doi.org/10.1214/08-AAP566)
   - 研究「怎麼賣在期間最高點附近」（＝我們的天花板）：μ ≤ σ²/2 就立刻賣，否則抱到期；只有 0 < μ < σ² 的中間區間，最適解才是「從最高點回撤到一條隨時間變動的界線就賣」（移動停損型）。
   - 另見 Shiryaev, Xu & Zhou (2008), *Thou shalt buy and hold*, Quantitative Finance 8(8):765–776（[doi](https://doi.org/10.1080/14697680802563732)）：好股指數 α＝超額報酬／σ²；α ≥ 1/2 抱到期，α ≤ 0 立刻賣。
7. **Dai, M., Zhang, Q. & Zhu, Q. J. (2010). *Trend following trading under a regime switching model.* SIAM J. Financial Mathematics 1(1), 780–810.** [doi](https://doi.org/10.1137/090770552)
   - 多空狀態切換模型下，最適買賣點由「目前是多頭的條件機率」兩條門檻決定：出場看狀態機率，不看漲跌幅。**我們每天的模型重評分就是這種機率的替代指標。**
   - 另見 Zhang, Q. (2001), *Stock Trading: An Optimal Selling Rule*, SIAM J. Control Optim. 40(1):64–87（[doi](https://doi.org/10.1137/S0363012999356325)）：目標價＋停損價兩道門檻（純理論）。
8. **Lipton, A. & López de Prado, M. (2020). *A closed-form solution for optimal mean-reverting trading strategies.* arXiv:2003.10502.** [arXiv](https://arxiv.org/abs/2003.10502)
   - 均值回歸（OU）下：錯價強→不設停利停損、抱到期；錯價弱→不宜停損，但可能適合提早停利。停利只在「優勢弱、價格會回歸」時有理論支持。
   - 另見 Leung & Li (2015), IJTAF 18(3)（[doi](https://doi.org/10.1142/S021902491550020X)）；Leung & Zhang (2021), Applied Math. & Optimization 83:669–698（[doi](https://doi.org/10.1007/s00245-019-09559-0)）。

### C. 行為證據

9. **Odean, T. (1998). *Are Investors Reluctant to Realize Their Losses?* JF 53(5), 1775–1798.** [doi](https://doi.org/10.1111/0022-1082.00072)
   - 1 萬個券商帳戶（1987–93）：獲利部位實現比例 0.148，虧損只有 0.098；被賣掉的贏家下一年超額報酬比留著的輸家高 3.4 個百分點。
   - 另見 Grinblatt & Han (2005), JFE 78(2):311–339（[doi](https://doi.org/10.1016/j.jfineco.2004.10.006)）：持有人未實現獲利是動能的關鍵變數；Frazzini (2006), JF 61(4):2017–2046（[doi](https://doi.org/10.1111/j.1540-6261.2006.00896.x)）。
10. **Akepanidtaworn, K., Di Mascio, R., Imas, A. & Schmidt, L. (2023). *Selling Fast and Buying Slow.* JF 78(6), 3055–3098.** [doi](https://doi.org/10.1111/jofi.13271)（數字取自 [NBER w29076](https://www.nber.org/papers/w29076)）
    - 783 個機構投資組合（2000–2016）：買進比隨機加碼多賺 >100 bp/年；**賣出比「隨機賣掉另一檔持股」少賺約 80 bp/年**；報酬最極端的持股被賣機率高 50% 以上。
    - **對我們**：ML 出場的對照組應包含「同一天隨機賣掉一檔持股」。

### D. 台灣市場

11. **Barber, B. M., Lee, Y.-T., Liu, Y.-J. & Odean, T. (2007). *Is the Aggregate Investor Reluctant to Realise Losses? Evidence from Taiwan.* European Financial Management 13(3), 423–447.** [doi](https://doi.org/10.1111/j.1468-036X.2007.00367.x)
    - 證交所 1995–99 全部交易：獲利時被賣的機率約是虧損時的兩倍；84% 的投資人賣贏家比賣輸家快；投信與外資沒有這個傾向。另見 Barber 等 (2009) RFS 22(2)：台灣散戶整體每年落後 3.8 個百分點。
12. **Lee, S. & Chou, R. K. (2025). *Price formation around historical highs in retail-dominated stock markets: Evidence from Taiwan.* Pacific-Basin Finance Journal 93, 102853.** [IDEAS](https://ideas.repec.org/a/eee/pacfin/v93y2025ics0927538x25001908.html)
    - 台股個人占成交量 74%；突破歷史高點後市場由過度反應轉為反應不足、後續有正報酬，小型股與散戶多的股票更明顯。
    - 另見 Wang & Lien (2023) NAJEF 67:101904（彩票股異常集中在離 52 週高點遠的股票）；Hung & Yang (2018) JFM 41:77–91；Huang, Fu & Ke (2001) IREF 10(3) ⚠（漲跌停後隔夜延續、下一時段反轉；摘要經轉述、7% 漲跌幅時代）。

### E. 機器學習出場

13. **López de Prado, M. (2018). *Advances in Financial Machine Learning.* Wiley.** 第 3 章三重障礙與 meta-labeling、第 4 章重疊標籤的樣本權重、第 7 章 purged k-fold＋embargo。另見 Joubert (2022) JFDS 4(3)；Hwang 等 (2023) Finance Research Letters 58（停損一致的標籤）。
14. **Longstaff, F. A. & Schwartz, E. S. (2001). *Valuing American Options by Simulation: A Simple Least-Squares Approach.* RFS 14(1), 113–147.** 用迴歸估「繼續持有」的價值、從到期日往回推——「現在賣還是繼續抱」就是這個問題。另見 Becker, Cheridito & Jentzen (2019), *Deep optimal stopping*, JMLR 20。
15. **Kumar, N., Prabhala, N. & Ranjan, R. (2026). *Anomaly Premiums with Dynamic Exits.* SSRN WP 6076808.** ⚠ 數字取自 [二手摘要](https://larryswedroe.substack.com/p/rethinking-exit-strategies-how-machine)，須核對原文。
    - 美國大型股 1975–2023，價值／動能／獲利三個異常，用最近 15 日報酬形態以 ML 決定出場：合併 Sharpe 0.55 → 1.61、扣成本 alpha 約 60–70 bp/月，但只拿到完美預知的 11–12%。**與我們要做的事最接近。**

## 二、綜合

**(a) 固定停利對右偏的飆股策略**：理論方向一致——有正漂移或動能時，固定停利就是截掉右尾，期望報酬必然下降
（Dybvig、du Toit & Peskir、Acar & Toffel）；只有「優勢弱＋價格會回歸」時才支持停利（Lipton & López de Prado）。
我們「八成五不飆、一成五飆」的分布正是停利傷害最大的形狀。台股證據偏向讓獲利跑（散戶賣贏家太快、突破歷史高點後反應不足）；
反例都有條件（離 52 週高點遠的彩票股、舊制漲停後反轉）→ 停利要交給 ML 依條件判斷，不是一個固定參數。**不建議登記固定停利臂**（只能當負對照）。

**(b) 移動停損 vs 時間出場**：停損只在報酬有正自相關時加值；個股加成本，緊停損多半輸（Kaminski & Lo、Lo & Remorov）；
移動停損主要是降低下檔風險（Dai 等）。評估窗口一個月以上比逐日好。理論上移動停損只在中間漂移區間最適，而且回撤寬度應隨剩餘天數與漂移估計改變。

**(c) ML 出場的標籤與評估**：
- 「繼續抱」的價值要假設之後照某個政策走（Longstaff & Schwartz）；簡單版：逐日比「現在賣」與「照 S1 繼續」哪個高。
- 實際會執行的停損、0.4% 成本、賣出後資金的替代報酬都要寫進標籤。
- 每檔每天一筆樣本、標籤高度重疊 → 樣本唯一性權重＋purged／embargo 交叉驗證。
- 主對照是「同一天、同樣數量、隨機賣掉其他持股」；要檢查模型是否只學會「賣掉損益最極端的部位」。
- 不宜期待 ML 出場逼近天花板（Kumar 等只拿到完美預知的 11–12%）。

**(d) 可事前登記的對照臂（參數來自文獻）**：隨機賣出（多個亂數種子）；Han 等的固定停損 −10%（每日檢查、觸發後放現金到第 20 日），與 S1 的 −15% 並列；
理論臂——每天用校準後的重評分估剩餘期間的預期對數報酬（μ̂ − σ̂²/2），≤ 0 就賣、否則抱滿 20 日（du Toit & Peskir 的門檻，無可調參數）。

## 三、查證註記

沒找到可查證的台灣碩博士論文專門研究個股停利（國圖 NDLTD 需互動查詢）。⚠ 保留項：第 3 篇是 SSRN 工作論文；Acar & Toffel 是研討會草稿；
第 15 篇數字來自二手摘要；Huang, Fu & Ke 的結論經搜尋引擎轉述；第 10 篇數字來自 NBER 工作論文版。

## 四、這份回顧立刻抓到的一個錯（D-153 §六 更正）

第十三輪的「天花板攫取率」拿**每筆**淨報酬比，但 S1／S3 最長可抱 60 日、天花板只看 20 日——持有天數不同的每筆報酬不能直接比（第 1 篇的教訓）。
用既有結果包換算成「每持有一日」（淨報酬總和 ÷ 持有日總和；2026-09-25 補算，不是新回測）：

| 臂 | 平均持有 | 每筆淨 | **每持有日淨** | 換算 20 日 |
|---|---:|---:|---:|---:|
| S0 抱滿 20 日（8,000 萬） | 21.5 日 | +1.46% | **+0.068%** | +1.36% |
| S1 ratchet（8,000 萬） | 45.8 日 | +2.43% | **+0.053%** | +1.06% |
| S2 訊號消失（8,000 萬） | 20.6 日 | +1.40% | **+0.068%** | +1.36% |
| S3 ratchet＋訊號（8,000 萬） | 39.4 日 | +2.71% | **+0.069%** | +1.37% |
| S0（1,000 萬） | 21.0 日 | +0.95% | +0.045% | +0.90% |
| S1（1,000 萬） | 43.1 日 | +1.31% | +0.030% | +0.61% |
| S2（1,000 萬） | 20.2 日 | +1.38% | +0.068% | +1.37% |
| S3（1,000 萬） | 37.4 日 | +2.27% | +0.061% | +1.22% |

**S1 每筆多賺，是因為抱了兩倍多的時間；換算成每日，它比抱滿 20 日還差。** 8,000 萬組四臂每日幾乎一樣，攫取率 11.6%／14.9% 是持有天數造成的假象。
D-153 §四「攫取率 < 50% → 做第十四輪」的結論不變（真正的攫取率更低），但第十四輪的天花板與比較都要**同一個最長持有期**、並報每持有日。
