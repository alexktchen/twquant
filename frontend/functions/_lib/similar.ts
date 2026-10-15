// 歷史相似情境（D-182）：serving.pick_similar，潛伏飆股／高分爆量名單上每一檔，
// 歷史上「大盤狀態相近、個股條件最像」的 k 次之後 20 天的分布（每日「相似情境」步驟算好存起來）。
// 表還沒建（情境庫還沒發布、第一次排程跑完之前）或那天沒算，當成沒有，名單照樣出來。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

/** 最像的其中一次：d＝公布日、sid＝代號、name＝名稱（查不到 null）、r＝20 天報酬。 */
export type SimilarExample = { d: string; sid: string; name: string | null; r: number }

export type Similar = {
  n: number
  median: number | null
  mean: number | null
  win_rate: number | null
  big_loss_rate: number | null
  p10: number | null
  p90: number | null
  from: string | null
  to: string | null
  examples: SimilarExample[]
}

type Db = ReturnType<typeof sql>

type Row = Omit<Similar, 'examples'> & { line: string; stock_id: string; examples: string | null }

function parseExamples(text: string | null): Omit<SimilarExample, 'name'>[] {
  if (!text) return []
  const v: unknown = JSON.parse(text)
  if (!Array.isArray(v)) return []
  return v.filter((e): e is Omit<SimilarExample, 'name'> => typeof e?.d === 'string' && typeof e?.sid === 'string' && typeof e?.r === 'number')
}

/** `date` 那天每條線每一檔的相似情境，鍵＝「線|代號」；例子附上名稱。 */
export async function readSimilar(db: Db, date: string): Promise<Map<string, Similar>> {
  const rows = await readRows(
    () => db<Row[]>`
      SELECT line, stock_id, n::int AS n, median, mean, win_rate, big_loss_rate, p10, p90,
             span_from::text AS "from", span_to::text AS "to", examples::text AS examples
      FROM serving.pick_similar WHERE date = ${date}::date`,
  )
  const parsed = rows.map((r) => ({ ...r, ex: parseExamples(r.examples) }))
  const sids = [...new Set(parsed.flatMap((r) => r.ex.map((e) => e.sid)))]
  const names = new Map<string, string | null>()
  if (sids.length) {
    const meta = await db<{ stock_id: string; name: string | null }[]>`
      SELECT stock_id, name FROM serving.stock_meta WHERE stock_id = ANY(string_to_array(${sids.join(',')}, ','))`
    for (const m of meta) names.set(m.stock_id, m.name)
  }
  const out = new Map<string, Similar>()
  for (const { line, stock_id, examples: _raw, ex, ...stats } of parsed) {
    out.set(`${line}|${stock_id}`, { ...stats, examples: ex.map((e) => ({ ...e, name: names.get(e.sid) ?? null })) })
  }
  return out
}
