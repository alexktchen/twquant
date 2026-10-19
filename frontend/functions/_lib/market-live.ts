// 盤中大盤體檢（T-397／D-198）：使用者 2026-10-19「大盤體檢可以做盤中的」。
// 證交所沒有盤中的漲跌家數，只能自己數：排程 Worker（market-live/）每分鐘問三分之一的四碼普通股（證交所「基本市況報導」
// getStockInfo），每 3 分鐘掃完全市場，結果存進 KV；網站只讀 KV、在這裡加總。家數、漲跌停、新高新低的定義同收盤後的
// market_daily（src/twquant/reporting/market_daily.py）：對昨收、漲跌停價、前 20 個交易日的最高／最低收盤。
// 成交金額用證交所 getStatis 的「股票」欄（上市＋上櫃），含特別股、TDR，跟收盤後只算四碼普通股差一點。
// 只供看盤：不進選股、不進模型。純函數，供 node 單元測試；Worker 與 Pages Functions 共用。
import { parseMis, type Quote } from './quote.ts'

/** 同 configs/serving.yaml cloud.stock_pattern、configs/system.yaml market_daily（測試核對）。 */
export const STOCK_PATTERN = '^[1-9][0-9]{3}$'
export const AVG_SESSIONS = 20
export const HIGH_LOW_WINDOW = 20

/** 全市場分幾份輪流問（每分鐘一份 → 每 SLICES 分鐘掃完一次）。 */
export const SLICES = 3
/**
 * 一次請求問幾檔（每檔只問它所在市場的頻道）。2026-10-19 盤中實測 120 檔 4.9 秒、150 檔 7.4 秒都正常；
 * 2026-10-13 實測 200 個頻道被拒（rtcode 9999）。
 */
export const BATCH = 130
/** 同一次排程裡，兩個請求證交所之間至少隔幾毫秒（證交所對同一個 IP 有頻率限制，2026-10-14 被擋過）。 */
export const MIN_GAP_MS = 1_500
/** 排程只在這段時間（台北）抓：9:00 開盤到收盤後幾分鐘，讓三份都拿到 13:30 的收盤價。 */
export const RUN_FROM = '09:00'
export const RUN_UNTIL = '13:36'

export const UNIVERSE_KEY = 'universe'
export const sliceKey = (k: number): string => `slice:${k}`

export const MIS_BASE = 'https://mis.twse.com.tw/stock'
export const MIS_HEADERS = {
  accept: 'application/json',
  'user-agent': 'Mozilla/5.0',
  referer: 'https://mis.twse.com.tw/stock/index.jsp',
}
const INDEX_CHANNELS = ['tse_t00.tw', 'otc_o00.tw']

export type Market = 'tse' | 'otc'
/** 一檔：代號、市場、前 HIGH_LOW_WINDOW 個交易日最高／最低收盤（視窗不足 → null，不算新高新低）。 */
export type UniverseStock = [sid: string, market: Market, hi: number | null, lo: number | null]
export type Universe = {
  /** 資料庫最新一個交易日（視窗算到這天） */
  asof: string
  /** 前 AVG_SESSIONS 個交易日四碼普通股每日成交金額的平均（TWD） */
  amount_avg: number | null
  stocks: UniverseStock[]
  /** Worker 哪一天（台北）抓的；不是今天就重抓 */
  fetched?: string
}

/** 一檔的盤中報價：代號、最近成交價（還沒成交 → null）、昨收、漲停價、跌停價。 */
export type SliceQuote = [sid: string, last: number | null, prev: number | null, lu: number | null, ld: number | null]
export type Slice = {
  k: number
  /** 證交所報價的日期（台北） */
  date: string
  /** 加權指數報價時間 HH:MM:SS */
  time: string
  /** Worker 寫入的時刻（毫秒） */
  at: number
  q: SliceQuote[]
  idx: { taiex: number | null; taiex_prev: number | null; otc: number | null; otc_prev: number | null }
  /** 股票成交金額累計（TWD）：上市、上櫃 */
  amt: { tse: number; otc: number }
  /** 加權指數每分鐘收盤：[HH:MM, 指數] */
  trend: [string, number][]
}

