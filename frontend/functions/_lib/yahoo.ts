// 美股指數（D-185）：Yahoo Finance 的 chart 介面（v8），一個指數一次請求，拿最近 5 個交易日的日線。
// 不是正式開放的介面：格式不對就丟錯（端點給舊的一份）。純函數，供 node 單元測試。
export const YAHOO_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/'

/** 四大指數（使用者：「美國幾大指數」；費半是台股最看的那一個）。順序＝頁面順序。 */
export const US_INDICES: { symbol: string; name: string }[] = [
  { symbol: '^GSPC', name: 'S&P 500' },
  { symbol: '^IXIC', name: '那斯達克' },
  { symbol: '^DJI', name: '道瓊' },
  { symbol: '^SOX', name: '費半' },
]

/** 最近 5 個交易日的日線（算最新一根對前一根的漲跌；放假日也一定有兩根）。 */
export const yahooUrl = (symbol: string): string => `${YAHOO_URL}${encodeURIComponent(symbol)}?range=5d&interval=1d`

export type UsIndex = {
  symbol: string
  name: string
  /** 最新一根日線是哪一天（紐約當地日期，YYYY-MM-DD） */
  date: string
  /** 最後更新時間，台北時間 HH:MM；沒有 → null */
  time: string | null
  /** 美股還在交易（最後更新落在今天的正常交易時段內）：數字是盤中的，不是收盤 */
  open: boolean
  last: number
  prev: number
  /** last / prev − 1 */
  chg: number
}

type Meta = {
  symbol?: unknown
  regularMarketTime?: unknown
  exchangeTimezoneName?: unknown
  currentTradingPeriod?: { regular?: { start?: unknown; end?: unknown } }
}
type Chart = {
  chart?: { result?: { meta?: Meta; timestamp?: unknown; indicators?: { quote?: { close?: unknown }[] } }[] | null; error?: { description?: unknown } | null }
}

/** epoch 秒 → 某時區的日期（YYYY-MM-DD）與時間（HH:MM）。 */
export function zoned(epochSec: number, timeZone: string): { date: string; time: string } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
      .formatToParts(new Date(epochSec * 1000))
      .map((p) => [p.type, p.value]),
  )
  return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}` }
}

const epoch = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : null)

/**
 * Yahoo chart 回應 → 最新一根對前一根的漲跌。
 * 不用 meta.chartPreviousClose：那是「範圍開始前一天」的收盤（5 天前），不是前一個交易日。
 * 收盤價是 null 的那根（Yahoo 偶爾會多一根空的）跳過；不到兩根 → 丟錯。
 */
export function parseYahooChart(body: unknown, name?: string): UsIndex {
  const r = (body as Chart | null)?.chart?.result?.[0]
  const meta = r?.meta
  if (!r || !meta || typeof meta.symbol !== 'string') {
    const why = (body as Chart | null)?.chart?.error?.description
    throw new Error(`Yahoo 指數回應異常${typeof why === 'string' ? `：${why}` : ''}`)
  }
  const ts = Array.isArray(r.timestamp) ? r.timestamp : []
  const closes = Array.isArray(r.indicators?.quote?.[0]?.close) ? r.indicators.quote[0].close : []
  const bars: { t: number; close: number }[] = []
  ts.forEach((t, i) => {
    const c = closes[i]
    if (typeof t === 'number' && typeof c === 'number' && Number.isFinite(c) && c > 0) bars.push({ t, close: c })
  })
  if (bars.length < 2) throw new Error(`Yahoo 指數 ${meta.symbol} 日線不到兩根`)
  const tz = typeof meta.exchangeTimezoneName === 'string' ? meta.exchangeTimezoneName : 'America/New_York'
  const latest = bars[bars.length - 1]
  const prev = bars[bars.length - 2]
  const at = epoch(meta.regularMarketTime)
  const reg = meta.currentTradingPeriod?.regular
  const start = epoch(reg?.start)
  const end = epoch(reg?.end)
  const open = at !== null && start !== null && end !== null && at >= start && at < end
  return {
    symbol: meta.symbol,
    name: name ?? US_INDICES.find((x) => x.symbol === meta.symbol)?.name ?? meta.symbol,
    date: zoned(latest.t, tz).date,
    time: at === null ? null : zoned(at, 'Asia/Taipei').time,
    open,
    last: latest.close,
    prev: prev.close,
    chg: latest.close / prev.close - 1,
  }
}

/** Yahoo 回應 → UsIndex；HTTP 不是 2xx 或不是 JSON → 丟錯（429 時換下一個執行個體也一樣，上層給舊的一份）。 */
export async function readYahoo(res: { ok: boolean; status: number; text: () => Promise<string> }, name?: string): Promise<UsIndex> {
  if (!res.ok) throw new Error(`Yahoo 指數 HTTP ${res.status}`)
  const body = await res.text()
  let parsed: unknown
  try {
    parsed = JSON.parse(body)
  } catch {
    throw new Error('Yahoo 指數回應不是 JSON')
  }
  return parseYahooChart(parsed, name)
}
