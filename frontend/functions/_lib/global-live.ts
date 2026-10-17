// 期貨與美股的共用抓取（D-186）：期交所日盤、夜盤各一次 ＋ Yahoo 四個指數各一次，同一個執行個體裡一份記憶——
// 兩輪抓取至少隔 MIN_GAP_MS（錯誤不進 60 秒回應暫存，別讓重試打成一串）；哪個來源沒拿到就留它上一份（最多 STALE_MAX_MS），
// 期交所擋下不影響美股、Yahoo 429 不影響期貨。全部都沒有才丟錯。
// 期交所拿不到時，有富果金鑰就改問富果的臺指期近月（D-186 修訂：上線第一天正式站整行台指期都沒出來）。
// 每個請求最多等 FETCH_TIMEOUT_MS：期交所會整個卡住不回（2026-10-17 實測 20 秒逾時），不能讓整份回應陪它等。
import { globalMood, type GlobalMood } from './mood.ts'
import {
  TAIFEX_URL,
  fugleNearSymbol,
  fugleQuoteUrl,
  fugleTickersUrl,
  parseFugleFut,
  readFugleJson,
  readTaifex,
  taifexBody,
  type Session,
  type TaifexQuotes,
  type TxQuote,
} from './taifex.ts'
import { US_INDICES, readYahoo, yahooUrl, type UsIndex } from './yahoo.ts'

/** 兩輪抓取至少隔多久。 */
export const MIN_GAP_MS = 15_000
/** 超過這麼久沒更新成功就算舊的（回應標 stale，網站寫「更新暫停」）。 */
export const FRESH_MS = 90_000
/** 舊到這麼久就不給了（夜盤到隔天開盤之間都還拿得到上一份）。 */
export const STALE_MAX_MS = 6 * 3600_000
/** 每個對外請求最多等多久。 */
export const FETCH_TIMEOUT_MS = 6_000
/** 富果擋下（429）或金鑰不對（401／403）就停多久。 */
export const FUGLE_COOLDOWN_MS = 60_000

export type Fetchers = {
  taifex: (session: Session) => Promise<TaifexQuotes>
  yahoo: (symbol: string, name: string) => Promise<UsIndex>
}
/** 期交所拿不到時的備援：給一節的近月行情。 */
export type FutFallback = (session: Session) => Promise<TxQuote>
export type GlobalResult = { global: GlobalMood; stale: boolean; errors: string[] }

type Piece<T> = { at: number; value: T }

const message = (e: unknown): string => (e instanceof Error ? e.message : String(e))

export class GlobalHub {
  private tx = new Map<Session, Piece<TaifexQuotes>>()
  private us = new Map<string, Piece<UsIndex>>()
  private lastFetch = -Infinity
  private lastErrors: string[] = []
  private fugleBlockedUntil = -Infinity
  private readonly f: Fetchers
  private readonly now: () => number

  constructor(f: Fetchers, now: () => number = Date.now) {
    this.f = f
    this.now = now
  }

  /** 一節台指期：先問期交所，失敗而且有備援（沒被富果擋）就問備援；備援的結果沿用上一份的現貨點數。 */
  private async session(s: Session, t: number, fallback: FutFallback | undefined, errors: string[]): Promise<TaifexQuotes> {
    try {
      return await this.f.taifex(s)
    } catch (e) {
      errors.push(message(e))
      if (fallback === undefined || t < this.fugleBlockedUntil) throw e
      try {
        const near = await fallback(s)
        return { session: s, spot: this.tx.get(s)?.value.spot ?? null, near }
      } catch (e2) {
        if ([401, 403, 429].includes((e2 as { status?: number }).status ?? 0)) this.fugleBlockedUntil = t + FUGLE_COOLDOWN_MS
        errors.push(message(e2))
        throw e2
      }
    }
  }

