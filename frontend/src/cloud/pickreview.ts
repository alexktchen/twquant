// 名單四面向評分與 AI 首選（T-394／D-196）的型別、四個面向的名稱與顏色、說明文字。畫面在 Review.tsx。
export type Review = {
  news: number
  tech: number
  chips: number
  fund: number
  total: number
  top: boolean
  reason: string | null
  ai: boolean
  views: { news: string | null; tech: string | null; chips: string | null; fund: string | null }
  stance: string | null
  conclusion: string | null
}

export type DimKey = 'news' | 'tech' | 'chips' | 'fund'

/** 四個面向：名稱、一個字的簡稱、顏色（沿用首頁路線色：消息＝事件藍、技術＝行情青、籌碼＝看法橘、基本＝族群粉）。 */
export const DIMS: { key: DimKey; name: string; short: string; color: string }[] = [
  { key: 'news', name: '消息', short: '消', color: '#3d9bff' },
  { key: 'tech', name: '技術', short: '技', color: '#2dd4bf' },
  { key: 'chips', name: '籌碼', short: '籌', color: '#ff7a00' },
  { key: 'fund', name: '基本', short: '基', color: '#f472b6' },
]

/** 滿分：四個面向各 5 分。 */
export const MAX_SCORE = 5

/** 「消息 3、技術 5、籌碼 5、基本 5（共 18／20）」。 */
export function scoreText(r: Review): string {
  return `${DIMS.map((d) => `${d.name} ${r[d.key]}`).join('、')}（共 ${r.total}／${MAX_SCORE * DIMS.length}）`
}

export const REVIEW_NOTE =
  'AI 只根據當天的事實替四個面向打分、寫觀點與建議；數字與說法都和事實核對過，但沒有驗證過能預測報酬。首選累積 60 個交易日後會事後檢驗。請自行判斷。'

/** 立場的顏色：偏多紅、偏空綠、中性灰（台股紅漲綠跌）。 */
export function stanceTone(s: string | null): 'up' | 'down' | 'flat' {
  return s === '偏多' ? 'up' : s === '偏空' ? 'down' : 'flat'
}

/** 這條線當天的首選（沒有就 null）。 */
export function topOf<T extends { review?: Review | null }>(picks: T[]): T | null {
  return picks.find((p) => p.review?.top) ?? null
}