/** 網站拿到的盤中大盤體檢。家數類在三份都是今天的才有（開盤頭幾分鐘還在掃 → null）。 */
export type LiveMarket = {
  date: string
  /** 最新一份的報價時間 HH:MM:SS */
  time: string
  /** 家數是哪個時間以後的報價（最舊那一份的時間）；家數還沒算 → null */
  since: string | null
  taiex_close: number | null
  taiex_chg: number | null
  taiex_prev: number | null
  otc_close: number | null
  otc_chg: number | null
  amount: number
  amount_avg: number | null
  /** 到目前累計 ÷ 前 20 日平均的「一整天」：盤中會從 0 慢慢長到 1 左右 */
  amount_ratio: number | null
  up: number | null
  down: number | null
  flat: number | null
  /** 還沒成交的檔數 */
  untraded: number | null
  limit_up: number | null
  limit_down: number | null
  new_high: number | null
  new_low: number | null
  trend: [string, number][]
}

/** 第 k 份（共 SLICES 份）：依代號排好後切成連續的幾段。 */
export function sliceOf(stocks: UniverseStock[], k: number, n: number = SLICES): UniverseStock[] {
  const size = Math.ceil(stocks.length / n)
  return stocks.slice(k * size, (k + 1) * size)
}

/** 這一分鐘輪到哪一份（台北 HH:MM → 當天第幾分鐘 mod SLICES）。 */
export function sliceFor(hhmm: string, n: number = SLICES): number {
  const [h, m] = hhmm.split(':').map(Number)
  return (h * 60 + m) % n
}

/** 每 BATCH 檔一個 getStockInfo 網址；每檔只問自己的市場。第一個請求順便問加權與櫃買指數。 */
export function stockUrls(stocks: UniverseStock[], batch: number = BATCH): string[] {
  const urls: string[] = []
  for (let i = 0; i < stocks.length; i += batch) {
    const ch = stocks.slice(i, i + batch).map(([sid, m]) => `${m}_${sid}.tw`)
    if (i === 0) ch.unshift(...INDEX_CHANNELS)
    urls.push(`${MIS_BASE}/api/getStockInfo.jsp?ex_ch=${encodeURIComponent(ch.join('|'))}&json=1&delay=0`)
  }
  return urls
}

export const statisUrl = (m: Market, now: number): string => `${MIS_BASE}/api/getStatis.jsp?ex=${m}&delay=0&_=${now}`
export const ohlcUrl = (now: number): string => `${MIS_BASE}/data/mis_ohlc_TSE.txt?_=${now}`