  /** 到時間就整輪重抓（六個請求同時發、各自成敗），然後把手上還不太舊的拼起來。 */
  async get(fallback?: FutFallback): Promise<GlobalResult> {
    const t = this.now()
    if (t - this.lastFetch >= MIN_GAP_MS) {
      this.lastFetch = t
      const errors: string[] = []
      const sessions: Session[] = ['day', 'night']
      const jobs: Promise<void>[] = [
        ...sessions.map((s) => this.session(s, t, fallback, errors).then((v) => void this.tx.set(s, { at: t, value: v }))),
        ...US_INDICES.map((x) =>
          this.f.yahoo(x.symbol, x.name).then(
            (v) => void this.us.set(x.symbol, { at: t, value: v }),
            (e) => void errors.push(message(e)),
          ),
        ),
      ]
      await Promise.allSettled(jobs)
      this.lastErrors = [...new Set(errors)]
    }
    const pick = <T>(p: Piece<T> | undefined): T | null => (p !== undefined && t - p.at < STALE_MAX_MS ? p.value : null)
    const day = pick(this.tx.get('day'))
    const night = pick(this.tx.get('night'))
    const us = US_INDICES.flatMap((x) => {
      const v = pick(this.us.get(x.symbol))
      return v ? [v] : []
    })
    if (day === null && night === null && us.length === 0) {
      throw new Error(this.lastErrors[0] ?? '期貨與美股行情暫時取不到')
    }
    const pieces = [this.tx.get('day'), this.tx.get('night'), ...US_INDICES.map((x) => this.us.get(x.symbol))]
    const stale = pieces.some((p) => p === undefined || t - p.at >= FRESH_MS)
    const global = globalMood(day, night, us)
    // 兩節都沒有：網站照樣畫「台指期」那一行，寫出取不到的原因（不要整行消失）
    if (global.day === null && global.night === null) global.tx_error = this.lastErrors.find((e) => /期交所|富果/.test(e)) ?? '期交所沒有回應'
    return { global, stale, errors: this.lastErrors }
  }
}

const timeout = () => AbortSignal.timeout(FETCH_TIMEOUT_MS)

/** 逾時（AbortSignal）轉成看得懂的錯誤。 */
async function guard<T>(who: string, run: () => Promise<T>): Promise<T> {
  try {
    return await run()
  } catch (e) {
    if (e instanceof Error && (e.name === 'TimeoutError' || e.name === 'AbortError')) throw new Error(`${who}沒有回應（等了 ${FETCH_TIMEOUT_MS / 1000} 秒）`)
    throw e
  }
}

const fetchTaifex = (session: Session): Promise<TaifexQuotes> =>
  guard('期交所', async () =>
    readTaifex(
      await fetch(TAIFEX_URL, {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json', 'user-agent': 'Mozilla/5.0', referer: 'https://mis.taifex.com.tw/futures/' },
        body: taifexBody(session),
        signal: timeout(),
      }),
      session,
    ),
  )

const fetchYahoo = (symbol: string, name: string): Promise<UsIndex> =>
  guard('Yahoo ', async () =>
    readYahoo(await fetch(yahooUrl(symbol), { headers: { accept: 'application/json', 'user-agent': 'Mozilla/5.0' }, signal: timeout() }), name),
  )

/** 台北現在的日期與時間（HH:MM）。 */
function taipeiClock(ms: number): { date: string; time: string } {
  const iso = new Date(ms + 8 * 3600_000).toISOString()
  return { date: iso.slice(0, 10), time: iso.slice(11, 16) }
}

/**
 * 找近月要用的「今天」：夜盤 15:00 以後算隔天——結算日（第三個星期三）當晚的夜盤，到期的那個合約已經不交易了。
 */
export function nearAsOf(session: Session, nowMs: number): string {
  const { date, time } = taipeiClock(nowMs)
  if (session === 'night' && time >= '15:00') return new Date(Date.parse(`${date}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10)
  return date
}

// 近月代號一天問一次（每節各一份），省富果的額度
const nearCache = new Map<string, string>()

/** 富果備援：先找近月代號（快取到換日），再問那一節的行情。金鑰放 header，不進網址。 */
export const fugleFut =
  (key: string, nowMs: () => number = Date.now): FutFallback =>
  (session) =>
    guard('富果', async () => {
      const headers = { accept: 'application/json', 'X-API-KEY': key }
      const asOf = nearAsOf(session, nowMs())
      const ck = `${session}|${asOf}`
      let symbol = nearCache.get(ck)
      if (symbol === undefined) {
        symbol = fugleNearSymbol(await readFugleJson(await fetch(fugleTickersUrl(session), { headers, signal: timeout() })), asOf)
        for (const k of nearCache.keys()) if (!k.endsWith(`|${asOf}`)) nearCache.delete(k)
        nearCache.set(ck, symbol)
      }
      return parseFugleFut(await readFugleJson(await fetch(fugleQuoteUrl(symbol, session), { headers, signal: timeout() })), session)
    })

export const globalHub = new GlobalHub({ taifex: fetchTaifex, yahoo: fetchYahoo })
