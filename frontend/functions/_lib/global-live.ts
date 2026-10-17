// 期貨與美股的共用抓取（D-186）：期交所日盤、夜盤各一次 ＋ Yahoo 四個指數各一次，同一個執行個體裡一份記憶——
// 兩輪抓取至少隔 MIN_GAP_MS（錯誤不進 60 秒回應暫存，別讓重試打成一串）；哪個來源沒拿到就留它上一份（最多 STALE_MAX_MS），
// 期交所擋下不影響美股、Yahoo 429 不影響期貨。全部都沒有才丟錯。
import { globalMood, type GlobalMood } from './mood.ts'
import { TAIFEX_URL, readTaifex, taifexBody, type Session, type TaifexQuotes } from './taifex.ts'
import { US_INDICES, readYahoo, yahooUrl, type UsIndex } from './yahoo.ts'

/** 兩輪抓取至少隔多久。 */
export const MIN_GAP_MS = 15_000
/** 超過這麼久沒更新成功就算舊的（回應標 stale，網站寫「更新暫停」）。 */
export const FRESH_MS = 90_000
/** 舊到這麼久就不給了（夜盤到隔天開盤之間都還拿得到上一份）。 */
export const STALE_MAX_MS = 6 * 3600_000

export type Fetchers = {
  taifex: (session: Session) => Promise<TaifexQuotes>
  yahoo: (symbol: string, name: string) => Promise<UsIndex>
}
export type GlobalResult = { global: GlobalMood; stale: boolean; errors: string[] }

type Piece<T> = { at: number; value: T }

export class GlobalHub {
  private tx = new Map<Session, Piece<TaifexQuotes>>()
  private us = new Map<string, Piece<UsIndex>>()
  private lastFetch = -Infinity
  private lastErrors: string[] = []
  private readonly f: Fetchers
  private readonly now: () => number

  constructor(f: Fetchers, now: () => number = Date.now) {
    this.f = f
    this.now = now
  }

  /** 到時間就整輪重抓（六個請求同時發、各自成敗），然後把手上還不太舊的拼起來。 */
  async get(): Promise<GlobalResult> {
    const t = this.now()
    if (t - this.lastFetch >= MIN_GAP_MS) {
      this.lastFetch = t
      const sessions: Session[] = ['day', 'night']
      const jobs: Promise<void>[] = [
        ...sessions.map((s) => this.f.taifex(s).then((v) => void this.tx.set(s, { at: t, value: v }))),
        ...US_INDICES.map((x) => this.f.yahoo(x.symbol, x.name).then((v) => void this.us.set(x.symbol, { at: t, value: v }))),
      ]
      const done = await Promise.allSettled(jobs)
      this.lastErrors = done.flatMap((r) => (r.status === 'rejected' ? [r.reason instanceof Error ? r.reason.message : String(r.reason)] : []))
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
    return { global: globalMood(day, night, us), stale, errors: this.lastErrors }
  }
}

const fetchTaifex = async (session: Session): Promise<TaifexQuotes> =>
  readTaifex(
    await fetch(TAIFEX_URL, {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json', 'user-agent': 'Mozilla/5.0', referer: 'https://mis.taifex.com.tw/futures/' },
      body: taifexBody(session),
    }),
    session,
  )

const fetchYahoo = async (symbol: string, name: string): Promise<UsIndex> =>
  readYahoo(await fetch(yahooUrl(symbol), { headers: { accept: 'application/json', 'user-agent': 'Mozilla/5.0' } }), name)

export const globalHub = new GlobalHub({ taifex: fetchTaifex, yahoo: fetchYahoo })
