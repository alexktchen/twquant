// 盤中即時量價（T-347）：證交所「基本市況報導」的 getStockInfo，約 5 秒更新一次。
// 不是正式開放的介面：格式不對就丟錯（端點回 503），網站照樣顯示資料庫的收盤價。純函數，供 node 單元測試。
import { SID_RE } from './picks.ts'

export const MIS_URL = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp'
/** 一次問幾檔：每檔上市、上櫃各問一個頻道。2026-10-13 實測 75 檔（150 個頻道）可以、100 檔（200 個）被拒，留餘裕取 50。 */
export const MIS_BATCH = 50
/** 一次最多查幾檔（＝追蹤股上限）。 */
export const QUOTE_MAX = 100

export type Quote = {
  sid: string
  /** 這份報價是哪一天（YYYY-MM-DD）；假日問到的是上一個交易日 */
  date: string
  /** 報價時間 HH:MM:SS */
  time: string | null
  open: number | null
  high: number | null
  low: number | null
  /** 最近一筆成交價；還沒成交 → null */
  last: number | null
  /** 昨收（今天的漲跌參考價） */
  prev: number | null
  /** 今天累計成交量，單位「股」（證交所給「張」，這裡 ×1000，與資料庫一致） */
  volume: number | null
  limit_up: number | null
  limit_down: number | null
  /** 收盤前集中撮合（13:25–13:30）：證交所不給即時價，last 是撮合前最後一筆成交 */
  auction: boolean
}

/** 代號清單：逗號分隔、去重複、格式正確、1～QUOTE_MAX 檔；不合格 → null。 */
export function parseSids(raw: string | null): string[] | null {
  const sids = [...new Set((raw ?? '').split(',').map((s) => s.trim()).filter(Boolean))]
  if (sids.length === 0 || sids.length > QUOTE_MAX || !sids.every((s) => SID_RE.test(s))) return null
  return sids
}

/** 每 MIS_BATCH 檔一次請求的網址。不知道是上市還是上櫃，兩個市場都問：問錯市場的頻道只回空殼，parseMis 會略過。 */
export function misUrls(sids: string[]): string[] {
  const urls: string[] = []
  for (let i = 0; i < sids.length; i += MIS_BATCH) {
    const ch = sids.slice(i, i + MIS_BATCH).flatMap((s) => [`tse_${s}.tw`, `otc_${s}.tw`])
    urls.push(`${MIS_URL}?ex_ch=${encodeURIComponent(ch.join('|'))}&json=1&delay=0`)
  }
  return urls
}

