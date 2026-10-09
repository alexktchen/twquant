// 雲端版（T-332）的純函數：漲跌格式、均線、K 線標記。不碰 DOM，供 node 單元測試。

export type Pick = {
  rank: number
  sid: string
  name: string | null
  score: number | null
  close: number | null
  chg: number | null
  streak: number
  new: boolean
}
export type Line = { code: string; name: string; picks: Pick[] }
export type PicksDay = { date: string; lines: Line[] }
export type Bar = { date: string; open: number; high: number; low: number; close: number; volume: number }

/** 台股習慣：漲紅、跌綠、平盤灰。 */
export function trend(chg: number | null): 'up' | 'down' | 'flat' {
  if (chg === null || !Number.isFinite(chg) || chg === 0) return 'flat'
  return chg > 0 ? 'up' : 'down'
}

/** 0.0123 → "+1.23%"；null → "—"。 */
export function pct(chg: number | null): string {
  if (chg === null || !Number.isFinite(chg)) return '—'
  const v = chg * 100
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

/** 價格依台股檔位習慣：≥ 1000 取 0 位、≥ 100 取 1 位、其餘 2 位。 */
export function price(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return '—'
  return v.toFixed(v >= 1000 ? 0 : v >= 100 ? 1 : 2)
}

/** 簡單移動平均；前 n−1 根沒有值（不補）。 */
export function sma(bars: Bar[], n: number): { time: string; value: number }[] {
  const out: { time: string; value: number }[] = []
  let sum = 0
  bars.forEach((b, i) => {
    sum += b.close
    if (i >= n) sum -= bars[i - n].close
    if (i >= n - 1) out.push({ time: b.date, value: sum / n })
  })
  return out
}

/** 被推薦的日子 → K 線標記（只標在 K 線範圍內的日子）。 */
export function pickMarkers(
  days: Record<string, string[]>,
  names: Record<string, string>,
  bars: Bar[],
): { time: string; text: string; line: string }[] {
  const inRange = new Set(bars.map((b) => b.date))
  const out: { time: string; text: string; line: string }[] = []
  for (const [line, ds] of Object.entries(days)) {
    for (const d of ds) if (inRange.has(d)) out.push({ time: d, text: names[line] ?? line, line })
  }
  return out.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : a.line.localeCompare(b.line)))
}
