// 我的下單（T-388／D-193 §一，使用者選 C）：每個登入帳號自己記「名單說了什麼、我做了什麼」。
// 存 Supabase web.my_trades，跟每日 pipeline 的 serving 分開，修剪、重建、同步都不會碰到它（也沒有備份）。
// 只記、不評；累積三個月後另立任務出「你挑的 vs 名單隨機抽同檔數」的對照。純函數放這裡供 node 單元測試。
import { DATE_RE, SID_RE } from './picks.ts'

/** 每個帳號最多存幾筆（一天 15 檔勾滿、記三年也不到）。 */
export const TRADE_MAX = 5000
/** 一次請求內容上限（一筆 JSON 幾十個字）。 */
export const TRADE_BODY_MAX = 1000
/** 列表一次最多回幾筆（新到舊）。 */
export const TRADE_LIST_MAX = 500

export type Side = 'buy' | 'sell'
export type Source = 'pick' | 'manual'

export type Trade = {
  sid: string
  /** 交易日（來源＝pick 時是名單那天） */
  date: string
  side: Side
  /** 成交價；沒填 null */
  price: number | null
  /** 張數；沒填 null */
  qty: number | null
  source: Source
  /** 來源＝pick 時記哪條線 */
  line: string | null
}

export const TRADES_DDL = [
  'CREATE SCHEMA IF NOT EXISTS web',
  `CREATE TABLE IF NOT EXISTS web.my_trades (
    email text NOT NULL,
    stock_id text NOT NULL,
    trade_date date NOT NULL,
    side text NOT NULL,
    price double precision,
    qty integer,
    source text NOT NULL,
    line text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (email, stock_id, trade_date, side)
  )`,
]

const num = (x: unknown): number | null => (x === null || x === undefined || x === '' ? null : Number(x))

/** 驗請求內容：代號、日期（不能是未來）、買或賣、價格 ≥ 0、張數正整數、來源與線。不合法回 null。 */
export function parseTrade(body: unknown, today: string): Trade | null {
  if (!body || typeof body !== 'object') return null
  const b = body as Record<string, unknown>
  const sid = String(b.sid ?? '').trim()
  const date = String(b.date ?? '').trim()
  const side = b.side
  const source = b.source ?? 'manual'
  if (!SID_RE.test(sid) || !DATE_RE.test(date) || date > today) return null
  if (side !== 'buy' && side !== 'sell') return null
  if (source !== 'pick' && source !== 'manual') return null
  const price = num(b.price)
  const qty = num(b.qty)
  if (price !== null && !(Number.isFinite(price) && price >= 0)) return null
  if (qty !== null && !(Number.isInteger(qty) && qty > 0)) return null
  const line = b.line === null || b.line === undefined || b.line === '' ? null : String(b.line).slice(0, 16)
  return { sid, date, side, price, qty, source, line }
}

/** 刪除用的鍵：代號＋日期＋買賣。 */
export function parseTradeKey(body: unknown): { sid: string; date: string; side: Side } | null {
  if (!body || typeof body !== 'object') return null
  const b = body as Record<string, unknown>
  const sid = String(b.sid ?? '').trim()
  const date = String(b.date ?? '').trim()
  if (!SID_RE.test(sid) || !DATE_RE.test(date)) return null
  if (b.side !== 'buy' && b.side !== 'sell') return null
  return { sid, date, side: b.side }
}

/** 名單上「我買了」的鍵：名單日＋代號（來源＝pick 的買進）。 */
export function boughtKeys(trades: Trade[]): Set<string> {
  return new Set(trades.filter((t) => t.side === 'buy' && t.source === 'pick').map((t) => `${t.date}|${t.sid}`))
}
