// 名單上的破底標記與大盤大跌提醒（D-183）：只供看盤，不改名單、不進模型。
// 純函數放這裡供 node 單元測試；/api/picks 讀資料庫後呼叫。
import type { sql } from './db.ts'

/** 「20日新低」比的是近幾個交易日（含當天）的收盤。 */
export const LOW_WINDOW = 20
/** 連跌幾天以上才標。 */
export const DOWN_MIN = 3
/** 個股收盤往回抓幾個日曆天：要涵蓋 LOW_WINDOW 個交易日（含春節長假）。 */
export const PRICE_LOOKBACK_DAYS = 45
/** 加權指數單日跌幅達這個值，名單上方才提醒。 */
export const DROP = -0.02
/** 大跌之後往後看幾個交易日。 */
export const DROP_HORIZON = 20
/** 同一波下跌只算第一天：前這麼多個交易日內已經有一次大跌，就不再算一次。 */
export const DROP_DEDUP = 10

export type Weak = { low20: boolean | null; down: number | null }
export type Bar = { date: string; close: number | null }
/** 除權息、減資、分割：factor＝除權息參考價 ÷ 前一天收盤（corporate_events.adj_factor）。 */
export type Adj = { date: string; factor: number }

export type DropStats = {
  /** 那天加權指數漲跌 */
  ret: number
  /** 統計從哪天開始（指數資料第一天） */
  since: string
  /** 歷史上幾次單日跌幅達 DROP（同一波只算第一天；只算之後 DROP_HORIZON 天到那天為止已經揭曉的） */
  n: number
  /** 其中幾次 DROP_HORIZON 天後的收盤比大跌那天高 */
  up: number
  /** 大跌後 DROP_HORIZON 天報酬的中位數；n＝0 → null */
  median: number | null
  /** 任一天之後 DROP_HORIZON 天報酬的中位數（對照） */
  base: number | null
  /** 最差的一次 */
  worst: { date: string; ret: number } | null
}

/** 還原除權息：除權息日之前的收盤乘上該次因子（向後還原，同回測引擎的口徑）。 */
export function adjusted(bars: Bar[], events: Adj[]): Bar[] {
  return bars.map((b) => {
    if (b.close === null) return b
    let c = b.close
    for (const e of events) if (e.date > b.date) c *= e.factor
    return { date: b.date, close: c }
  })
}

const valid = (bars: Bar[], target: string) => bars.filter((b) => b.date <= target && b.close !== null && b.close > 0)

/** 某檔在 target 那天：收盤是不是近 LOW_WINDOW 個交易日最低（同價也算）、連跌幾天（收盤一天比一天低，平盤就斷）。
 *  bars 舊到新。那天沒有收盤 → 兩個都 null；有收盤的日子不滿 LOW_WINDOW 天（新股、長期停牌）→ low20 null。 */
export function weakness(bars: Bar[], events: Adj[], target: string): Weak {
  const ev = events.filter((e) => e.date <= target && e.factor > 0 && Number.isFinite(e.factor))
  const cl = adjusted(valid(bars, target), ev)
  if (cl[cl.length - 1]?.date !== target) return { low20: null, down: null }
  const c = cl.map((b) => b.close as number)
  let down = 0
  for (let i = c.length - 1; i > 0 && c[i] < c[i - 1]; i--) down++
  const win = c.slice(-LOW_WINDOW)
  return { low20: win.length < LOW_WINDOW ? null : c[c.length - 1] <= Math.min(...win), down }
}

/** 序列最後一天是 target 時的單日漲跌；否則 null。 */
export function lastReturn(series: Bar[], target: string): number | null {
  const xs = valid(series, target)
  const [prev, last] = xs.slice(-2)
  return last?.date === target && prev ? (last.close as number) / (prev.close as number) - 1 : null
}

