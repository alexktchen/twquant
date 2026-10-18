// 名單四面向評分、分析師觀點與 AI 首選（T-394／D-196）：每日 pipeline 寫進 serving.pick_reviews。分數 1～5（沒過＝null），
// 每個面向一句觀點、立場（偏多／中性／偏空）、一句結論（含建議）；沒過核對的那一項是 null。
// is_top＝這條線當天的首選；source＝model（AI 挑、理由核對過）或 rule（四面向加總最高）。只顯示，沒驗證過能預測報酬。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

export type Review = {
  news: number
  tech: number
  chips: number
  fund: number
  total: number
  top: boolean
  reason: string | null
  /** 首選是 AI 挑的（true）還是規則後備（false）；不是首選時沒有意義 */
  ai: boolean
  views: { news: string | null; tech: string | null; chips: string | null; fund: string | null }
  stance: string | null
  conclusion: string | null
}

type Raw = {
  line: string
  stock_id: string
  news: unknown
  tech: unknown
  chips: unknown
  fund: unknown
  is_top: boolean | null
  top_reason: string | null
  source: string | null
  news_view?: string | null
  tech_view?: string | null
  chips_view?: string | null
  fund_view?: string | null
  stance?: string | null
  conclusion?: string | null
}

const text = (v: string | null | undefined): string | null => (v && v.trim() ? v.trim() : null)

const score = (v: unknown): number | null => {
  const n = Number(v)
  return v !== null && v !== undefined && Number.isInteger(n) && n >= 1 && n <= 5 ? n : null
}

/** 資料庫一列 → 網站要的；四個分數缺一個就整檔不顯示（null）。 */
export function reviewOf(r: Omit<Raw, 'line' | 'stock_id'>): Review | null {
  const s = [score(r.news), score(r.tech), score(r.chips), score(r.fund)]
  if (s.some((x) => x === null)) return null
  const [news, tech, chips, fund] = s as number[]
  return {
    news,
    tech,
    chips,
    fund,
    total: news + tech + chips + fund,
    top: !!r.is_top,
    reason: r.is_top ? (r.top_reason ?? null) : null,
    ai: r.source === 'model',
    views: { news: text(r.news_view), tech: text(r.tech_view), chips: text(r.chips_view), fund: text(r.fund_view) },
    stance: text(r.stance),
    conclusion: text(r.conclusion),
  }
}

/** date 那天每條線每檔的評分，key＝「線|代號」。表還沒建、那天沒算 → 空。 */
export async function readReviews(db: ReturnType<typeof sql>, date: string): Promise<Map<string, Review>> {
  const rows = await readRows(
    () => db<Raw[]>`
      SELECT line, stock_id, news, tech, chips, fund, is_top, top_reason, source,
             news_view, tech_view, chips_view, fund_view, stance, conclusion
      FROM serving.pick_reviews WHERE date = ${date}::date`,
  )
  const out = new Map<string, Review>()
  for (const r of rows) {
    const v = reviewOf(r)
    if (v) out.set(`${r.line}|${r.stock_id}`, v)
  }
  return out
}
