// 歷史同區間參考（D-179）：serving.score_reference 整表（幾十列），每條線附在 /api/picks 上。
// 本機 scripts/score_reference.py 算一次、整表覆蓋；表還沒建（還沒算）當成沒有，名單照樣出來。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

export type RefRow = {
  line: string
  period: 'dev' | 'unseen'
  label: string
  bucket: string
  lo: number | null
  hi: number | null
  n: number
  mean: number | null
  median: number | null
  win_rate: number | null
  big_loss_rate: number | null
  p10: number | null
  p90: number | null
}

type Db = ReturnType<typeof sql>

/** 每條線的列（dev 在前、區間由低到高）；表不存在 → 空 Map。 */
export async function readReference(db: Db): Promise<Map<string, Omit<RefRow, 'line'>[]>> {
  const rows = await readRows(
    () => db<RefRow[]>`
      SELECT line, period, label, bucket, lo, hi, n::int AS n, mean, median, win_rate, big_loss_rate, p10, p90
      FROM serving.score_reference ORDER BY line, period, lo NULLS FIRST`,
  )
  const out = new Map<string, Omit<RefRow, 'line'>[]>()
  for (const { line, ...r } of rows) out.set(line, [...(out.get(line) ?? []), r])
  return out
}
