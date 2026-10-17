// 同波動股票 20 日內碰到停損／大漲的歷史比例（D-191 §三，使用者 2026-10-18 選 C；T-389）。
// 不是預測模型：把 D-132「下跌風險＝波動度」的結論變成查表。表凍結在 configs/stop_hit_reference.json，
// 這裡的 stop-hit-table.ts 是鏡像（tests/test_stop_hit_reference.py 比對兩份相同）。只供看盤，不改名單、不進模型。
import type { sql } from './db.ts'
import { STOP_HIT_TABLE } from './stop-hit-table.ts'

/** 波動度窗（同 factors/price.vol_20d）：近 20 個交易日的簡單日報酬，要 21 筆收盤。 */
export const VOL_WINDOW = 20

export type StopHitGroup = {
  group: number
  vol_lo: number | null
  vol_hi: number | null
  vol_median: number
  n: number
  down15: number
  up20: number
  median_ret: number
}

export type StopHitTable = {
  dev_start: string
  dev_end: string
  horizon_days: number
  stop: number
  surge: number
  bounds: number[]
  groups: StopHitGroup[]
}

/** 名單一列附的查表結果：這檔當天的波動度、落在哪一組、那組的歷史比例與樣本數。 */
export type StopHit = { vol: number; group: number; down15: number; up20: number; n: number; vol_lo: number | null; vol_hi: number | null }

export const STOP_HIT: StopHitTable = {
  dev_start: STOP_HIT_TABLE.dev_start,
  dev_end: STOP_HIT_TABLE.dev_end,
  horizon_days: STOP_HIT_TABLE.horizon_days,
  stop: STOP_HIT_TABLE.stop,
  surge: STOP_HIT_TABLE.surge,
  bounds: [...STOP_HIT_TABLE.bounds],
  groups: STOP_HIT_TABLE.groups.map((g) => ({ ...g })),
}

/** 近 20 日簡單日報酬的樣本標準差（ddof=1），收盤不還原除權息；收盤不足 21 筆或有 0／缺值 → null。 */
export function vol20(closes: (number | null)[]): number | null {
  const c = closes.slice(-(VOL_WINDOW + 1))
  if (c.length < VOL_WINDOW + 1) return null
  const rets: number[] = []
  for (let i = 1; i < c.length; i++) {
    const a = c[i - 1]
    const b = c[i]
    if (a === null || b === null || !(a > 0) || !Number.isFinite(b)) return null
    rets.push(b / a - 1)
  }
  const mean = rets.reduce((s, x) => s + x, 0) / rets.length
  const ss = rets.reduce((s, x) => s + (x - mean) * (x - mean), 0)
  return Math.sqrt(ss / (rets.length - 1))
}

/** vol → 組 0..9：第 g 組＝bounds[g−1] ≤ vol < bounds[g]（同 scripts/d191_stop_hit_reference.py 的 group_expr）。 */
export function groupOf(vol: number, bounds: number[] = STOP_HIT.bounds): number {
  let g = 0
  for (const b of bounds) if (vol >= b) g++
  return g
}

/** 查表；vol 算不出來 → null。 */
export function stopHit(closes: (number | null)[], t: StopHitTable = STOP_HIT): StopHit | null {
  const vol = vol20(closes)
  if (vol === null) return null
  const g = t.groups[groupOf(vol, t.bounds)]
  if (!g) return null
  return { vol, group: g.group, down15: g.down15, up20: g.up20, n: g.n, vol_lo: g.vol_lo, vol_hi: g.vol_hi }
}

type Db = ReturnType<typeof sql>
const num = (x: unknown): number | null => (x === null || x === undefined ? null : Number(x))

/** 名單那天（含）往回 21 筆收盤 → 每檔查表。訊號日收盤已知，所以含當天。 */
export async function readStopHit(db: Db, date: string, sids: string[]): Promise<Map<string, StopHit>> {
  const out = new Map<string, StopHit>()
  if (sids.length === 0) return out
  const rows = await db<{ stock_id: string; date: string; close: unknown }[]>`
    SELECT stock_id, date::text AS date, close FROM serving.prices_daily
    WHERE stock_id = ANY(string_to_array(${sids.join(',')}, ',')) AND date <= ${date}::date
      AND date > ${date}::date - 45
    ORDER BY stock_id, date`
  for (const sid of sids) {
    const closes = rows.filter((r) => r.stock_id === sid).map((r) => num(r.close))
    const hit = stopHit(closes)
    if (hit) out.set(sid, hit)
  }
  return out
}