const num = (v: unknown): number | null => {
  if (typeof v !== 'string' && typeof v !== 'number') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

/** getStatis 的「股票」成交金額（detail.fz，TWD）。格式不對 → 丟錯。 */
export function parseStatis(body: unknown): number {
  const b = body as { rtcode?: unknown; detail?: { fz?: unknown } } | null
  const fz = num(b?.detail?.fz)
  if (!b || b.rtcode !== '0000' || fz === null || fz < 0) throw new Error(`證交所成交統計回應異常（rtcode=${String(b?.rtcode)}）`)
  return fz
}

/** mis_ohlc_TSE 的每分鐘加權指數 → [HH:MM, 指數]（一分鐘一點）。格式不對 → 丟錯。 */
export function parseOhlc(body: unknown): [string, number][] {
  const b = body as { rtcode?: unknown; ohlcArray?: unknown } | null
  if (!b || b.rtcode !== '0000' || !Array.isArray(b.ohlcArray)) throw new Error(`證交所分時走勢回應異常（rtcode=${String(b?.rtcode)}）`)
  const out: [string, number][] = []
  for (const r of b.ohlcArray as { ts?: unknown; c?: unknown }[]) {
    const c = num(r.c)
    if (typeof r.ts !== 'string' || !/^\d{6}$/.test(r.ts) || c === null || c <= 0) continue
    const hhmm = `${r.ts.slice(0, 2)}:${r.ts.slice(2, 4)}`
    // 最後一點是「這一分鐘到目前」（例 114850），跟前一點同一分鐘 → 換掉
    if (out.at(-1)?.[0] === hhmm) out[out.length - 1] = [hhmm, c]
    else out.push([hhmm, c])
  }
  return out
}

/** 一份的報價 → Slice。加權指數不在裡面（證交所沒回）→ 丟錯：沒有日期就不知道是不是今天。 */
export function buildSlice(
  k: number,
  quotes: Quote[],
  amt: Slice['amt'],
  trend: [string, number][],
  at: number,
): Slice {
  const by = new Map(quotes.map((q) => [q.sid, q]))
  const t = by.get('t00')
  if (!t || t.time === null) throw new Error('證交所沒回加權指數報價')
  const o = by.get('o00')
  return {
    k,
    date: t.date,
    time: t.time,
    at,
    q: quotes.filter((q) => q.sid !== 't00' && q.sid !== 'o00').map((q) => [q.sid, q.last, q.prev, q.limit_up, q.limit_down]),
    idx: { taiex: t.last, taiex_prev: t.prev, otc: o?.last ?? null, otc_prev: o?.prev ?? null },
    amt,
    trend,
  }
}

/** 回應文字 → JSON；被證交所防火牆擋下時回的是一頁 HTML（「因為安全性考量…」）→ 丟錯並說是哪一支。 */
export function readJson(text: string, what: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(text.includes('安全性') ? `證交所防火牆暫時擋下（${what}）` : `${what}回應不是 JSON`)
  }
}

const chg = (last: number | null, prev: number | null): number | null => (last !== null && prev !== null && prev > 0 ? last / prev - 1 : null)

/**
 * 三份 → 網站要的盤中大盤體檢。只用 `date` 那天的份；一份都沒有 → null。
 * 指數、成交金額、走勢用最新那一份；家數要三份都在（全市場都問過）才算，否則 null。
 */
export function summarize(universe: Universe, slices: (Slice | null)[], date: string, n: number = SLICES): LiveMarket | null {
  const today = slices.filter((s): s is Slice => s !== null && s.date === date)
  if (today.length === 0) return null
  const latest = today.reduce((a, b) => (b.at > a.at ? b : a))
  const complete = new Set(today.map((s) => s.k)).size === n
  const amount = latest.amt.tse + latest.amt.otc
  const base: LiveMarket = {
    date,
    time: latest.time,
    since: null,
    taiex_close: latest.idx.taiex,
    taiex_chg: chg(latest.idx.taiex, latest.idx.taiex_prev),
    taiex_prev: latest.idx.taiex_prev,
    otc_close: latest.idx.otc,
    otc_chg: chg(latest.idx.otc, latest.idx.otc_prev),
    amount,
    amount_avg: universe.amount_avg,
    amount_ratio: universe.amount_avg ? amount / universe.amount_avg : null,
    up: null,
    down: null,
    flat: null,
    untraded: null,
    limit_up: null,
    limit_down: null,
    new_high: null,
    new_low: null,
    trend: latest.trend,
  }
  if (!complete) return base

  const quotes = new Map<string, SliceQuote>()
  // 同一檔出現在兩份（宇宙換日、切法變了）→ 用新的那份
  for (const s of [...today].sort((a, b) => a.at - b.at)) for (const q of s.q) quotes.set(q[0], q)
  let up = 0
  let down = 0
  let flat = 0
  let untraded = 0
  let lu = 0
  let ld = 0
  let hi = 0
  let lo = 0
  for (const [sid, , h, l] of universe.stocks) {
    const q = quotes.get(sid)
    if (!q) continue
    const [, last, prev, up_px, down_px] = q
    if (last === null) {
      untraded += 1
      continue
    }
    if (prev === null || prev <= 0) continue
    if (last > prev) up += 1
    else if (last < prev) down += 1
    else flat += 1
    if (up_px !== null && last >= up_px) lu += 1
    if (down_px !== null && last <= down_px) ld += 1
    if (h !== null && last > h) hi += 1
    if (l !== null && last < l) lo += 1
  }
  const since = today.reduce((a, b) => (b.time < a.time ? b : a)).time
  return { ...base, since, up, down, flat, untraded, limit_up: lu, limit_down: ld, new_high: hi, new_low: lo }
}

