// 雲端版（T-332）的 API：每日名單、日期清單、個股 K 線與被推薦的日子。
import { apiUrl } from '../api'
import { LoginRequired, logoutDestination, needsLogin } from './auth'
import type { Bar, DateCounts, PicksDay } from './format'
import type { SearchHit, StockScores, WatchList } from './scores'
import type { Scorecard, TradeRow } from './scorecard'
import type { Quote } from './live'
import type { RankRow } from './group'
import type { CloseMood, GlobalMood, LiveMood } from './mood'
import type { Brief, BriefActual, LiveMarket, NewsItem, WarRoomData } from './war'
import type { NewsHealth, NewsKind, NewsRow, StockNews, StockNewsCounts } from './news'
import type { NewsGraph as NewsGraphData } from './news-graph'
import type { StockCalItem } from './stockcal'
import type { HoldersWeek } from './holders'
import type { StopHit } from './stophit'
import type { WarrantDay } from './warrants'
import type { Trade, TradeList } from './trades'

const OFFLINE = '連線中斷，請重新整理頁面'

// redirect: 'manual'：沒登入時 Access 會把 /api 導去登入頁，攔下來改丟 LoginRequired，畫面切成預覽模式。
async function get<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let res: Response
  try {
    const init: RequestInit = { method, headers: { accept: 'application/json' }, redirect: 'manual', cache: 'no-store' }
    // 只有存順序（PUT）帶內容
    if (body !== undefined) {
      init.headers = { accept: 'application/json', 'content-type': 'application/json' }
      init.body = JSON.stringify(body)
    }
    res = await fetch(apiUrl(path), init)
  } catch {
    throw new Error(OFFLINE)
  }
  if (needsLogin(res)) throw new LoginRequired()
  if (!(res.headers.get('content-type') ?? '').includes('json')) throw new Error(`${res.status} ${res.statusText}`)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

/** 登出後要去的網址（規則見 auth.ts logoutDestination）。redirect: 'manual'：登入過期時 Access 的導向要攔下來判斷，
 *  不能讓瀏覽器跟著跳到 cloudflareaccess.com（跨網域會被擋成網路錯誤，分不出是過期還是斷線）。 */
export async function logoutTarget(): Promise<string> {
  const res = await fetch('/api/logout', { method: 'POST', redirect: 'manual', cache: 'no-store' }).catch(() => null)
  return logoutDestination(res, window.location.origin)
}

/** 目前登入的 email；沒登入時丟 LoginRequired。 */
export const fetchMe = () => get<{ email: string }>('/api/me')

export const fetchPicks = (date?: string) =>
  get<PicksDay>(date ? `/api/picks?date=${encodeURIComponent(date)}` : '/api/picks')

/** 有名單的日期（新到舊），附每天每條線的檔數（舊後端沒有 counts/since → null）。 */
export const fetchPickDates = () =>
  get<{ dates: string[]; counts?: DateCounts['counts']; since?: DateCounts['since'] }>('/api/picks/dates').then((r) => ({
    dates: r.dates,
    counts: r.counts && r.since ? { counts: r.counts, since: r.since } : null,
  }))

/** 有名單的交易日（公開，不含任何股票），讓沒登入的人也能切到歷史名單選日期。 */
export const fetchPublicDates = () => get<{ dates: string[] }>('/pub/dates').then((r) => r.dates)

export const fetchBars = (sid: string, days = 500) =>
  get<{ rows: Bar[] }>(`/api/stocks/${encodeURIComponent(sid)}/ohlc?days=${days}`).then((r) => r.rows)

export const fetchStockPicks = (sid: string) =>
  get<{ days: Record<string, string[]> }>(`/api/stocks/${encodeURIComponent(sid)}/picks`).then((r) => r.days)

/** 這一檔的行事曆（使用者 2026-10-17）：過去一年到往後 120 天的除權息、法說會、分割減資。 */
export const fetchStockCalendar = (sid: string) =>
  get<{ sid: string; today: string; items: StockCalItem[] }>(`/api/stocks/${encodeURIComponent(sid)}/calendar`)

