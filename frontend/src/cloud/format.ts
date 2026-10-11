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
  /** 近 6 個有名單的交易日是否在這條線的名單，舊到新；null＝那天沒打分 */
  recent: (boolean | null)[]
  /** 近 6 日漲幅：窗口第一天收盤到當天收盤 */
  chg6: number | null
}
/** note：這條線的補充說明（例：驗證中）；沒有就不顯示 */
export type Line = { code: string; name: string; note?: string | null; picks: Pick[] }
export type PicksDay = { date: string; window?: string[]; lines: Line[] }
/** 歷史名單日期選單的檔數（/api/picks/dates）：counts[日期][線別]＝檔數；since[線別]＝這條線第一天有名單的日子。 */
export type DateCounts = { counts: Record<string, Record<string, number>>; since: Record<string, string> }

/** 某天某條線有幾檔；這條線那天還沒開始記錄（早於第一天）或後端沒給檔數 → null（不寫 0，免得把「沒記錄」說成「沒股票」）。 */
export function dayCount(c: DateCounts | null, date: string, line: string): number | null {
  const first = c?.since[line]
  if (!c || !first || date < first) return null
  return c.counts[date]?.[line] ?? 0
}

/** 日期選單的一個選項文字：「2026-10-08　2 檔」；沒有檔數就只寫日期。 */
export function dateOption(date: string, n: number | null): string {
  return n === null ? date : `${date}　${n} 檔`
}

export type Bar = { date: string; open: number; high: number; low: number; close: number; volume: number }

/** 台股習慣：漲紅、跌綠、平盤灰。 */
export function trend(chg: number | null): 'up' | 'down' | 'flat' {
  if (chg === null || !Number.isFinite(chg) || chg === 0) return 'flat'
  return chg > 0 ? 'up' : 'down'
}

/** 近 6 日方格的「上榜天數/有打分天數」，例如 4/6。 */
export function hits(recent: (boolean | null)[]): { on: number; of: number } {
  return { on: recent.filter((x) => x === true).length, of: recent.filter((x) => x !== null).length }
}

/** 0.0123 → "+1.23%"（digits 預設 2 位）；null → "—"。 */
export function pct(chg: number | null, digits = 2): string {
  if (chg === null || !Number.isFinite(chg)) return '—'
  const v = chg * 100
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
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

/** 報價列要顯示的那根 K 棒：長按中的那根，否則是名單日期那根（歷史名單），找不到就用最後一根。漲跌對前一根收盤。 */
export function quoteBar(
  bars: Bar[],
  asOf: string | undefined,
  hoverIndex: number | null,
): { bar: Bar; chg: number | null; diff: number | null } | null {
  if (bars.length === 0) return null
  let i = hoverIndex !== null && hoverIndex >= 0 && hoverIndex < bars.length ? hoverIndex : -1
  if (i < 0 && asOf) i = bars.findIndex((b) => b.date === asOf)
  if (i < 0) i = bars.length - 1
  const prev = i > 0 ? bars[i - 1].close : null
  return { bar: bars[i], chg: prev ? bars[i].close / prev - 1 : null, diff: prev ? bars[i].close - prev : null }
}

/** 漲跌點數：帶正負號，位數跟股價一樣。 */
export function signedPrice(v: number | null): string {
  if (v === null) return '—'
  return `${v > 0 ? '+' : v < 0 ? '-' : ''}${price(Math.abs(v))}`
}

/** 預設看最近 60 根 K 棒（約 3 個月；台股一個月約 21 個交易日）。 */
export const DEFAULT_VISIBLE_BARS = 60

/** 瀏覽器裡記住「K 線看幾根」的鍵（隱私權政策有寫：只有這個數字，不含個人資料）。 */
export const KEPT_BARS_KEY = 'twquant.kline.bars'

/** 讀回記住的根數；沒有或壞掉（非整數、太小、太大）就用預設。 */
export function parseKeptBars(raw: string | null): number {
  const n = Number(raw)
  return raw !== null && Number.isInteger(n) && n >= 5 && n <= 2000 ? n : DEFAULT_VISIBLE_BARS
}

/** 顯示最新的 visible 根：右緣留 1 根空間給最新一根的推薦箭頭。 */
export function latestRange(total: number, visible: number): { from: number; to: number } {
  return { from: Math.max(0, total + 1 - visible), to: total + 1 }
}
