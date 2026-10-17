// 首頁版面表的建立：讀寫版面的端點先呼叫一次（查一次系統表；缺表才跑 DDL）。
import type { sql } from './db.ts'
import { LAYOUT_DDL } from './layout.ts'

export async function ensureLayout(db: ReturnType<typeof sql>): Promise<void> {
  const [{ ok }] = await db<{ ok: boolean }[]>`
    SELECT EXISTS (SELECT 1 FROM information_schema.tables
      WHERE table_schema = 'web' AND table_name = 'home_layout') AS ok`
  if (!ok) for (const stmt of LAYOUT_DDL) await db.unsafe(stmt)
}