/** 這一檔的波動度落在哪一組、那組 20 日內碰 −15%／+20% 的歷史比例（D-191 §三 C）；收盤不足是 null。 */
export const fetchStockStopHit = (sid: string) =>
  get<{ sid: string; today: string; hit: StopHit | null }>(`/api/stocks/${encodeURIComponent(sid)}/stophit`)

/** 這一檔最近幾天的權證摘要（T-385）；沒有權證或還沒算是空陣列。 */
export const fetchStockWarrants = (sid: string) =>
  get<{ sid: string; today: string; days: WarrantDay[] }>(`/api/stocks/${encodeURIComponent(sid)}/warrants`)

/** 這一檔最近幾週的千張大戶比例（T-386）；還沒累積到資料是空陣列。 */
export const fetchStockHolders = (sid: string) =>
  get<{ sid: string; today: string; weeks: HoldersWeek[] }>(`/api/stocks/${encodeURIComponent(sid)}/holders`)

/** 三個模型最近幾天的分數與名次，加上最新一天在哪條線的名單上。 */
export const fetchScores = (sid: string) => get<StockScores>(`/api/stocks/${encodeURIComponent(sid)}/scores`)

/** 代號開頭或名稱含這幾個字的股票（最多 10 檔）。 */
export const searchStocks = (q: string) =>
  get<{ items: SearchHit[] }>(`/api/stocks/search?q=${encodeURIComponent(q)}`).then((r) => r.items)

/** 登入者的追蹤股（新加的在前），附最新收盤與三個模型的分數。 */
export const fetchWatchlist = () => get<WatchList>('/api/watchlist')

/** 加入（on）或移除追蹤股；兩個都冪等。 */
export const setTracked = (sid: string, on: boolean) =>
  get<{ ok: boolean }>(`/api/watchlist/${encodeURIComponent(sid)}`, on ? 'PUT' : 'DELETE')

/** 存追蹤股的順序（由上到下的代號）。 */
export const saveWatchOrder = (order: string[]) => get<{ ok: boolean }>('/api/watchlist', 'PUT', { order })

/** 我的下單（T-388）：列出、記一筆（同代號同日同方向覆蓋）、刪一筆。 */
export const fetchTrades = () => get<TradeList>('/api/trades')
export const saveTrade = (t: Omit<Trade, 'created_at'>) => get<{ ok: boolean }>('/api/trades', 'PUT', t)
export const deleteTrade = (k: { sid: string; date: string; side: Trade['side'] }) => get<{ ok: boolean }>('/api/trades', 'DELETE', k)

/** 盤中即時量價（證交所，約 5 秒更新）；最多 100 檔。 */
export const fetchQuotes = (sids: string[]) =>
  get<{ quotes: Quote[]; stale?: boolean; retry_after?: number | null }>(`/api/quotes?sids=${encodeURIComponent(sids.join(','))}`)

/** 這台裝置開通知：存瀏覽器給的推播訂閱。 */
export const savePushSubscription = (sub: PushSubscriptionJSON) => get<{ ok: boolean }>('/api/push', 'POST', sub)

/** 這台裝置關通知。 */
export const deletePushSubscription = (endpoint: string) => get<{ ok: boolean }>('/api/push', 'DELETE', { endpoint })

/** 名單績效（D-176）：這條線的模擬帳戶與逐筆統計（不含明細）。 */
/** 市場氛圍（D-181）盤中版：類股指數強弱；開盤前 live 是 null。 */
export const fetchMoodLive = () => get<{ live: LiveMood | null; stale: boolean; retry_after: number | null }>('/api/mood/live')
/** 市場氛圍收盤版：最近一天的族群資金；還沒算過 close 是 null。 */
export const fetchMoodClose = () => get<{ close: CloseMood | null }>('/api/mood/close').then((r) => r.close)
/** 市場氛圍期貨與美股版（D-186）：台指期日盤／夜盤、加權現貨、美股四大指數；stale＝有來源沒更新到、給的是上一份。 */
export const fetchMoodGlobal = () => get<{ global: GlobalMood; stale: boolean; errors: string[] }>('/api/mood/global')
export const fetchScorecard = (line: string) => get<Scorecard>(`/api/scorecard?line=${encodeURIComponent(line)}`)

