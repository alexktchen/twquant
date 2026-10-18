// 集保千張大戶（T-386）：每週一次的集保戶股權分散表，第 15 級＝持股 1,000 張（100 萬股）以上。
// 千張大戶比例＝第 15 級股數 ÷ 全部 15 級股數合計。只供看盤（個股頁走勢、名單小標），不改名單、不進模型。
// 純函數放這裡供 node 單元測試；/api/picks 與 /api/stocks/:sid/holders 讀資料庫後呼叫。資料表 serving.shareholding_dispersion。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

/** 個股頁列幾週。 */
export const HOLDERS_WEEKS = 8
/** 集保分級第 15 級＝1,000 張以上。 */
export const BIG_LEVEL = 15
/** 連增／連減幾週以上才在名單上標。 */
export const STREAK_MIN = 3
/** 往回抓幾個日曆日（HOLDERS_WEEKS＋1 週，留一週算變化；週快照偶有延後，多留兩週）。 */
export const HOLDERS_LOOKBACK_DAYS = (HOLDERS_WEEKS + 3) * 7

export type HoldersWeek = {
  /** 基準日（週五） */
  date: string
  /** 千張大戶占總股數比例（小數，0.42＝42%）；合計為 0 或缺 → null */
  pct: number | null
  /** 千張大戶人數 */
  big_holders: number | null
  /** 全部 15 級股數合計（股） */
  total: number | null
}

/** 名單小標用：最新一週的比例、對前一週的變化（小數差，0.004＝0.4 個百分點）、連增（+）／連減（−）幾週、有幾週資料。 */
export type HoldersFlag = { latest: string; pct: number; change: number | null; streak: number; weeks: number }

type Raw = { date: string; big: unknown; total: unknown; big_holders: unknown }

const num = (x: unknown): number | null => (x === null || x === undefined ? null : Number(x))

/** 每週一列（舊到新）；比例算不出來是 null。 */
export function holdersSeries(rows: Raw[]): HoldersWeek[] {
  return [...rows]
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))
    .map((r) => {
      const big = num(r.big)
      const total = num(r.total)
      const pct = big !== null && total !== null && total > 0 ? big / total : null
      return { date: r.date, pct, big_holders: num(r.big_holders), total }
    })
}

/** 連增／連減幾週：從最新一週往回看，比例嚴格遞增算 +n、嚴格遞減算 −n，持平或缺值就斷。只有一週 → 0。 */
export function holdersStreak(series: HoldersWeek[]): number {
  const pts = series.map((w) => w.pct)
  let n = 0
  let dir = 0
  for (let i = pts.length - 1; i > 0; i--) {
    const a = pts[i]
    const b = pts[i - 1]
    if (a === null || b === null || a === b) break
    const d = a > b ? 1 : -1
    if (dir === 0) dir = d
    if (d !== dir) break
    n++
  }
  return dir * n
}

/** 名單小標摘要；最新一週算不出比例、或沒資料 → null。 */
export function holdersFlag(series: HoldersWeek[]): HoldersFlag | null {
  if (series.length === 0) return null
  const last = series[series.length - 1]
  if (last.pct === null) return null
  const prev = series.length > 1 ? series[series.length - 2].pct : null
  return { latest: last.date, pct: last.pct, change: prev === null ? null : last.pct - prev, streak: holdersStreak(series), weeks: series.length }
}

type Db = ReturnType<typeof sql>

const GROUPED = (db: Db, sidsCsv: string, until: string, strict: boolean) =>
  db<(Raw & { stock_id: string })[]>`
    SELECT stock_id, date::text AS date,
           sum(shares) FILTER (WHERE level = ${BIG_LEVEL}) AS big,
           sum(holders) FILTER (WHERE level = ${BIG_LEVEL}) AS big_holders,
           sum(shares) AS total
    FROM serving.shareholding_dispersion
    WHERE stock_id = ANY(string_to_array(${sidsCsv}, ','))
      AND (${strict}::boolean AND date < ${until}::date OR NOT ${strict}::boolean AND date <= ${until}::date)
      AND date > ${until}::date - ${HOLDERS_LOOKBACK_DAYS}::int
    GROUP BY stock_id, date ORDER BY stock_id, date`

/** 名單那天看得到的每檔摘要。基準日是週五、週六才公布，所以只用**早於**名單那天的週（不偷看）。表還沒建 → 空。 */
export async function readHolders(db: Db, date: string, sids: string[]): Promise<Map<string, HoldersFlag>> {
  const out = new Map<string, HoldersFlag>()
  if (sids.length === 0) return out
  const rows = await readRows(() => GROUPED(db, sids.join(','), date, true))
  for (const sid of sids) {
    const flag = holdersFlag(holdersSeries(rows.filter((r) => r.stock_id === sid)))
    if (flag) out.set(sid, flag)
  }
  return out
}

/** 個股頁：到今天為止最近幾週（含已公布的最新一週）。表還沒建 → 空。 */
export async function readHolderWeeks(db: Db, sid: string, today: string): Promise<HoldersWeek[]> {
  const rows = await readRows(() => GROUPED(db, sid, today, false))
  return holdersSeries(rows).slice(-HOLDERS_WEEKS)
}
