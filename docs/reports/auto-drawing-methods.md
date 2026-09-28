# 自動畫線的規則與出處（T-321）

K 線圖「自動畫線」列（[frontend/src/stock/drawings.ts](../../frontend/src/stock/drawings.ts)）用的每條規則、預設參數與出處。
**只描述圖形結構，給人判讀，不是訊號**：不進回測、不進 ML。

## 證據強度（先講清楚）

| 畫法 | 證據 |
|---|---|
| 水平支撐壓力（轉折點聚集） | Zapranis & Tsinaslanidis 2012：美股 20 年，有「趨勢中斷」效果，但**扣掉買進持有後沒有超額報酬**（亦見 [PAPERS-KEYLEVELS.md](../research/PAPERS-KEYLEVELS.md) §B） |
| 區間突破（N 日高低） | Brock–Lakonishok–LeBaron 1992 在道瓊有效；Sullivan–Timmermann–White 1999 做資料窺探校正後效果消失 |
| 型態（頭肩、雙頂） | Lo–Mamaysky–Wang 2000：有「增量資訊」，但沒有證明能獲利 |
| 趨勢線、黃金切割、缺口回補、3% 破線 | **只是慣例**，找不到嚴謹的正面證據；黃金切割比例沒有理論依據 |

所以這些線頂多有「很多人看同一條線」的自我實現效果。

## 規則與預設參數（全部在 `DRAW` 常數）

| 圖層 | 規則 | 預設 | 出處 |
|---|---|---|---|
| 轉折點 | 左右各 span 根內的最高／最低；左側嚴格、右側可相等（平台只標一次）。最後 span 根無法確認 | 短 3、長 10 | TradingView `ta.pivothigh`；Bill Williams fractal |
| 趨勢線 | 最近 6 個同類轉折點兩兩相連、錨點相隔 ≥ 5 根；兩錨點間 K 棒不得越線 > 1%；延伸到今天 ≤ 錨點距離 2 倍（否則視為過時）；排序：未破 → 碰觸多 → 錨點新 | 碰觸容忍 1% | StockCharts ChartSchool；TradingView Auto Trendlines（延伸 2 倍上限） |
| 破線 | 收盤越線 > 3% | 3% | 台股散戶慣例（「收盤突破／跌破 3%」） |
| 軌道線 | 趨勢線平移，過錨點後另一側最遠的 high／low | — | 麻紗宅在家「軌道線」；StockCharts 平行通道 |
| 水平支撐壓力 | 所有轉折點依價格併帶（相距 ≤ 2%），碰 ≥ 2 次；收盤上下各取最近 2 條 | 2%、2 次 | Zapranis & Tsinaslanidis 2012 |
| 前高／前低 | 收盤上方最近一個轉折高、下方最近一個轉折低 | — | 台股慣例 |
| 大量K棒 | 量 ≥ 2 倍前 20 日均量，且實體 ≥ 3%；高點＝壓力、低點＝支撐（站上高點＝換手成功、跌破低點＝換手失敗） | 最近 3 根 | 台股慣例（scantrader 等） |
| 箱型 | 連續 ≥ 15 根的（最高−最低）／最低 ≤ 12%，箱頂、箱底各碰 ≥ 2 次；由左往右貪婪找 | 最近 2 個 | Darvas 箱型；麻紗宅在家的方框 |
| 缺口 | 今低 > 昨高（上跳）或今高 < 昨低（下跳），≥ 1%；之後有 K 棒完全穿過就算回補、不再畫 | 最近 3 個 | CMoney、玉山證券缺口文章 |
| 黃金切割 | 視窗內（最多 120 根）最高、最低為波段；低點在前＝回檔、高點在前＝反彈 | 0.191／0.382／0.5／0.618／0.809 | 台股軟體常用比例（CMoney、XQ） |
| 回歸通道 | 收盤對 K 棒序數最小平方，± 2σ 殘差 | ≥ 20 根 | 常見線性回歸通道 |

所有圖層都用**目前視窗**（全部／120 日／60 日）的 K 棒偵測，換視窗線就跟著換。

## 已知限制

- 除權息造成的機械性缺口與假轉折點無法從未還原的 K 棒分辨，照樣畫出。
- 沒做：頭肩／三角收斂等型態、整數關卡、均線扣抵。研究整理裡都有演算法，需要時再加。
- 參數是文獻與慣例的預設值，**沒有也不應該**依回測結果調整。

## 研究來源

- TradingView Auto Trendlines：https://www.tradingview.com/support/solutions/43000741165-auto-trendlines/
- StockCharts 趨勢線：https://chartschool.stockcharts.com/table-of-contents/chart-analysis/trend-lines
- Zapranis & Tsinaslanidis 2012：https://ideas.repec.org/a/taf/apfiec/v22y2012i19p1571-1585.html
- Lo–Mamaysky–Wang 2000：https://www.nber.org/papers/w7613
- Brock–Lakonishok–LeBaron 1992：https://ideas.repec.org/a/bla/jfinan/v47y1992i5p1731-64.html
- 趨勢線與 3% 突破：https://www.stockfeel.com.tw/%E6%8A%80%E8%A1%93%E5%88%86%E6%9E%90-%E8%B6%A8%E5%8B%A2%E7%B7%9A-%E5%BB%B6%E4%BC%B8-%E9%81%8B%E7%94%A8/
- 缺口：https://money.cmoney.tw/article/11914 ；https://www.esunsec.com.tw/article/post/57
- 黃金切割率：https://www.cmoney.tw/notes/note-detail.aspx?nid=106633
- 大量K棒／箱型：https://scantrader.com/article/016f76113e9900000600000000000000 ；https://www.rayskyinvest.com/88933/darvas-box-theory
- 軌道線、方框畫法：YouTube「麻紗宅在家」（@masaathome）「只畫線不說話」系列
