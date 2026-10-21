// 名單四面向評分、分析師觀點與 AI 首選（T-394／D-196）：每日 pipeline 寫進 serving.pick_reviews。
// 分數兩種：scale＝10（2026-10-21 起，D-206）規則算 0～10、5＝中性，附 20 個小項；scale 空＝舊資料，AI 自評 1～5。
// 每個面向一句觀點、立場（偏多／中性／偏空）、一句結論（含建議）；沒過核對的那一項是 null。
// is_top＝這條線當天的首選；source＝model（AI 挑、理由核對過）或 rule（四面向加總最高）。只顯示，沒驗證過能預測報酬。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

export type DimKey = 'news' | 'tech' | 'chips' | 'fund'

/** 一個小項：偏多 +1／中性 0／偏空 −1，附白話理由。 */
export type ReviewItem = { dim: DimKey; name: string; points: -1 | 0 | 1; text: string }

export type Review = {
  news: number
  tech: number
  chips: number
  fund: number
  total: number
  /** 每個面向滿分：10（規則）或 5（舊的 AI 自評） */
  max: number
  items: ReviewItem[]
  top: boolean
  reason: string | null
  /** 首選是 AI 挑的（true）還是規則後備（false）；不是首選時沒有意義 */
  ai: boolean
  views: { news: string | null; tech: string | null; chips: string | null; fund: string | null }
  stance: string | null
  /** 多空分歧：某面向 ≥ 7 且另一面向 ≤ 3（D-206 §六；舊資料一律 false） */
  split: boolean
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
  scale?: unknown
  items?: unknown
  split?: unknown
}

const text = (v: string | null | undefined): string | null => (v && v.trim() ? v.trim() : null)

const score = (v: unknown, lo: number, hi: number): number | null => {
  const n = Number(v)
  return v !== null && v !== undefined && Number.isInteger(n) && n >= lo && n <= hi ? n : null
}

const DIM_OF: Record<string, DimKey> = { 消息: 'news', 技術: 'tech', 籌碼: 'chips', 基本: 'fund' }

/** items 欄（jsonb，可能是字串）→ 小項；格式不對的那一項丟掉。 */
export function itemsOf(v: unknown): ReviewItem[] {
  let arr: unknown = v
  if (typeof v === 'string') {
    try {
      arr = JSON.parse(v)
    } catch {
      return []
    }
  }
  if (!Array.isArray(arr)) return []
  const out: ReviewItem[] = []
  for (const x of arr) {
    if (!x || typeof x !== 'object') continue
    const o = x as Record<string, unknown>
    const dim = DIM_OF[String(o.dim)]
    const p = Number(o.points)
    if (!dim || typeof o.name !== 'string' || typeof o.text !== 'string' || ![-1, 0, 1].includes(p)) continue
    out.push({ dim, name: o.name, points: p as -1 | 0 | 1, text: o.text })
  }
  return out
}

/** 資料庫一列 → 網站要的；四個分數缺一個（或超出該量表）就整檔不顯示（null）。 */
export function reviewOf(r: Omit<Raw, 'line' | 'stock_id'>): Review | null {
  const max = Number(r.scale) === 10 ? 10 : 5
  const lo = max === 10 ? 0 : 1
  const s = [score(r.news, lo, max), score(r.tech, lo, max), score(r.chips, lo, max), score(r.fund, lo, max)]
  if (s.some((x) => x === null)) return null
  const [news, tech, chips, fund] = s as number[]
  return {
    news,
    tech,
    chips,
    fund,
    total: news + tech + chips + fund,
    max,
    items: max === 10 ? itemsOf(r.items) : [],
    top: !!r.is_top,
    reason: r.is_top ? (r.top_reason ?? null) : null,
    ai: r.source === 'model',
    views: { news: text(r.news_view), tech: text(r.tech_view), chips: text(r.chips_view), fund: text(r.fund_view) },
    stance: text(r.stance),
    split: r.split === true || r.split === 'true',
    conclusion: text(r.conclusion),
  }
}

/** date 那天每條線每檔的評分，key＝「線|代號」。表還沒建、那天沒算 → 空。
 *  scale／items 經 to_jsonb 取：pipeline 還沒替舊表加這兩欄時照樣讀得到（缺＝null）。 */
export async function readReviews(db: ReturnType<typeof sql>, date: string): Promise<Map<string, Review>> {
  const rows = await readRows(
    () => db<Raw[]>`
      SELECT line, stock_id, news, tech, chips, fund, is_top, top_reason, source,
             news_view, tech_view, chips_view, fund_view, stance, conclusion,
             to_jsonb(r) ->> 'scale' AS scale, to_jsonb(r) -> 'items' AS items,
             to_jsonb(r) ->> 'split' AS split
      FROM serving.pick_reviews r WHERE date = ${date}::date`,
  )
  const out = new Map<string, Review>()
  for (const r of rows) {
    const v = reviewOf(r)
    if (v) out.set(`${r.line}|${r.stock_id}`, v)
  }
  return out
}
