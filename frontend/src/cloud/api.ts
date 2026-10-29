// 雲端版（T-332）的 API：每日名單、日期清單、個股 K 線與被推薦的日子。
import { apiUrl } from '../api'
import { LoginRequired, loginReason, needsLogin } from './auth'
import { getToken } from './session-client'
import type { Bar, DateCounts, PicksDay } from './format'
import type { SearchHit, StockScores, WatchList } from './scores'
import type { Scorecard, TradeRow } from './scorecard'
import { normalizeDay, type BoardOnlyDay, type DayTradeDay, type DayTradeHistory, type DayTradeRow } from './daytrade'
import type { Flow, FuturesDay, FuturesDayRow, Product, QuoteDayRow, QuotePayload, QuoteProduct, SecondsPayload } from './futures'
import type { Quote } from './live'
import type { RankRow } from './group'
import type { CloseMood, GlobalMood, LiveMood } from './mood'
import type { Brief, BriefActual, LiveMarket, NewsItem, WarRoomData } from './war'
import type { NewsDigest, NewsEvidence, NewsHealth, NewsKind, NewsReview, NewsSummary, NewsThread, StockNewsDays, NewsRow, NewsSearch, ReviewDecision, ReviewResult, SimilarNews, StockNews, StockNewsCounts } from './news'
import type { NewsGraph as NewsGraphData } from './news-graph'
import type { GalaxyData } from './news-galaxy'
import type { StockCalItem } from './stockcal'
import type { HoldersWeek } from './holders'
import type { StopHit } from './stophit'
import type { WarrantDay } from './warrants'
import type { Trade, TradeList } from './trades'
import type { Feature } from './access'
import type { LineGroupRow, LineLogin, LineStatus } from './linegroup'

const OFFLINE = '連線中斷，請重新整理頁面'

// 每個 /api 請求帶 Supabase 的 Bearer 權杖（T-439；快到期會先換新）；/pub 公開不用帶。
// redirect: 'manual'：過渡期 Access 會把沒登入的 /api 導去登入頁，攔下來改丟 LoginRequired，畫面切成預覽模式。
async function get<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let res: Response
  try {
    const token = path.startsWith('/pub/') ? null : await getToken()
    const headers: Record<string, string> = { accept: 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) }
    // /pub 的資料每個人都一樣（新聞、日期），讓瀏覽器照伺服器給的 max-age 留一下；其他一律 no-store（登出後不能留在快取裡）
    const init: RequestInit = { method, headers, redirect: 'manual', cache: path.startsWith('/pub/') ? 'default' : 'no-store' }
    // 只有存順序（PUT）帶內容
    if (body !== undefined) {
      headers['content-type'] = 'application/json'
      init.body = JSON.stringify(body)
    }
    res = await fetch(apiUrl(path), init)
  } catch {
    throw new Error(OFFLINE)
  }
  if (needsLogin(res)) throw new LoginRequired(loginReason(res))
  if (!(res.headers.get('content-type') ?? '').includes('json')) throw new Error(`${res.status} ${res.statusText}`)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

/** 目前登入的 email；沒登入時丟 LoginRequired。 */
export const fetchMe = () => get<{ email: string }>('/api/me')

/** 登入者能用哪些功能（T-423；決定要不要顯示「LINE 群組」分頁）。 */
export const fetchAccess = () => get<{ features: Feature[] }>('/api/access')

/** 「LINE 群組」分頁的資料（只有 line 功能的角色拿得到）。 */
export const fetchLineStatus = () => get<LineStatus>('/api/line')

/** 從網站登入 LINE 發文帳號：觸發 GitHub Actions，QR 與 PIN 之後從 fetchLineStatus 的 login 讀。 */
export const startLineLogin = () => get<LineLogin | null>('/api/line/login', 'POST', {})

/** 同步群組清單（觸發 GitHub Actions，約 1 分鐘後 fetchLineStatus 的 groups 才會更新）。 */
export const syncLineGroups = () => get<{ ok: boolean }>('/api/line/sync', 'POST', {})

/** 手動發送一張圖卡（觸發 GitHub Actions，約 1～2 分鐘後出現在 fetchLineStatus 的 posts）。 */
export const postLineCard = (kind: string, date: string) => get<{ ok: boolean }>('/api/line/post', 'POST', { kind, date })

