// 我的下單表的建立：每個讀寫端點先呼叫一次（查一次系統表；缺表才跑 DDL）。
import type { sql } from './db.ts'
import { TRADES_DDL } from './trades.ts'

export async function ensureTrades(db: ReturnType<typeof sql>): Promise<void> {
  const [{ ok }] = await db<{ ok: boolean }[]>`
    SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'web' AND table_name = 'my_trades') AS ok`
  if (!ok) for (const stmt of TRADES_DDL) await db.unsafe(stmt)
}
