// 追蹤股表的建立與升級：每個讀寫追蹤股的端點先呼叫一次（查一次系統表；缺表或缺 position 欄才跑 DDL）。
import type { sql } from './db.ts'
import { WATCHLIST_DDL } from './watchlist.ts'

export async function ensureWatchlist(db: ReturnType<typeof sql>): Promise<void> {
  const [{ ok }] = await db<{ ok: boolean }[]>`
    SELECT EXISTS (SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'web' AND table_name = 'watchlist' AND column_name = 'position') AS ok`
  if (!ok) for (const stmt of WATCHLIST_DDL) await db.unsafe(stmt)
}