function median(xs: number[]): number | null {
  if (xs.length === 0) return null
  const s = [...xs].sort((a, b) => a - b)
  const m = Math.floor(s.length / 2)
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

/** 加權指數在 target 那天跌幅達 DROP 時，歷史上同樣大跌之後 DROP_HORIZON 個交易日怎麼走；沒跌到 DROP → null。
 *  series 舊到新；只用 target 以前的資料，事件之後的 DROP_HORIZON 天也要在 target 以前，看過去某天時不會用到之後的行情。 */
export function marketDrop(series: Bar[], target: string): DropStats | null {
  const ret = lastReturn(series, target)
  if (ret === null || ret > DROP) return null
  const xs = valid(series, target)
  const c = xs.map((b) => b.close as number)
  const r = c.map((v, i) => (i === 0 ? null : v / c[i - 1] - 1))
  const fwd = (i: number) => c[i + DROP_HORIZON] / c[i] - 1
  const last = c.length - 1 - DROP_HORIZON
  const events: number[] = []
  for (let i = 1; i <= last; i++) {
    if ((r[i] as number) > DROP) continue
    const recent = r.slice(Math.max(1, i - DROP_DEDUP), i)
    if (recent.some((x) => (x as number) <= DROP)) continue
    events.push(i)
  }
  const ev = events.map(fwd)
  const base = Array.from({ length: Math.max(0, last + 1) }, (_, i) => fwd(i))
  let worst: DropStats['worst'] = null
  for (const i of events) if (worst === null || fwd(i) < worst.ret) worst = { date: xs[i].date, ret: fwd(i) }
  return {
    ret,
    since: xs[0].date,
    n: events.length,
    up: ev.filter((x) => x > 0).length,
    median: median(ev),
    base: median(base),
    worst,
  }
}

type Db = ReturnType<typeof sql>

const num = (v: unknown): number | null => (v === null || v === undefined ? null : Number(v))

/** 名單上每一檔在 date 那天的破底狀態，鍵＝代號。 */
export async function readWeak(db: Db, date: string, sids: string[]): Promise<Map<string, Weak>> {
  const out = new Map<string, Weak>()
  if (sids.length === 0) return out
  const list = sids.join(',')
  const px = await db<{ stock_id: string; date: string; close: unknown }[]>`
    SELECT stock_id, date::text AS date, close FROM serving.prices_daily
    WHERE stock_id = ANY(string_to_array(${list}, ',')) AND date <= ${date}::date
      AND date > ${date}::date - ${PRICE_LOOKBACK_DAYS}::int
    ORDER BY stock_id, date`
  const ev = await db<{ stock_id: string; date: string; factor: unknown }[]>`
    SELECT stock_id, date::text AS date, adj_factor AS factor FROM serving.corporate_events
    WHERE stock_id = ANY(string_to_array(${list}, ',')) AND date <= ${date}::date
      AND date > ${date}::date - ${PRICE_LOOKBACK_DAYS}::int AND adj_factor > 0`
  for (const sid of sids) {
    const bars = px.filter((r) => r.stock_id === sid).map((r) => ({ date: r.date, close: num(r.close) }))
    const adj = ev.filter((r) => r.stock_id === sid).map((r) => ({ date: r.date, factor: Number(r.factor) }))
    out.set(sid, weakness(bars, adj, date))
  }
  return out
}

/** date 那天加權指數大跌時的歷史對照；沒大跌或指數還沒有那天的收盤 → null。先看近兩週，大跌才讀全部歷史。 */
export async function readDrop(db: Db, date: string): Promise<DropStats | null> {
  const read = async (all: boolean) =>
    (
      await db<{ date: string; close: unknown }[]>`
        SELECT date::text AS date, close FROM serving.index_daily
        WHERE index_id = 'TAIEX' AND date <= ${date}::date AND (${all}::boolean OR date > ${date}::date - 15)
        ORDER BY date`
    ).map((r) => ({ date: r.date, close: num(r.close) }))
  const ret = lastReturn(await read(false), date)
  if (ret === null || ret > DROP) return null
  return marketDrop(await read(true), date)
}