// 價格：證交所用 "-" 表示沒有；0 或負數也當沒有（不補 0）
const price = (v: unknown): number | null => {
  if (typeof v !== 'string' || v === '-' || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? n : null
}
const lots = (v: unknown): number | null => {
  if (typeof v !== 'string' || v === '-' || v === '') return null
  const n = Number(v)
  return Number.isInteger(n) && n >= 0 ? n * 1000 : null
}

/** 解析一份 getStockInfo 回應。rtcode 不是 0000（例：一次問太多檔）或沒有 msgArray → 丟錯。 */
export function parseMis(body: unknown): Quote[] {
  const b = body as { rtcode?: unknown; msgArray?: unknown } | null
  if (!b || b.rtcode !== '0000' || !Array.isArray(b.msgArray)) {
    throw new Error(`證交所即時報價回應異常（rtcode=${String(b?.rtcode)}）`)
  }
  const out: Quote[] = []
  for (const raw of b.msgArray as Record<string, unknown>[]) {
    const d = raw.d
    // 問錯市場的頻道：回一個沒有代號、日期的空殼
    if (typeof raw.c !== 'string' || typeof d !== 'string' || !/^\d{8}$/.test(d)) continue
    const z = price(raw.z)
    const trade = raw.trade as { z?: unknown } | undefined
    const tradeZ = price(trade?.z)
    out.push({
      sid: raw.c,
      date: `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`,
      time: typeof raw.t === 'string' && /^\d{2}:\d{2}:\d{2}$/.test(raw.t) ? raw.t : null,
      open: price(raw.o),
      high: price(raw.h),
      low: price(raw.l),
      last: z ?? tradeZ,
      prev: price(raw.y),
      volume: lots(raw.v),
      limit_up: price(raw.u),
      limit_down: price(raw.w),
      auction: z === null && tradeZ !== null,
    })
  }
  return out
}

// ── 限流與快取（2026-10-14：證交所防火牆擋下，「因為安全性考量…」）────────────────────────
// 證交所對同一個 IP 的請求有頻率限制（坊間常見說法：5 秒 3 次），超過就整個 IP 擋一段時間，
// 而且被擋時繼續打只會一直被擋。所以：
//   ① 以「每一檔」為單位快取：名單、個股頁、追蹤清單問到同一檔時共用，不再各問一次；
//   ② 同一個執行個體兩次請求證交所至少隔 MIN_GAP_MS，太密就先給快取（可能舊一點）；
//   ③ 被擋（HTTP 錯誤、回 HTML、連線被斷）就暫停 COOLDOWN_MS，連續被擋加倍到 MAX_COOLDOWN_MS，期間只給快取。

export const FRESH_MS = 8_000
export const MIN_GAP_MS = 2_000
export const STALE_MAX_MS = 15 * 60_000
export const COOLDOWN_MS = 60_000
export const MAX_COOLDOWN_MS = 10 * 60_000

/** 回給網站的結果：stale＝有的報價是舊的（證交所暫停或請求太密）；retryAfter＝暫停還有幾秒（沒暫停、或富果補得上時 null）。 */
export type HubResult = { quotes: Quote[]; stale: boolean; retryAfter: number | null }

// ── 富果備援（2026-10-14）：證交所擋下時才用，一檔一次請求 ─────────────────────────────
// 富果免費方案（基本用戶）日內行情 API 每分鐘 60 次、同一把金鑰共用：每個執行個體只用 FUGLE_PER_MIN，
// 留給其他執行個體與重試；補到的價格 FUGLE_FRESH_MS 內不重問。被富果擋（429）或金鑰不對（401／403）就停 FUGLE_COOLDOWN_MS。
export const FUGLE_URL = 'https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/'
export const FUGLE_PER_MIN = 40
/** 一次請求最多補幾檔（Cloudflare 免費方案每次請求最多 50 個對外連線） */
export const FUGLE_PER_REQUEST = 15
export const FUGLE_FRESH_MS = 30_000
export const FUGLE_COOLDOWN_MS = 60_000

/** 一檔的富果報價；代號不存在 → null。 */
export type Fallback = (sid: string) => Promise<Quote | null>

export class QuoteHub {
  private cache = new Map<string, { at: number; ttl: number; quote: Quote | null }>()
  private lastFetch = -Infinity
  private blockedUntil = -Infinity
  private cooldown = COOLDOWN_MS
  private fugleTokens = FUGLE_PER_MIN
  private fugleAt = -Infinity
  private fugleBlockedUntil = -Infinity
  private readonly fetchMis: (sids: string[]) => Promise<Quote[]>
  private readonly now: () => number

  constructor(fetchMis: (sids: string[]) => Promise<Quote[]>, now: () => number = Date.now) {
    this.fetchMis = fetchMis
    this.now = now
  }

  /**
   * 這幾檔的報價：新鮮的直接給；其餘在沒暫停、沒太密時問證交所；證交所擋下時，有 fallback（富果）就在額度內補，
   * 否則給還不太舊的快取。全都給不出來才丟錯。
   */
  async get(sids: string[], fallback?: Fallback): Promise<HubResult> {
    const t = this.now()
    const due = (s: string) => {
      const hit = this.cache.get(s)
      return !hit || t - hit.at >= hit.ttl
    }
    const need = sids.filter(due)
    let error: Error | null = null
    if (need.length > 0 && t >= this.blockedUntil && t - this.lastFetch >= MIN_GAP_MS) {
      this.lastFetch = t
      try {
        const got = await this.fetchMis(need)
        const by = new Map(got.map((q) => [q.sid, q]))
        // 問了但證交所沒回的（例：興櫃、代號錯）也記下來，免得每次都再問
        for (const s of need) this.cache.set(s, { at: t, ttl: FRESH_MS, quote: by.get(s) ?? null })
        this.cooldown = COOLDOWN_MS
        this.prune(t)
      } catch (e) {
        error = e instanceof Error ? e : new Error(String(e))
        this.blockedUntil = t + this.cooldown
        this.cooldown = Math.min(this.cooldown * 2, MAX_COOLDOWN_MS)
      }
    }
    const misDown = t < this.blockedUntil
    const fugleUp = fallback !== undefined && t >= this.fugleBlockedUntil
    if (misDown && fugleUp) await this.fillFromFugle(sids.filter(due), fallback, t)
    const quotes: Quote[] = []
    let stale = false
    let missing = 0
    for (const s of sids) {
      const hit = this.cache.get(s)
      if (!hit || t - hit.at >= STALE_MAX_MS) {
        missing += 1
        continue
      }
      if (t - hit.at >= hit.ttl) stale = true
      if (hit.quote) quotes.push(hit.quote)
    }
    // 富果還補得上就不要網站停下來等；兩個都不行才叫網站晚點再來
    const retryAfter = misDown && !(fallback !== undefined && t >= this.fugleBlockedUntil) ? Math.ceil((this.blockedUntil - t) / 1000) : null
    if (missing === sids.length && sids.length > 0) {
      const why = error?.message ?? (misDown ? '證交所暫時擋下即時報價' : '請求太密，稍後再試')
      throw Object.assign(new Error(why), { retryAfter })
    }
    return { quotes, stale: stale || missing > 0, retryAfter }
  }

  /** 富果補價：沒有的先補、再補最舊的；每次最多 FUGLE_PER_REQUEST 檔，而且不超過這分鐘剩下的額度。 */
  private async fillFromFugle(due: string[], fallback: Fallback, t: number): Promise<void> {
    this.fugleTokens = Math.min(FUGLE_PER_MIN, this.fugleTokens + ((t - this.fugleAt) * FUGLE_PER_MIN) / 60_000)
    this.fugleAt = t
    const order = [...due].sort((a, b) => (this.cache.get(a)?.at ?? -Infinity) - (this.cache.get(b)?.at ?? -Infinity))
    const take = order.slice(0, Math.min(FUGLE_PER_REQUEST, Math.floor(this.fugleTokens)))
    if (take.length === 0) return
    this.fugleTokens -= take.length
    const got = await Promise.allSettled(take.map((s) => fallback(s)))
    got.forEach((r, i) => {
      if (r.status === 'fulfilled') this.cache.set(take[i], { at: t, ttl: FUGLE_FRESH_MS, quote: r.value })
      else if ([401, 403, 429].includes((r.reason as { status?: number }).status ?? 0)) this.fugleBlockedUntil = t + FUGLE_COOLDOWN_MS
    })
  }

  private prune(t: number): void {
    for (const [k, v] of this.cache) if (t - v.at >= STALE_MAX_MS) this.cache.delete(k)
  }
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : null)

/** 微秒時間戳（富果）→ 台北時間 HH:MM:SS（台灣沒有日光節約，固定 +8）。 */
function taipeiTime(us: unknown): string | null {
  if (typeof us !== 'number' || !Number.isFinite(us) || us <= 0) return null
  const d = new Date(Math.floor(us / 1000) + 8 * 3600_000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`
}

/**
 * 富果 /stock/intraday/quote/{代號} → 與證交所相同形狀的 Quote。成交量富果給「張」，這裡 ×1000 成「股」；
 * 漲跌停價不在這支 API（另一支要多花額度），給 null，網站由昨收照升降單位推。格式不對 → 丟錯。
 */
export function parseFugle(body: unknown): Quote {
  const b = body as Record<string, unknown> | null
  const date = b?.date
  if (!b || typeof b.symbol !== 'string' || typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    throw new Error('富果即時報價回應異常（沒有代號或日期）')
  }
  const total = b.total as { tradeVolume?: unknown } | undefined
  const lots = typeof total?.tradeVolume === 'number' && Number.isInteger(total.tradeVolume) && total.tradeVolume >= 0 ? total.tradeVolume : null
  const trade = b.lastTrade as { time?: unknown } | undefined
  return {
    sid: b.symbol,
    date,
    time: taipeiTime(trade?.time ?? b.lastUpdated),
    open: num(b.openPrice),
    high: num(b.highPrice),
    low: num(b.lowPrice),
    last: num(b.lastPrice) ?? num(b.closePrice),
    prev: num(b.referencePrice) ?? num(b.previousClose),
    volume: lots === null ? null : lots * 1000,
    limit_up: null,
    limit_down: null,
    // 收盤前集中撮合（13:25–13:30）富果標 isTrial：成交價停在撮合前，與證交所的 auction 同義
    auction: b.isTrial === true,
  }
}

/** 富果回應 → Quote；代號不存在（404）→ null；其他錯誤帶 status 丟出去（429／401 讓 QuoteHub 停用富果一陣子）。 */
export async function readFugle(res: { ok: boolean; status: number; json: () => Promise<unknown> }): Promise<Quote | null> {
  if (res.status === 404) return null
  if (!res.ok) throw Object.assign(new Error(`富果即時報價 HTTP ${res.status}`), { status: res.status })
  return parseFugle(await res.json())
}

/** 證交所回的不是 JSON（被防火牆擋下時回一頁 HTML「因為安全性考量…」）→ 丟錯，讓 QuoteHub 暫停。 */
export async function readMis(res: { ok: boolean; status: number; text: () => Promise<string> }): Promise<Quote[]> {
  if (!res.ok) throw new Error(`證交所即時報價 HTTP ${res.status}`)
  const body = await res.text()
  let parsed: unknown
  try {
    parsed = JSON.parse(body)
  } catch {
    throw new Error(body.includes('安全性') ? '證交所防火牆暫時擋下即時報價' : '證交所即時報價回應不是 JSON')
  }
  return parseMis(parsed)
}
