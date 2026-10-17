// configs/news_categories.json 的鏡像（T-378／D-188）：Pages Functions 不能讀 repo 外的檔，tests/test_news_categories.py 守著兩邊相同。
// 改分類請改 configs/news_categories.json，再把 "categories" 貼過來。
export const NEWS_CATEGORIES = [
  { "id": "disposal", "label": "處置／注意", "market_wide": true, "keywords": ["處置", "注意交易資訊", "注意股票"] },
  { "id": "trading", "label": "變更交易／下市", "market_wide": true, "keywords": ["變更交易方法", "停止買賣", "終止上市", "終止上櫃", "下市", "全額交割", "恢復交易"] },
  { "id": "capital", "label": "減資／增資／私募", "market_wide": true, "keywords": ["減資", "現金增資", "私募", "可轉換公司債", "員工認股權", "限制員工權利新股"] },
  { "id": "deal", "label": "併購／分割", "market_wide": true, "keywords": ["合併案", "吸收合併", "合併契約", "收購", "併購", "公開收購", "分割", "股份轉換"] },
  { "id": "buyback", "label": "庫藏股", "market_wide": true, "keywords": ["庫藏股"] },
  { "id": "ops", "label": "停工／災損／訴訟／裁罰", "market_wide": true, "keywords": ["停工", "停產", "火災", "罷工", "訴訟", "裁罰", "重大災", "資安"] },
  { "id": "people", "label": "董總異動", "market_wide": true, "keywords": ["董事長", "總經理", "財務主管", "會計主管", "獨立董事", "辭任", "解任"] },
  { "id": "press", "label": "重訊說明記者會", "market_wide": true, "keywords": ["記者會"] },
  { "id": "conference", "label": "法說會", "market_wide": false, "keywords": ["法人說明會", "法說會", "說明會", "路演", "Conference", "Summit"] },
  { "id": "earnings", "label": "營收／財報／股利", "market_wide": false, "keywords": ["營收", "盈餘", "虧損", "財務報告", "財務報表", "自結", "股利", "除息", "除權"] },
  { "id": "order", "label": "訂單／合約／投資", "market_wide": false, "keywords": ["訂單", "合約", "契約", "取得", "處分", "投資", "合建", "設廠"] }
] as const

export type NewsCategory = (typeof NEWS_CATEGORIES)[number]
export type CategoryId = NewsCategory['id']
