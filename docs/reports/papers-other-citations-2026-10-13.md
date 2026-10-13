# 研究過程引用的其他論文（2026-10-13 整理）

> 四份文獻總表（研究文獻總表、短線操作、技術型態、關鍵價位）與出場文獻回顧之外，研究決策紀錄（DECISIONS）、
> 策略與規格文件、對抗審查（REVIEW）裡引用過的論文。每篇一句「在研究裡用在哪裡」；
> 卷期頁碼以原文為準，標「作者未核實」的是研究筆記裡只核對過題名與期刊的。

## 機器學習選股與組合建構

| 論文 | 在研究裡用在哪裡 |
|---|---|
| Gu, Kelly & Xiu (2020), *Empirical Asset Pricing via Machine Learning*, RFS | 用樹模型與神經網路預測橫斷面報酬的基準研究；本專案 ML 選股的出發點。 |
| Avramov, Cheng & Metzker (2023), *Machine Learning vs. Economic Restrictions*, Management Science | 提醒 ML 訊號的獲利多半集中在難交易的股票，加上可交易性限制後會大幅縮水；我們的流動性門檻（成交金額前 30%）與此一致。 |
| Jensen, Kelly, Malamud & Pedersen (2026), *Machine Learning and the Implementable Efficient Frontier*, RFS | 主張把交易成本直接放進組合目標、學習動態權重；用來說明「模型分數不是直接的買進名單」。 |
| Gârleanu & Pedersen (2013), *Dynamic Trading with Predictable Returns and Transaction Costs*, JF | 慢衰減的訊號給較大權重、每次只朝目標持倉移動一部分；降周轉設計的依據。 |
| Novy-Marx & Velikov (2019), FAJ（換股緩衝帶） | 新進門檻高、續抱門檻低（banding）；我們用「前 10% 才買、掉出前 30% 才賣」，周轉降了 47%（SPEC-012）。 |
| Li, Wu & Zhou (2026), *Machine+Heuristics*, Journal of Financial Markets | 列在 ML 選股研究筆記的主要參考（筆記裡沒有摘要內容）。 |
| Baruník, Hronec & Tobek (2026), *Forecasting Stock Return Distributions Around the Globe with Quantile Neural Networks*, IJF | 預測整個報酬分布而不是單一排名；右尾（飆股）模型方向的參考。 |

## 動能與擇時

| 論文 | 在研究裡用在哪裡 |
|---|---|
| Chan, Jegadeesh & Lakonishok (1996), *Momentum Strategies*, JF | 價格動能與盈餘（營收）動能各自獨立；回答「營收驚奇模型能不能再配動能」。 |
| Antonacci (2014), *Dual Momentum Investing*（書） | 相對動能＋絕對動能的擇時；大盤擇時規則的候選之一。 |
| Zakamulin (2014), Journal of Asset Management | 均線擇時的反面證據：樣本外弱、對參數敏感；提醒擇時規則不要調參。 |

## 產業與關聯公司

| 論文 | 在研究裡用在哪裡 |
|---|---|
| Cohen & Frazzini (2008), *Economic Links and Predictable Returns*, JF | 客戶的股價變動能預測供應商的報酬；「關聯公司」因子最強的文獻，因為需要供應鏈資料，列為獨立專案。 |
| Ali & Hirshleifer (2020), *Shared Analyst Coverage*, JFE | 共同分析師覆蓋能統一解釋多種動能外溢；同儕關係的另一種定義。 |
| Hoberg & Phillips（以文字定義的產業，TNIC） | 用公司描述文字定義同業；同儕因子的設計精神。 |

## 資金流、指數與基金行為

| 論文 | 在研究裡用在哪裡 |
|---|---|
| Coval & Stafford (2007), *Asset Fire Sales (and Purchases) in Equity Markets*, JFE | 基金被迫買賣造成的價格壓力與之後的反轉；投信／ETF 資金流研究（SPEC-016）的理論依據。 |
| Lou (2012), *A Flow-Based Explanation for Return Predictability*, RFS | 資金流入預測短期報酬與之後的反轉；同上。 |
| Chang, Hong & Liskovich (2015), *Regression Discontinuity and the Price Effects of Stock Market Indexing*, RFS | 指數納入／剔除的價格效應；指數換股事件的設計參考。 |
| Brown, Davies & Ringgenberg (2021), *ETF Arbitrage, Non-Fundamental Demand, and Return Predictability*, Review of Finance | ETF 申贖帶來的是非基本面需求；ETF 申購潮／贖回潮事件的依據。 |
| Chen, Jegadeesh & Wermers (2000), *The Value of Active Mutual Fund Management*, JFQA | 基金經理人買進的股票有資訊、賣出的沒有；投信買超與賣超分開看的依據。 |
| Carhart, Kaniel, Musto & Reed (2002), *Leaning for the Tape*, JF | 基金季底拉抬持股；投信季底窗飾的參考。 |
| MSCI 台灣指數調整的交易者行為與價格反應（2016, Journal of Multinational Financial Management；作者未核實） | 新增股宣告到生效前約 +3.9%、生效日反轉；外資獲利、散戶提供流動性——指數調整事件的台股實證。 |

## 放空、事件與台股市場結構

| 論文 | 在研究裡用在哪裡 |
|---|---|
| Christophe, Ferri & Angel (2004), JF | 放空者在財報公告前就先行動；事件前「知情者痕跡」研究的依據。 |
| *Shorting Flows and Return Predictability in Taiwan*（Pacific-Basin Finance Journal, 2022；作者未核實） | 台股放空流量帶有資訊；與我們量到的融券方向一致。 |
| *Short Sale and Stock Returns: Evidence from the Taiwan Stock Exchange*（Journal of Economics and Business, 2009；作者未核實） | 高融券股之後有負的異常報酬；融券可當負向篩選，搭配強制回補日曆。 |
| Hsieh, Kim & Yang (2009), *The Magnet Effect of Price Limits: A Logit Approach*, Journal of Empirical Finance | 股價接近漲跌停時續漲（跌）機率上升；回測裡「觸及漲停買不到」的成交假設依據。 |
| *Frequent Batch Auctions vs. Continuous Trading: Evidence from Taiwan*（Journal of Financial Markets, 2026；作者未核實） | 2020-03-23 台股從 5 秒集合競價改逐筆交易，中小型股成交量增加、價差收斂；滑價模型在 2020/3 前後要分段。 |
| Hartzmark & Solomon (2013), *The Dividend Month Premium*, JFE | 預期配息月份有正異常報酬、除息後反轉；股利事件策略的參考（尚未回測）。 |
| Gatev, Goetzmann & Rouwenhorst (2006), *Pairs Trading*, RFS | 配對交易的經典；統計套利家族的基準（未採用）。 |
| Do & Faff (2010), *Does Simple Pairs Trading Still Work?*, FAJ | 配對交易報酬逐年衰減、扣成本後大致無利；說明為什麼不做配對交易。 |
| Israelov (2019), *Pathetic Protection: The Elusive Benefits of Protective Puts*, Journal of Alternative Investments | 買保護性賣權不如直接降曝險；尾部風險用降曝險處理的依據。 |