/** /pub/market-universe 的回應 → Universe；格式不對 → 丟錯（Worker 不能拿壞掉的宇宙去問）。 */
export function parseUniverse(body: unknown): Universe {
  const b = body as Partial<Universe> | null
  if (!b || typeof b.asof !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(b.asof) || !Array.isArray(b.stocks) || b.stocks.length === 0) {
    throw new Error('股票清單（/pub/market-universe）回應異常')
  }
  const pat = new RegExp(STOCK_PATTERN)
  for (const s of b.stocks) {
    if (!Array.isArray(s) || typeof s[0] !== 'string' || !pat.test(s[0]) || (s[1] !== 'tse' && s[1] !== 'otc')) {
      throw new Error(`股票清單格式錯誤：${JSON.stringify(s)}`)
    }
  }
  return { asof: b.asof, amount_avg: typeof b.amount_avg === 'number' ? b.amount_avg : null, stocks: b.stocks as UniverseStock[] }
}

/** 台北現在的日期 YYYY-MM-DD 與時間 HH:MM（台灣沒有日光節約，固定 +8）。 */
export function taipeiClock(at: number): { date: string; hhmm: string } {
  const iso = new Date(at + 8 * 3600_000).toISOString()
  return { date: iso.slice(0, 10), hhmm: iso.slice(11, 16) }
}

export type Kv = {
  get(key: string, type: 'json'): Promise<unknown>
  put(key: string, value: string): Promise<void>
}

/** Worker 每分鐘做的事（I/O 由呼叫端注入，供測試）：時間外、休市 → 不寫；否則問這一份並寫進 KV。回傳做了什麼。 */
export async function runOnce(deps: {
  now: number
  kv: Kv
  fetchText: (url: string) => Promise<string>
  sleep: (ms: number) => Promise<void>
  universeUrl: string
}): Promise<string> {
  const { now, kv, fetchText, sleep } = deps
  const { date, hhmm } = taipeiClock(now)
  if (hhmm < RUN_FROM || hhmm > RUN_UNTIL) return `略過：台北 ${hhmm} 不在 ${RUN_FROM}～${RUN_UNTIL}`

  let universe = (await kv.get(UNIVERSE_KEY, 'json')) as Universe | null
  if (!universe || universe.fetched !== date) {
    universe = { ...parseUniverse(readJson(await fetchText(deps.universeUrl), '股票清單')), fetched: date }
    await kv.put(UNIVERSE_KEY, JSON.stringify(universe))
  }

  const k = sliceFor(hhmm)
  const urls = stockUrls(sliceOf(universe.stocks, k))
  // 一個接一個問，兩個請求之間隔 MIN_GAP_MS
  const ask = async (url: string, i: number): Promise<string> => {
    if (i > 0) await sleep(MIN_GAP_MS)
    return fetchText(url)
  }
  const quotes: Quote[] = []
  for (const [i, url] of urls.entries()) {
    const got = parseMis(readJson(await ask(url, i), '即時報價'))
    // 第一個請求就看得出今天有沒有開市：加權指數的日期不是今天 → 休市，不再問、不寫
    const t = got.find((q) => q.sid === 't00')
    if (i === 0 && t && t.date !== date) return `略過：證交所報價是 ${t.date}，今天（${date}）休市`
    quotes.push(...got)
  }
  const n = urls.length
  const tse = parseStatis(readJson(await ask(statisUrl('tse', now), n), '上市成交統計'))
  const otc = parseStatis(readJson(await ask(statisUrl('otc', now), n + 1), '上櫃成交統計'))
  const trend = parseOhlc(readJson(await ask(ohlcUrl(now), n + 2), '分時走勢'))
  const slice = buildSlice(k, quotes, { tse, otc }, trend, now)
  await kv.put(sliceKey(k), JSON.stringify(slice))
  return `第 ${k} 份 ${slice.q.length} 檔，報價時間 ${slice.time}`
}