/** 勾／取消要發的群組。 */
export const setLineGroup = (homeId: string, enabled: boolean) =>
  get<LineGroupRow>(`/api/line/groups/${encodeURIComponent(homeId)}`, 'PUT', { enabled })

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

/** 小台模擬（T-450／D-220）：那一天的整份狀態（不給日期＝最新一天）。只給 admin。 */
export const fetchFuturesDay = (day?: string) =>
  get<FuturesDay>(day ? `/api/futures?day=${encodeURIComponent(day)}` : '/api/futures')

/** 小台模擬有紀錄的交易日（新到舊）。只給 admin。 */
export const fetchFuturesDays = () => get<{ days: FuturesDayRow[] }>('/api/futures/days').then((r) => r.days)

/** 小台模擬那一天的秒級復盤資料；沒有（只有 1 分 K）→ 後端回 404，呼叫端改用 1 分 K。只給 admin。 */
export const fetchFuturesSeconds = (day: string) => get<SecondsPayload>(`/api/futures/seconds?day=${encodeURIComponent(day)}`)

/** 大台／微台那一天的行情（不給日期＝最新一天）。只給 admin（T-451）。 */
export const fetchQuoteDay = (product: QuoteProduct, day?: string) =>
  get<QuotePayload>(`/api/futures/quotes?product=${product}${day ? `&day=${encodeURIComponent(day)}` : ''}`)

/** 大台／微台有行情的交易日（新到舊）。 */
export const fetchQuoteDays = (product: QuoteProduct) =>
  get<{ days: QuoteDayRow[] }>(`/api/futures/quotes/days?product=${product}`).then((r) => r.days)

/** 盤口與大單（T-455）：那一天的主動買賣、大單（≥ min 口）、五檔；live＝盤中（不帶每分鐘的五檔）。沒有 → 404。只給 admin。 */
export const fetchFlow = (product: Product, day: string | null, min: number, live: boolean) =>
  get<Flow>(`/api/futures/flow?product=${product}&min=${min}${live ? '&live=1' : ''}${day ? `&day=${encodeURIComponent(day)}` : ''}`)

/** 大台／微台那一天的秒級復盤資料；沒有 → 404，呼叫端改用 1 分 K。 */
export const fetchQuoteSeconds = (product: QuoteProduct, day: string) =>
  get<SecondsPayload>(`/api/futures/quotes/seconds?product=${product}&day=${encodeURIComponent(day)}`)

/** 首頁戰情室（T-379）：晨報、大盤體檢、期貨籌碼、名單速覽、昨日消息、行事曆。 */
export const fetchWarroom = () => get<WarRoomData>('/api/warroom')
/** 新聞分頁（T-400）：新聞流；until 含這個時間（翻頁用）。 */
export function fetchNewsItems(q: { kind: NewsKind | null; type?: string | null; limit: number; until?: string }) {
  const p = new URLSearchParams({ limit: String(q.limit) })
  if (q.kind) p.set('kind', q.kind)
  if (q.type) p.set('type', q.type)
  if (q.until) p.set('until', q.until)
  return get<{ items: NewsRow[] }>(`/pub/news/items?${p}`)
}
/** 一則新聞的關係圖（T-402／T-404）：提到的實體往外三度；沒有這則 → 404。 */
export const fetchNewsGraph = (id: string) => get<NewsGraphData>(`/pub/news/graph?id=${encodeURIComponent(id)}`)
/** 反向看圖：某一檔近 7 天連得到的新聞（個股頁「相關新聞」）。 */
export const fetchStockNews = (sid: string) => get<StockNews>(`/pub/news/stock?id=${encodeURIComponent(`tw:${sid}`)}`)
/** 名單小標：一批股票近 3 天各有幾則相關新聞；鍵是 tw:代號。 */
export const fetchStockNewsCounts = (sids: string[]) =>
  get<StockNewsCounts>(`/pub/news/stock-counts?ids=${encodeURIComponent(sids.map((s) => `tw:${s}`).join(','))}`)