/** 首頁戰情室（T-379）：晨報、大盤體檢、期貨籌碼、名單速覽、昨日消息、行事曆。 */
export const fetchWarroom = () => get<WarRoomData>('/api/warroom')
/** 新聞分頁（T-400）：新聞流；until 含這個時間（翻頁用）。 */
export function fetchNewsItems(q: { kind: NewsKind | null; limit: number; until?: string }) {
  const p = new URLSearchParams({ limit: String(q.limit) })
  if (q.kind) p.set('kind', q.kind)
  if (q.until) p.set('until', q.until)
  return get<{ items: NewsRow[] }>(`/api/news/items?${p}`)
}
/** 一則新聞的關係圖（T-402／T-404）：提到的實體往外三度；沒有這則 → 404。 */
export const fetchNewsGraph = (id: string) => get<NewsGraphData>(`/api/news/graph?id=${encodeURIComponent(id)}`)
/** 反向看圖：某一檔近 7 天連得到的新聞（個股頁「相關新聞」）。 */
export const fetchStockNews = (sid: string) => get<StockNews>(`/api/news/stock?id=${encodeURIComponent(`tw:${sid}`)}`)
/** 名單小標：一批股票近 3 天各有幾則相關新聞；鍵是 tw:代號。 */
export const fetchStockNewsCounts = (sids: string[]) =>
  get<StockNewsCounts>(`/api/news/stock-counts?ids=${encodeURIComponent(sids.map((s) => `tw:${s}`).join(','))}`)
/** 新聞服務最後抓取時間與各來源狀態。 */
export const fetchNewsHealth = () => get<NewsHealth>('/api/news/health')
/** 盤中大盤體檢（T-397）：排程每分鐘更新；今天還沒有 → live 是 null。 */
export const fetchMarketLive = () => get<{ live: LiveMarket | null }>('/api/warroom/live')
/** 首頁版面（使用者 2026-10-17：依帳號儲存卡片位置與收起）：沒存過 → layout 是 null。 */
export const fetchLayout = () => get<{ layout: { order: string[]; collapsed: string[] } | null }>('/api/layout')
export const saveLayout = (layout: { order: string[]; collapsed: string[] }) => get<{ ok: boolean }>('/api/layout', 'PUT', layout)
/** 恢復預設：刪掉帳號裡存的版面。 */
export const resetLayout = () => get<{ ok: boolean }>('/api/layout', 'DELETE')
/** 過去某天的「今日一句話」與那天大盤實際收盤（使用者 2026-10-17：選日期看歷史）。 */
export const fetchBriefDay = (date: string, edition: 'morning' | 'evening' = 'morning') =>
  get<{ date: string; brief: Brief | null; actual: BriefActual | null }>(`/api/warroom/brief?date=${date}&edition=${edition}`)
/** 追蹤股自 since 起的重大訊息（T-379）。 */
export const fetchWarroomNews = (sids: string[], since: string) =>
  get<{ since: string; items: NewsItem[] }>(`/api/warroom/news?sids=${encodeURIComponent(sids.join(','))}&since=${since}`)

/** 族群排行（D-177 修訂）：最近一天每個族群的資金倍數、漲跌家數與成交金額最大的幾檔。 */
export const fetchGroups = () => get<{ date: string; rows: RankRow[] }>('/api/groups')

/** 名單績效的逐筆明細（新到舊）。 */
export const fetchScorecardTrades = (line: string) =>
  get<{ rows: TradeRow[] }>(`/api/scorecard/trades?line=${encodeURIComponent(line)}`).then((r) => r.rows)
