// 名單四面向評分與 AI 首選（T-394／D-196；10 分制規則評分 D-206）的型別、四個面向的名稱與顏色、說明文字。畫面在 Review.tsx。
/** 一個小項：偏多 +1／中性 0／偏空 −1，附白話理由（D-206，只有 10 分制才有）。 */
export type ReviewItem = { dim: DimKey; name: string; points: -1 | 0 | 1; text: string }

export type Review = {
  news: number
  tech: number
  chips: number
  fund: number
  total: number
  /** 每個面向滿分：10（規則算，5＝中性）或 5（2026-10-21 以前，AI 自評） */
  max: number
  items: ReviewItem[]
  top: boolean
  reason: string | null
  ai: boolean
  views: { news: string | null; tech: string | null; chips: string | null; fund: string | null }
  stance: string | null
  /** 多空分歧（D-206 §六） */
  split?: boolean
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

/** 「消息 5、技術 8、籌碼 4、基本 6（共 23／40）」。 */
export function scoreText(r: Review): string {
  return `${DIMS.map((d) => `${d.name} ${r[d.key]}`).join('、')}（共 ${r.total}／${r.max * DIMS.length}）`
}

/** 說明文字依量表分兩種：10 分制是規則算的（D-206），5 分制是舊的 AI 自評。 */
export function reviewNote(r: Pick<Review, 'max'>): string {
  return r.max === 10
    ? '分數由規則算：每個面向 5 項，偏多 +1、偏空 −1，從 5 分（中性）起算；描述現況，沒有驗證過能預測報酬。AI 只寫觀點與建議，數字都和事實核對過。首選累積 60 個交易日後會事後檢驗。請自行判斷。'
    : REVIEW_NOTE
}

export const REVIEW_NOTE =
  'AI 只根據當天的事實替四個面向打分、寫觀點與建議；數字與說法都和事實核對過，但沒有驗證過能預測報酬。首選累積 60 個交易日後會事後檢驗。請自行判斷。'

/** 小項的補充說明：主力賣超扣分不等於會跌（D-203 驗過）。 */
export function itemNote(i: ReviewItem): string | null {
  return i.name === '主力' && i.points < 0
    ? '主力在賣，不代表會跌：2022～2026 驗過，主力大賣之後 20 天的表現跟條件相近的股票差不多，跌 10% 的比例反而少一點。'
    : null
}

/** 立場的顏色：偏多紅、偏空綠、中性灰（台股紅漲綠跌）。五級（D-206 §六）的「強烈偏多／偏空」同色。 */
export function stanceTone(s: string | null): 'up' | 'down' | 'flat' {
  return s?.endsWith('偏多') ? 'up' : s?.endsWith('偏空') ? 'down' : 'flat'
}

/** 「強烈偏多／強烈偏空」用實心底色強調。 */
export const stanceStrong = (s: string | null): boolean => !!s?.startsWith('強烈')

/** 這條線當天的首選（沒有就 null）。 */
export function topOf<T extends { review?: Review | null }>(picks: T[]): T | null {
  return picks.find((p) => p.review?.top) ?? null
}
