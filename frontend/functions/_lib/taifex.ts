// 台指期即時行情（D-186）：期交所「期貨市況」的 getQuoteList，日盤（MarketType 0）與夜盤（MarketType 1）各問一次，
// 一次回全部月份的臺指期＋臺指現貨。不是正式開放的介面：格式不對就丟錯（端點給舊的一份或 503）。純函數，供 node 單元測試。
export const TAIFEX_URL = 'https://mis.taifex.com.tw/futures/api/getQuoteList'

export type Session = 'day' | 'night'

/** 問臺指期（TXF）全部月份的 POST 內容；日盤 0、夜盤 1（期交所網頁自己送的參數，2026-10-17 錄）。 */
export const taifexBody = (session: Session): string =>
  JSON.stringify({
    MarketType: session === 'day' ? '0' : '1',
    SymbolType: 'F',
    KindID: '1',
    CID: 'TXF',
    ExpireMonth: '',
    RowSize: '全部',
    PageNo: '',
    SortColumn: '',
    AscDesc: 'A',
  })

export type TxQuote = {
  session: Session
  /** 合約代號（TXFJ6）與月份名（臺指期106） */
  symbol: string
  name: string
  /**
   * 期交所標的交易日（YYYY-MM-DD）。夜盤標的是它接在後面的那個日盤的日期：10/16 15:00～10/17 05:00 這一節是 2026-10-16，
   * 凌晨的成交時間（04:59:57）配的也是這個日期。
   */
  date: string
  /** 最後成交時間 HH:MM:SS；還沒成交 → null */
  time: string | null
  last: number | null
  /** 漲跌參考價：日盤＝前一個交易日的結算價、夜盤＝當天日盤的結算價 */
  ref: number | null
  /** 日盤收盤後的每日結算價；夜盤沒有 → null */
  settlement: number | null
  /** 累計成交口數 */
  volume: number | null
  /** 試撮價（開盤前 8:30～8:45、14:50～15:00）；沒有 → null */
  test: number | null
  /** last / ref − 1；缺一個 → null */
  chg: number | null
}

/** 臺指現貨（加權指數）在同一份回應裡：日盤給最新點數與昨收；夜盤那份只有「參考價」＝當天加權收盤（last 為 null）。 */
export type TxSpot = { date: string; time: string | null; last: number | null; prev: number | null }

export type TaifexQuotes = { session: Session; spot: TxSpot | null; near: TxQuote | null }

// 期交所用 "0.00" 表示沒有；非數字也當沒有（不補 0）
const price = (v: unknown): number | null => {
  if (typeof v !== 'string' || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? n : null
}
const count = (v: unknown): number | null => {
  if (typeof v !== 'string' || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) && Number.isInteger(n) && n >= 0 ? n : null
}
const ymd = (v: unknown): string | null => (typeof v === 'string' && /^\d{8}$/.test(v) ? `${v.slice(0, 4)}-${v.slice(4, 6)}-${v.slice(6, 8)}` : null)
const hms = (v: unknown): string | null => (typeof v === 'string' && /^\d{6}$/.test(v) ? `${v.slice(0, 2)}:${v.slice(2, 4)}:${v.slice(4, 6)}` : null)

/** 現貨列：日盤 TXF-S、夜盤 TXF-P。 */
const SPOT_RE = /^TXF-[SP]$/
/** 單一月份的臺指期：TXF＋月份碼（A～L）＋年份一碼＋ -F（日盤）／-M（夜盤）；價差合約（TXFJ6/K6）不算。 */
const CONTRACT_RE = /^TXF[A-L]\d-[FM]$/

/**
 * 期交所 getQuoteList 的回應 → 臺指現貨＋近月合約（清單裡第一個單一月份，期交所依到期月排序；結算日收盤後近月換下一個）。
 * RtCode 不是 "0" 或沒有 QuoteList → 丟錯。
 */
export function parseTaifex(body: unknown, session: Session): TaifexQuotes {
  const b = body as { RtCode?: unknown; RtMsg?: unknown; RtData?: { QuoteList?: unknown } } | null
  if (!b || b.RtCode !== '0' || !Array.isArray(b.RtData?.QuoteList)) {
    throw new Error(`期交所行情回應異常（RtCode ${String(b?.RtCode ?? '?')}${typeof b?.RtMsg === 'string' && b.RtMsg ? `：${b.RtMsg}` : ''}）`)
  }
  let spot: TxSpot | null = null
  let near: TxQuote | null = null
  for (const raw of b.RtData.QuoteList as Record<string, unknown>[]) {
    const id = raw.SymbolID
    const date = ymd(raw.CDate)
    if (typeof id !== 'string' || date === null) continue
    if (spot === null && SPOT_RE.test(id)) {
      spot = { date, time: hms(raw.CTime), last: price(raw.CLastPrice), prev: price(raw.CRefPrice) }
    } else if (near === null && CONTRACT_RE.test(id)) {
      const last = price(raw.CLastPrice)
      const ref = price(raw.CRefPrice)
      near = {
        session,
        symbol: id.slice(0, id.indexOf('-')),
        name: typeof raw.DispCName === 'string' ? raw.DispCName : id,
        date,
        time: hms(raw.CTime),
        last,
        ref,
        settlement: session === 'day' ? price(raw.SettlementPrice) : null,
        volume: count(raw.CTotalVolume),
        test: price(raw.CTestPrice),
        chg: last !== null && ref !== null ? last / ref - 1 : null,
      }
    }
  }
  return { session, spot, near }
}

/** 期交所回的不是 JSON（被擋時回 HTML）→ 丟錯。 */
export async function readTaifex(res: { ok: boolean; status: number; text: () => Promise<string> }, session: Session): Promise<TaifexQuotes> {
  if (!res.ok) throw new Error(`期交所行情 HTTP ${res.status}`)
  const body = await res.text()
  let parsed: unknown
  try {
    parsed = JSON.parse(body)
  } catch {
    throw new Error('期交所行情回應不是 JSON')
  }
  return parseTaifex(parsed, session)
}
