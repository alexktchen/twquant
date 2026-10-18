// 個股頁的行事曆（使用者 2026-10-17：「這邊裡面的個股資訊要顯示行事曆」）：這一檔的除權息、法說會、股票分割／減資。
// 三個來源合成一串：除權息預告（dividend_calendar，有金額）、法說會（conference_calendar）、
// 已經發生的除權息與分割減資（corporate_events，只有日期與種類）。同一天預告與已發生都有 → 用預告那筆（有金額）。
// T-384 再加第四個來源：內部人持股轉讓事前申報（insider_transfer_notices，出表日期那天一筆、說明是誰要怎麼賣多少）。
import { insiderText, type InsiderNotice } from './insider.ts'

/** 往前看多少天（過去一年的除權息、法說會）。 */
export const STOCK_CAL_PAST_DAYS = 365
/** 往後看多少天（預告通常只到一個多月後，法說會兩個月左右）。 */
export const STOCK_CAL_FUTURE_DAYS = 120

export type StockCalKind = 'cash' | 'stock' | 'both' | 'split' | 'reduce' | 'conf' | 'insider'

export type StockCalItem = {
  date: string
  kind: StockCalKind
  /** 法說會時間（例：14:00） */
  time: string | null
  /** 法說會地點／說明 */
  summary: string | null
  cash_dividend: number | null
  stock_ratio: number | null
  rights_ratio: number | null
}

type Div = { date: string; kind: string; cash_dividend: number | null; stock_ratio: number | null; rights_ratio: number | null }
type Conf = { date: string; conf_time: string | null; location: string | null; summary: string | null }
type Past = { date: string; event_type: string }

/** corporate_events 的種類 → 行事曆種類；不認得的回 null（不列）。 */
const PAST_KIND: Record<string, StockCalKind> = {
  cash_dividend: 'cash',
  stock_dividend: 'stock',
  cash_and_stock_dividend: 'both',
  split: 'split',
  reverse_split: 'reduce',
}
const DIV_KIND: Record<string, StockCalKind> = { cash: 'cash', stock: 'stock', both: 'both' }

const num = (x: unknown): number | null => (x === null || x === undefined ? null : Number(x))

/** 合成一串，依日期（新到舊）、同一天法說會在後、內部人申讓更後。 */
export function stockCalendar(divs: Div[], confs: Conf[], past: Past[], insiders: InsiderNotice[] = []): StockCalItem[] {
  const out: StockCalItem[] = []
  const divDays = new Set<string>()
  for (const d of divs) {
    const kind = DIV_KIND[d.kind]
    if (!kind) continue
    divDays.add(d.date)
    out.push({ date: d.date, kind, time: null, summary: null, cash_dividend: num(d.cash_dividend), stock_ratio: num(d.stock_ratio), rights_ratio: num(d.rights_ratio) })
  }
  for (const p of past) {
    const kind = PAST_KIND[p.event_type]
    if (!kind || divDays.has(p.date)) continue
    out.push({ date: p.date, kind, time: null, summary: null, cash_dividend: null, stock_ratio: null, rights_ratio: null })
  }
  for (const c of confs) {
    const summary = [c.location, c.summary].filter((s) => s && s.trim()).join('；') || null
    out.push({ date: c.date, kind: 'conf', time: c.conf_time, summary, cash_dividend: null, stock_ratio: null, rights_ratio: null })
  }
  for (const r of insiders) {
    out.push({ date: r.date, kind: 'insider', time: null, summary: insiderText(r), cash_dividend: null, stock_ratio: null, rights_ratio: null })
  }
  const order = (k: StockCalKind) => (k === 'insider' ? 2 : k === 'conf' ? 1 : 0)
  const key = (x: StockCalItem) => `${x.date}|${order(x.kind)}|${x.time ?? ''}|${x.summary ?? ''}`
  return out.sort((a, b) => (key(a) < key(b) ? 1 : key(a) > key(b) ? -1 : 0))
}