/** 新聞服務最後抓取時間與各來源狀態。 */
export const fetchNewsHealth = () => get<NewsHealth>('/pub/news/health')
/** 這件事的來龍去脈（T-420） */
export const fetchNewsThread = (id: string) => get<NewsThread>(`/pub/news/thread?id=${encodeURIComponent(id)}`)
/** 一則新聞的相似新聞（T-410） */
export const fetchSimilarNews = (id: string) => get<SimilarNews>(`/pub/news/similar?id=${encodeURIComponent(id)}`)
/** 點開才翻的摘要（D-207） */
/** 證據句（T-437）：這些新聞裡這些實體是在哪段字被比對到。公開（新聞頁不用登入） */
export const fetchNewsEvidence = (ids: string[], entities: string[]) => get<NewsEvidence>(`/pub/news/evidence?ids=${encodeURIComponent(ids.join(','))}&entities=${encodeURIComponent(entities.join(','))}`)
export const fetchNewsSummary = (id: string) => get<NewsSummary>(`/pub/news/summary?id=${encodeURIComponent(id)}`)
/** 新聞全景星雲圖（T-418）：排程算好的最新一筆 */
export const fetchNewsGalaxy = () => get<GalaxyData>('/pub/news/galaxy')

/** 星雲圖「大家都在搜」（T-446）：近 days 天被最多分頁搜過的亮點 id 與幾個分頁。 */
export const fetchGalaxyTrend = () => get<{ days: number; items: { hub: string; n: number }[] }>('/pub/galaxy-search')

/** 記一次搜尋（選了哪幾個亮點；不記打的字）。 */
export const recordGalaxySearch = (id: string, hubs: string[]) => get<{ ok: boolean }>('/pub/galaxy-search', 'POST', { id, hubs })

/** 星雲圖心跳（T-445）：回同時幾個人在看（含自己）。 */
export const sendGalaxyPresence = (id: string) => get<{ n: number }>('/pub/galaxy-presence', 'POST', { id })

/**
 * 關掉／切走星雲圖：sendBeacon 是瀏覽器專門給「頁面正在關」用的（keepalive fetch 在換頁時實測會掉），不支援才退回 keepalive fetch。
 * 送不到也沒關係（90 秒後自動不算），所以不回結果、不丟錯。
 */
export function leaveGalaxyPresence(id: string): void {
  const url = apiUrl('/pub/galaxy-presence'), body = JSON.stringify({ id, leave: true })
  if (navigator.sendBeacon?.(url, new Blob([body], { type: 'application/json' }))) return
  fetch(url, { method: 'POST', keepalive: true, headers: { 'content-type': 'application/json' }, body }).catch(() => undefined)
}
/** 個股頁 K 線上的新聞標記（T-419） */
export const fetchStockNewsDays = (sid: string) => get<StockNewsDays>(`/pub/news/stock-days?id=${encodeURIComponent(`tw:${sid}`)}`)
/** 新聞三張卡（T-417） */
export const fetchNewsDigest = () => get<NewsDigest>('/pub/news/digest')
/** 核可頁（T-403）：擁有者才有 can_review 與草稿清單 */
export const fetchNewsReview = () => get<NewsReview>('/api/news/review')
export const postNewsReview = (decisions: ReviewDecision[]) => get<ReviewResult>('/api/news/review', 'POST', { decisions })
/** 新聞語意搜尋（T-410） */
export const fetchNewsSearch = (q: string) => get<NewsSearch>(`/api/news/search?q=${encodeURIComponent(q)}`)
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

/** 個股當沖（T-458／D-221）：那一天的整份狀態（不給日期＝最新一天）。只給 admin。 */
export const fetchDayTrade = (day?: string): Promise<DayTradeDay> =>
  get<DayTradeDay | BoardOnlyDay>(day ? `/api/daytrade?day=${encodeURIComponent(day)}` : '/api/daytrade').then(normalizeDay)

/** 個股當沖有紀錄的交易日（新到舊）。 */
export const fetchDayTradeDays = () => get<{ days: DayTradeRow[] }>('/api/daytrade/days').then((r) => r.days)

/** 個股當沖那一天的復盤資料（收盤後才有；沒有 → 404）。 */
export const fetchDayTradeHistory = (day: string) => get<DayTradeHistory>(`/api/daytrade/history?day=${encodeURIComponent(day)}`)
