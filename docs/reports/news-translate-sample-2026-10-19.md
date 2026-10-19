# 新聞翻譯抽樣（T-401，2026-10-19）

用今天各來源（Bloomberg×2、CNBC×2、FT、Nikkei Asia、DigiTimes）的即時 RSS 各前 5 則，跑新聞服務的翻譯流程（`news-service/src/translate.ts`，
提示詞版本 2026-10-19.2，Workers AI `@cf/qwen/qwen3-30b-a3b-fp8`，一次 8 則）。人工逐則看過。

## 結論

- **35 則全部過檢查**（沒有一則被擋下改顯示原文）；共用 113 Neurons（約 3.2／則；一天約 150 則國際新聞 ≈ 500，額度 2,500）、20 秒。
- 人工看：**31 則可用、4 則意思錯或打結**（#1、#15、#22、#30）。這 4 則數字、公司名、簡體字都對，檢查抓不到語意錯；
  網站有「譯」小標、點開附英文原文，讀者看得到原文對照。
- 自動修正有作用：拼錯的英文名改回原文（同日第一版實測 Wooptix 被寫成 Woopitx／Woopix）、簡體字轉繁體（国→國、刹→剎）、
  對岸用語換台灣用語（首席執行官→執行長）、季別改阿拉伯數字（第三季→第3季，才對得上原文 Q3）。

## 同一天改了什麼（從第一版 2026-10-19.1 到 .2）

| 第一版看到的問題 | 改法 |
|---|---|
| 「首席**执**行官」「降**温**」：手寫簡體字表漏字 | 改用 OpenCC（STCharacters／TSCharacters／TWVariants）算出的 3,795 字對照，先自動轉繁體、記在 fixes |
| 「峰」被轉成「峯」 | 多套一層台灣標準字形（TWVariants），台灣也用的字不轉 |
| Wooptix 寫成 Woopitx | 會擋的檢查：譯文的英文字原文要有；只差一兩個字母且只有一個候選的自動改回 |
| 「立憲 rejects…」留英文、亂猜人名 | 會擋的檢查：全小寫的英文字＝沒翻；提示詞：不確定台灣譯名就保留英文照抄 |
| 「無糖巧克力」「士兵…遭起訴」：加了原文沒有的字 | 提示詞：不准加原文沒有的字詞（舉例）；第二版這兩則都沒再出現 |
| Q3 寫成「第三季」→ 數字對不上被擋 | 自動修正：第一～四季改阿拉伯數字 |
| 特朗普、台灣幣、小盤股 | 詞彙表與對岸用語表（川普、新台幣、小型股） |

## 35 則

