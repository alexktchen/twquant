// 易跌標記（T-391／D-193）：D-192 模型對當天大盤精選每檔的機率，機率最高 5 檔 flagged。資料表 serving.pick_down_risk。
// 只供看盤：D-192 §七已判定它不能當濾網（剔掉它挑的 5 檔，平均報酬反而降）；名單不因此刪減。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

export type DownRisk = { p: number; flagged: boolean }

type Db = ReturnType<typeof sql>

/** date 那天每檔的機率與標記（只有大盤精選有）。表還沒建、那天沒算 → 空。 */
export async function readDownRisk(db: Db, date: string): Promise<Map<string, DownRisk>> {
  const rows = await readRows(
    () => db<{ line: string; stock_id: string; p: unknown; flagged: boolean }[]>`
      SELECT line, stock_id, p, flagged FROM serving.pick_down_risk WHERE date = ${date}::date`,
  )
  const out = new Map<string, DownRisk>()
  for (const r of rows) if (r.p !== null && r.p !== undefined) out.set(`${r.line}|${r.stock_id}`, { p: Number(r.p), flagged: !!r.flagged })
  return out
}
