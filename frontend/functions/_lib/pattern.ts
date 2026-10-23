// 名單上每檔的型態標記（T-440／D-215 §四，使用者 2026-10-23：「可以在個股標是箱型或旗型」）：
// 今天收盤突破前 60 個交易日最高價的股票，再看突破前的長相是「箱型」（窄幅整理）還是「旗型」（急漲後整理量縮）。
// 只供看盤，不改名單、不進模型。純函數放這裡供 node 單元測試；/api/picks 讀資料庫後呼叫。
import type { sql } from './db.ts'
import { type Adj } from './weak.ts'

/** 往回看幾個交易日（不含當天）；不滿這麼多天不判斷。 */
export const WINDOW = 60
/** 收盤要高出前高這麼多才算突破（同策略 breakout_v20 的 0.5%）。 */
export const BREAK_BUFFER = 0.005
/** 箱型：前 60 日最高與最低只差這麼多以內（同因子 price.box_breakout_60d 的 box_width）。 */
export const BOX_WIDTH = 0.12
/** 旗桿：到最高點之前這麼多個交易日內的最低價算起，漲幅要達 POLE_MIN。 */
export const POLE_LOOKBACK = 30
export const POLE_MIN = 0.2
/** 旗面：最高點之後、突破前的整理天數要在這個範圍。 */
export const FLAG_MIN = 5
export const FLAG_MAX = 40
/** 旗面最低價回檔不能超過旗桿漲幅的這個比例。 */
export const RETRACE_MAX = 0.5
/** 個股行情往回抓幾個日曆天：要涵蓋 WINDOW 個交易日（含春節長假）。 */
export const PRICE_LOOKBACK_DAYS = 110

export type Ohlcv = { date: string; high: number | null; low: number | null; close: number | null; volume: number | null }
export type Pattern =
  | { kind: 'box'; over: number; width: number; days: number }
  | { kind: 'flag'; over: number; pole: number; days: number }

const ok = (b: Ohlcv) =>
  b.high !== null && b.low !== null && b.close !== null && b.volume !== null && b.high > 0 && b.low > 0 && b.close > 0

/** 還原除權息（向後還原，同 weak.ts）：除權息日之前的價格乘上該次因子。 */
function adjustedBars(bars: Ohlcv[], events: Adj[]): Ohlcv[] {
  return bars.map((b) => {
    let f = 1
    for (const e of events) if (e.date > b.date) f *= e.factor
    return f === 1 ? b : { ...b, high: (b.high as number) * f, low: (b.low as number) * f, close: (b.close as number) * f }
  })
}

const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length

/** 某檔在 target 那天的型態。bars 舊到新。那天沒有完整的行情、前面不滿 WINDOW 天、或收盤沒有突破前高 → null。 */
export function pattern(bars: Ohlcv[], events: Adj[], target: string): Pattern | null {
  const ev = events.filter((e) => e.date <= target && e.factor > 0 && Number.isFinite(e.factor))
  const xs = adjustedBars(bars.filter((b) => b.date <= target && ok(b)), ev)
  const today = xs[xs.length - 1]
  if (today?.date !== target) return null
  const prior = xs.slice(-WINDOW - 1, -1)
  if (prior.length < WINDOW) return null
  const highs = prior.map((b) => b.high as number)
  const lows = prior.map((b) => b.low as number)
  const hi = Math.max(...highs)
  const lo = Math.min(...lows)
  const over = (today.close as number) / hi - 1
  if (over < BREAK_BUFFER) return null
  // 旗型：最高點（同高取最早的）之前 POLE_LOOKBACK 天內的最低價起漲 ≥ POLE_MIN；之後整理 FLAG_MIN～FLAG_MAX 天、
  // 回檔不超過旗桿的 RETRACE_MAX、整理期平均量不高於旗桿期
  const t = highs.indexOf(hi)
  const poleStart = Math.max(0, t - POLE_LOOKBACK + 1)
  const poleLow = Math.min(...lows.slice(poleStart, t + 1))
  const poleGain = hi / poleLow - 1
  const flagDays = prior.length - 1 - t
  if (poleGain >= POLE_MIN && flagDays >= FLAG_MIN && flagDays <= FLAG_MAX) {
    const flagLow = Math.min(...lows.slice(t + 1))
    const retrace = (hi - flagLow) / (hi - poleLow)
    const poleVol = mean(prior.slice(poleStart, t + 1).map((b) => b.volume as number))
    const flagVol = mean(prior.slice(t + 1).map((b) => b.volume as number))
    if (retrace <= RETRACE_MAX && flagVol <= poleVol) return { kind: 'flag', over, pole: poleGain, days: flagDays }
  }
  const width = hi / lo - 1
  if (width <= BOX_WIDTH) return { kind: 'box', over, width, days: WINDOW }
  return null
}

type Db = ReturnType<typeof sql>
const num = (v: unknown): number | null => (v === null || v === undefined ? null : Number(v))

/** 名單上每一檔在 date 那天的型態，鍵＝代號；沒有型態的不在 Map 裡。 */
export async function readPattern(db: Db, date: string, sids: string[]): Promise<Map<string, Pattern>> {
  const out = new Map<string, Pattern>()
  if (sids.length === 0) return out
  const list = sids.join(',')
  const px = await db<{ stock_id: string; date: string; high: unknown; low: unknown; close: unknown; volume: unknown }[]>`
    SELECT stock_id, date::text AS date, high, low, close, volume FROM serving.prices_daily
    WHERE stock_id = ANY(string_to_array(${list}, ',')) AND date <= ${date}::date
      AND date > ${date}::date - ${PRICE_LOOKBACK_DAYS}::int
    ORDER BY stock_id, date`
  const ev = await db<{ stock_id: string; date: string; factor: unknown }[]>`
    SELECT stock_id, date::text AS date, adj_factor AS factor FROM serving.corporate_events
    WHERE stock_id = ANY(string_to_array(${list}, ',')) AND date <= ${date}::date
      AND date > ${date}::date - ${PRICE_LOOKBACK_DAYS}::int AND adj_factor > 0`
  for (const sid of sids) {
    const bars = px
      .filter((r) => r.stock_id === sid)
      .map((r) => ({ date: r.date, high: num(r.high), low: num(r.low), close: num(r.close), volume: num(r.volume) }))
    const events = ev.filter((r) => r.stock_id === sid).map((r) => ({ date: r.date, factor: Number(r.factor) }))
    const p = pattern(bars, events, date)
    if (p) out.set(sid, p)
  }
  return out
}