| # | 來源 | 原文 | 譯文 | 自動修正 | 人工判讀 |
|---|---|---|---|---|---|
| 1 | bloomberg-markets | VW Dealer Heir’s Windfall Fuels €3.5 Billion Investment Firm | 大眾汽車經銷商繼承人獲利35億歐元投資公司 | — | 句子打結：原意是「意外之財撐起一家 35 億歐元的投資公司」 |
| 2 | bloomberg-markets | UK Debt Is a Buy for Traders Betting on a Play-It-Safe Budget | 英國債務成為保守預算押注的買進對象 | — | 可用 |
| 3 | bloomberg-markets | Lindt’s CEO Exits as Chocolate Firm Grapples With Weak Demand | 瑞士蓮CEO辭職，巧克力公司面臨需求疲軟 | 首席執行官→執行長 | 可用 |
| 4 | bloomberg-markets | Indonesia to Meet Monday With MSCI Over Shock Index Threat | 印尼將於周一與MSCI會談應對驚人指數威脅 | — | 可用 |
| 5 | bloomberg-markets | Macron Calls for Order After Violent Protests in France | 法國暴力抗議後馬克宏呼籲秩序 | — | 可用 |
| 6 | bloomberg-tech | Japan’s Nikkei Narrows Losses as Tech Woes Cool Ahead of Results | 日本日經指數收窄跌幅，科技憂慮緩和迎來財報季 | — | 可用 |
| 7 | bloomberg-tech | The Case for Buying Small Caps Now | 現在是買進小型股的時機 | — | 可用 |
| 8 | bloomberg-tech | Taiwan Dollar Bulls Look Past AI-Led Drop to Year-End Rally | 新台幣多頭忽略AI引發的跌勢，預期年底反彈 | — | 可用 |
| 9 | bloomberg-tech | Samsung, SK Hynix Slide Causes $137 Billion Hit to Korea Pension | 三星、SK海力士下跌造成韓國養老金損失1370億美元 | — | 可用 |
| 10 | bloomberg-tech | AI Trade Reversal Pierces Asia’s Star Picks-and-Shovels Stocks | AI交易逆轉重創亞洲熱門AI概念股 | — | 可用 |
| 11 | cnbc-top | Putin reportedly suggests summit with Trump and Xi to decide fate of Ukraine | 普丁據傳建議與川普、習近平會談決定烏克蘭命運 | — | 可用 |
| 12 | cnbc-top | France hit by worst unrest in years as attempts to rein in government spending meet citizen ire | 法國爆發數年來最嚴重動亂 政府節支措施引發民眾不滿 | — | 可用 |
| 13 | cnbc-top | Always on, always listening: New AI devices are pushing legal limits and could put users in jeopardy | 永遠開啟、永遠聆聽：新AI裝置正突破法律界限 可能使用戶陷入危險 | — | 可用 |
| 14 | cnbc-top | OpenAI bugging suspect WhatsApp'ed Saudi royal days before arrest, feds reveal | OpenAI竊聽嫌疑人被發現於逮捕前幾天向沙烏地王室成員傳送訊息 | — | 可用 |
| 15 | cnbc-top | Japanese Prime Minister Takaichi rejects criticism around weak yen and high interest rates | 日本首相高市早苗否認日圓貶值與高利率政策相關 | — | 意思偏了：原文是「駁斥對日圓疲弱與高利率的批評」 |
| 16 | cnbc-tech | China's CXMT sees shares drop over 6% as investors look past chipmaker's profit turnaround | 中國CXMT股價下跌逾6% 投資人忽略晶片廠獲利轉強 | — | 可用 |
| 17 | cnbc-tech | Always on, always listening: New AI devices are pushing legal limits and could put users in jeopardy | 持續啟動、持續聆聽：新AI裝置正在突破法律界線，可能使使用者陷入危機 | — | 可用 |
| 18 | cnbc-tech | AI stocks plunge. Inflation heats up. Where we're still finding opportunities | AI股票暴跌。通膨加溫。我們仍找到機會 | — | 可用 |
| 19 | cnbc-tech | Software’s new consolidator gets big vote of confidence from Berkshire | 軟體新整合者獲巴菲特大舉支持 | — | 可用 |
| 20 | cnbc-tech | Some companies give AI to everyone. This CEO is doing the opposite | 一些公司將AI普及化。這位執行長卻反其道而行 | — | 可用 |
| 21 | ft-markets | ‘Frenzy’ in Australian deals as investors swoop on cheap assets | 澳洲交易熱潮，投資者搶購便宜資產 | — | 可用 |
| 22 | ft-markets | Investors turn on data centres | 投資者轉向資料中心 | — | 方向反了：turn on＝轉而看壞，不是「轉向」 |
| 23 | ft-markets | Private equity group Charlesbank closes one of the biggest law firm deals | 私募股權公司Charlesbank完成一筆大型律師事務所交易 | — | 可用 |
| 24 | ft-markets | BoE scrutinises Barclays over claim traders misrepresented private credit exposure | 英國央行審查巴克萊銀行有關交易員誤導私人信貸風險的聲稱 | — | 可用 |
| 25 | ft-markets | Rise of crypto vaults channels billions of dollars into risky lending | 加密貨幣金庫吸引數十億美元投入高風險貸款 | — | 可用 |
| 26 | nikkei-asia | Indian chemical firms turn to foreign partners to crack chip supply chains | 印度化學公司尋求外資合作破解晶片供應鏈 | — | 可用 |
| 27 | nikkei-asia | Indonesian wildfires threaten forest carbon projects | 印尼山火威脅森林碳專案 | — | 可用 |
| 28 | nikkei-asia | Foxconn, Mitsubishi Electric join forces for global EV push | 鴻海、三菱電機合作推動全球電動車計畫 | — | 可用 |
| 29 | nikkei-asia | China's Q3 GDP narrowly beats forecasts as strong exports buoy growth | 中國第3季GDP略超預期，強勁出口推升成長 | 第三季→第3季 | 可用 |
| 30 | nikkei-asia | Russia wants something in return for Japan journalist, ex-detainee says | 俄羅斯要求日本記者交出東西才釋放，前被拘者說 | — | 意思錯：原文是「俄方要求交換條件才放人」 |
| 31 | digitimes | Von Ardenne's flexible equipment targets unsettled panel-level packaging market | Von Ardenne柔性設備瞄準未解決的面板級封裝市場 | — | 可用 |
| 32 | digitimes | Exclusive: Wooptix targets leading chipmakers with wafer metrology technology, eyes production adoption within 3-5 years | 獨家：Wooptix以晶片量測技術接觸領先晶片廠，預計3至5年內投入生產 | — | 可用 |
| 33 | digitimes | Toyota to deploy gigacasting in China as Shanghai Lexus plant targets 2027 | 豐田將在中國導入巨型鑄造技術 上海雷克薩斯工廠目標2027年 | 国→國 | 可用 |
| 34 | digitimes | China probes Huawei-JAC Maextro V800 over brake pedal failure | 中國調查華為-JAC Maextro V800 剎車踏板故障 | 刹→剎 | 可用 |
| 35 | digitimes | Anthropic opens Singapore office, signs AI cooperation agreement | Anthropic開設新加坡辦公室 簽訂AI合作協議 | — | 可用 |
