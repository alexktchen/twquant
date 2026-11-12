// 台指期檢討單（T-467，使用者 2026-10-30：「那把策略也整合進去我的系統」）：每個帳號自己記每筆當沖的理由與結果。
// 存 Supabase web.futures_journal，跟每日 pipeline 的 serving 分開。純函數放這裡供 node 單元測試。
import type { sql } from './db.ts'
import { DATE_RE } from './picks.ts'

export const JOURNAL_MAX = 5000
export const JOURNAL_LIST_MAX = 500
export const JOURNAL_BODY_MAX = 1500
export const NOTE_MAX = 200

export const JOURNAL_PRODUCTS = ['MTX', 'TX', 'TMF'] as const
export const JOURNAL_SETUPS = ['回測確認', '突破', '反彈', '順勢', '其他'] as const

export type JournalInput = {
  date: string
  product: (typeof JOURNAL_PRODUCTS)[number]
  side: 1 | -1
  time: string
  entry: number
  exit: number
  stop: number | null
  qty: number
  setup: (typeof JOURNAL_SETUPS)[number]
  followed: boolean
  note: string
}

export const JOURNAL_DDL = [
  'CREATE SCHEMA IF NOT EXISTS web',
  `CREATE TABLE IF NOT EXISTS web.futures_journal (
    id bigserial PRIMARY KEY,
    email text NOT NULL,
    trade_date date NOT NULL,
    product text NOT NULL,
    side smallint NOT NULL,
    entry_time text NOT NULL,
    entry_price double precision NOT NULL,
    exit_price double precision NOT NULL,
    stop_price double precision,
    qty integer NOT NULL,
    setup text NOT NULL,
    followed boolean NOT NULL,
    note text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
  )`,
  'CREATE INDEX IF NOT EXISTS futures_journal_email_day ON web.futures_journal (email, trade_date DESC)',
]

export async function ensureJournal(db: ReturnType<typeof sql>): Promise<void> {
  const [{ ok }] = await db<{ ok: boolean }[]>`
    SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'web' AND table_name = 'futures_journal') AS ok`
  if (!ok) for (const stmt of JOURNAL_DDL) await db.unsafe(stmt)
}

const price = (x: unknown): number | null => {
  const n = Number(x)
  return x === null || x === undefined || x === '' || !Number.isFinite(n) || n <= 0 ? null : n
}

/** 驗請求內容。不合法回 null。 */
export function parseJournal(body: unknown, today: string): JournalInput | null {
  if (!body || typeof body !== 'object') return null
  const b = body as Record<string, unknown>
  const date = String(b.date ?? '').trim()
  const product = b.product as JournalInput['product']
  const setup = b.setup as JournalInput['setup']
  const time = String(b.time ?? '').trim()
  const entry = price(b.entry)
  const exit = price(b.exit)
  const qty = Number(b.qty ?? 1)
  const note = String(b.note ?? '').trim()
  if (!DATE_RE.test(date) || date > today) return null
  if (!JOURNAL_PRODUCTS.includes(product) || !JOURNAL_SETUPS.includes(setup)) return null
  if (b.side !== 1 && b.side !== -1) return null
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) return null
  if (entry === null || exit === null || !Number.isInteger(qty) || qty < 1 || qty > 100 || note.length > NOTE_MAX) return null
  const stop = b.stop === null || b.stop === undefined || b.stop === '' ? null : price(b.stop)
  if (stop === null && b.stop !== null && b.stop !== undefined && b.stop !== '') return null
  return { date, product, side: b.side, time, entry, exit, stop, qty, setup, followed: b.followed !== false, note }
}
