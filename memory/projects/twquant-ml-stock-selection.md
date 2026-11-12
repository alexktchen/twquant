# 台股 ML 選股研究

**狀態：** 進行中  
**最近更新：** 2026-09-04  
**投資者版總覽：** `data/reports/台股ML選股實驗總覽.html`

## 共編與溝通偏好

- 所有可信與作廢的實驗都集中在同一份 HTML，避免只展示好看的結果。
- 正文以投資者能理解的白話撰寫；工程細節只在追溯來源或共同編輯說明中出現。
- 每個數字都必須能追溯至 `data/research_log`、配置、診斷 artifact 或正式文獻。
- 新想法必須標示「尚未回測」，不可和實驗結果混在一起。

## 已確認的投資結論

- 資料與特徵已不是主要瓶頸。模型有排序訊號，但早期 `rank_pct` 目標偏好低波動、容易小勝的股票，錯過少數大贏家。
- 原週頻策略的可測超額約每年 0.4 個百分點，統計上不顯著；估計交易成本約每年 21 個百分點，不能靠微調模型補足。
- 模型較穩定的能力在「避開最低分股票」，不只是挑出 top-20；下一代架構應能利用負面分數。
- 台股來回成本約 0.648%，其中賣出證交稅 0.30% 無法靠執行技巧消除；降低周轉是經濟必要條件。
- 增加特徵、單純降頻／平滑、拉長持有、硬切價格或成交量股票池、一般 rank 緩衝已測過或正式關閉，不重複調參。

## 目前最佳、仍未上線

`ml_net_h20_m`（成本感知模型、月頻）是目前最佳候選：

- 真實成本後年化報酬 +14.20%、Sharpe 0.644、年周轉 928%。
- 1.5 倍成本下仍為正報酬，但 Sharpe 降至 0.475。
- 四段期間有三段獲利。
- 最大回撤 -40.8%、去膨脹 Sharpe 0.199，未通過上線門檻，因此不得描述成可部署策略。

權威數字以 HTML 內的研究資料與 `data/research_log` 為準；本檔是長期摘要，不取代研究登記簿。

## 市場與文獻研究結論

公開的機構流程通常是：穩定可投資宇宙 → ML alpha forecast → 成本與風險感知的組合建構 → 增量交易門檻與執行。模型分數不是直接買進名單。

最相關的研究方向：

1. **成本直接進入組合目標。** Jensen、Kelly、Malamud、Pedersen（RFS 2026）主張評估成本後的 implementable efficient frontier，並直接學習動態 portfolio weights。
2. **只朝目標持倉移動一部分。** Gârleanu、Pedersen（JF 2013）指出，慢衰減訊號應有較大權重，交易應從舊持倉部分移向 aim portfolio。
3. **新進與續抱採不同門檻。** Novy-Marx、Velikov（RFS 2016）發現 buy/hold spread 是最有效的簡單降成本方法之一。
4. **不要只預測單一排名。** 可比較 benchmark-relative cost-adjusted return、多 horizon 預測及報酬分布／quantile 預測；尾端模型必須樣本外驗證，不能因少數大贏家事後集中就直接假設可預測。
5. **硬濾網只處理不可交易與資料品質。** 流動性、波動、產業、beta、預測不確定性宜放在權重或成本懲罰；硬刪除高波動或小型股可能同時刪掉 alpha。

- **量先價行鏈（2026-09-07，11 年 IC，未中性化未扣成本）**：法人「連續買超天數」各視窗全正（外資 5 日 IC +0.034、投信 +0.020），
  「買超÷成交量」隔日微正、20 日反向；異常放量 U 型（排序負、極端端價差正）；分點 top-15 淨買超偏散戶（T-016）。
  結論：「有人先買」要用**持續性**定義，不用量。報告 `docs/reports/volume-lead-price-ic.md`。
- **主力籌碼資料邊界**：`broker_trades.avg_price` 2022–2026 全空（主力成本只能用淨買股數×日 VWAP 近似）；集保股權分散只有 3 個週快照，
  歷史（2010 起）在 FinMind 付費層。主力持股這一環暫不可回測。
- **關鍵價位**（回踩成本區站穩再噴）：學術對應是參考價／CGO（Grinblatt–Han、An）＋ 52 週高錨（George–Hwang）＋ 掛單深度支撐
  （Kavajecz–Odders-White）；已寫成 D-093 四段式事前登記（交集大小 → 定位誤差 → 事件研究 → 分類器），**尚未回測**。

## 尚未回測的架構候選

優先研究 **enhanced-index negative screen（加強型指數／避雷型組合）**：

- 從穩定、可交易的流動性基準組合開始。
- 低分股票降權或歸零，中間股票接近基準，高分股票只適度加權。
- 用產業、beta、個股上限、ADV、tracking error 與增量交易成本控制風險。
- 目的在利用已觀察到的「避開輸家」能力，不需要立刻承擔台股放空與借券成本，也避免 top-20 過度集中。

這是新架構提案，不是實驗結果。若要實作，必須另立 SPEC／任務、事前登記門檻，並遵守 PitView、時區、單位與成本規則。

## 近期工作順序

1. 以 HTML 與研究登記簿保存 T-110 的完整結果；目前已證明成本感知目標與月頻的組合可改善成本後績效，但仍卡在回撤與去膨脹 Sharpe。
2. 完成 T-108 的 rank/pct 標準化修正；修正後所有比較須在同一標準下重跑。
3. 儘快處理 T-102，避免每日 T86 排程持續產生欄位錯位資料。
4. 若要換架構，正式比較 enhanced-index negative screen、多空、低頻事件驅動，不再回到 top-20 小參數搜尋。

## 主要參考

- Jensen et al., *Machine Learning and the Implementable Efficient Frontier*, RFS 2026.
- Gârleanu and Pedersen, *Dynamic Trading with Predictable Returns and Transaction Costs*, JF 2013.
- Novy-Marx and Velikov, *A Taxonomy of Anomalies and Their Trading Costs*, RFS 2016.
- Li, Wu and Zhou, *Machine+Heuristics*, Journal of Financial Markets 2026.
- Baruník, Hronec and Tobek, *Forecasting Stock Return Distributions Around the Globe with Quantile Neural Networks*, IJF 2026.
- Gu, Kelly and Xiu, *Empirical Asset Pricing via Machine Learning*, RFS 2020.
- George and Hwang, *The 52-Week High and Momentum Investing*, JF 2004；Grinblatt and Han, *Prospect Theory, Mental Accounting, and Momentum*, JFE 2005；An, *Asset Pricing When Traders Sell Extreme Winners and Losers*, RFS 2016.
- Lee, Liu, Roll and Subrahmanyam, *Order Imbalances and Market Efficiency: Evidence from the Taiwan Stock Exchange*, JFQA 2004；Barber, Lee, Liu and Odean, EFM 2007（台灣處置效應）與 RFS 2009（散戶年損 3.8pp）.
- Gervais, Kaniel and Mingelgrin, *The High-Volume Return Premium*, JF 2001；Lee and Swaminathan, *Price Momentum and Trading Volume*, JF 2000；Llorente, Michaely, Saar and Wang, *Dynamic Volume-Return Relation of Individual Stocks*, RFS 2002；Kavajecz and Odders-White, *Technical Analysis and Liquidity Provision*, RFS 2004.
- Avramov, Cheng and Metzker, *Machine Learning vs. Economic Restrictions*, Management Science 2023.
- Bui et al., *Momentum in Machine Learning: Evidence from the Taiwan Stock Market*, Pacific-Basin Finance Journal 2023.
