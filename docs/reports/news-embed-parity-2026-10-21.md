# 本機 vs 雲端 bge-m3 向量一致性（T-412 驗收，2026-10-21）

- 樣本：雲端來的新聞最近 1500 則；雲端模型 ['@cf/baai/bge-m3']，本機 BAAI/bge-m3（fp16、normalize）
- 文字規則兩邊同一條（`embed_text`，截 1500 字）
- cosine：中位數 **1.0000**、p10 0.9998、p90 1.0003、最低 0.5363；≥ 0.99 的占 96.4%
- 門檻（事前）：中位數 ≥ 0.99 → **過**

最低的 5 則：

| cosine | id | 標題 |
|---|---|---|
| 0.5363 | cloud:1632223199b58fe5 | Stocks Rise as US-Iran Hopes Trigger Oil Decline: Markets Wr |
| 0.5760 | cloud:8d49b9b55d2e91b0 | AI Neoclouds Lose Their Shine, Stalling IPOs and Sinking Sto |
| 0.5863 | cloud:bbf592a2ba6c0fd9 | France to deploy military as it braces for fresh unrest this |
| 0.6214 | cloud:45e7d1044ae67646 | Oil prices fall as traders hope for diplomatic progress in M |
| 0.6525 | cloud:dabf44415d6b2d40 | S&P 500 Rebounds as Big Bank Stocks Climb, SpaceX Results Lo |

## 雲端新聞 published_at → first_seen_at（研究用可得時間）差距

| 來源 | 則數 | 中位數（分） | p90（分） |
|---|---|---|---|
| cnyes-headline | 202 | 20 | 59 |
| bloomberg-markets | 173 | 27 | 96 |
| ltn-business | 141 | 24 | 283 |
| udn-money-industry | 140 | 25 | 479 |
| digitimes | 116 | 76 | 574 |
| technews | 110 | 24 | 193 |
| udn-money-stock | 94 | 28 | 184 |
| cna-finance | 94 | 74 | 464 |
| bloomberg-tech | 93 | 27 | 237 |
| cnyes-tw-stock | 87 | 24 | 854 |
| scmp-business | 86 | 5283 | 7357 |
| cnbc-top | 80 | 34 | 1779 |
| nikkei-asia | 71 | 0 | 0 |
| ft-markets | 45 | 218 | 3482 |
| cnbc-tech | 43 | 2743 | 5462 |
| technews-finance | 40 | 1388 | 2959 |
| moneydj-news | 33 | 90 | 919 |
| cna-tech | 22 | 4304 | 6926 |
| fed-press | 20 | 27864 | 47214 |
| ecb-press | 16 | 18943 | 25963 |

註：差距大的來源（官方新聞稿、週刊型）published_at 是發布日不是抓到的時間；研究一律用 available_at（= first_seen_at），不用 published_at。

註（2026-10-21）：cosine < 0.99 的 3.6% 全是英文新聞——本機拉下來時雲端還沒翻譯（或雲端在「英文等翻譯再算」規則上線前就先算了英文向量），兩邊文字不同，不是模型差異。修法：`news pull` 從上次拉到的時間再回看兩天，文字欄與向量以雲端覆蓋；雲端早期算在英文上的向量 45 天後自然淘汰。
