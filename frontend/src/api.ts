// 監控 API 型別與 fetch 客戶端。前端即時讀後端（twquant serve）；API 不可達時
// App 會退回內嵌的 data.json 快照。同源部署（nginx/vite 反代 /api）故預設無需 base。

export type Candle = { d: string; o: number; h: number; l: number; c: number; amt?: number; v?: number }
// 官方指數：加權 TAIEX 有 OHLC；櫃買 TPEX 僅收盤 c。
export type IndexPoint = { d: string; o?: number; h?: number; l?: number; c: number }
export type Indices = { taiex: IndexPoint[]; tpex: IndexPoint[] }
// ic/t/ir/ls 可能為 null：後端 _sanitize 把非有限值（NaN/Inf）清成 null。
export type Horizon = { h: number; ic: number | null; t: number | null; ir: number | null; ls: number | null; n: number }
export type Fac = { name: string; direction: number; horizons: Horizon[] }
export type Coverage = { label: string; days: number; span: string }
export type Metrics = {
  sharpe: number; cagr: number; mdd: number; sortino: number; calmar: number
  win_rate: number; turnover: number; total_ret: number; n_trades: number
}
export type Backtest = { name: string; window?: string; equity: { d: string; e: number }[]; metrics: Metrics }
export type Inst = { sid: string; trust: number; frgn: number; px: number }
export type Rev = { sid: string; rev: number; yoy: number }
export type Capacity = { twd: number; binding: string; n_positions: number }
export type Meta = { as_of: string | null; window: string; rev_month: string | null; top_stocks: string[]; coverage: Coverage[] }
export type Data = {
  meta: Meta
  market: Candle[]
  stocks: Record<string, Candle[]>
  factors: Fac[]
  backtest: Backtest
  institutional: Inst[]
  revenue: Rev[]
  capacity: Capacity
}
export type IngestSource = { label: string; span: string; days: number; gaps: number }
export type IngestFailure = { attempt_ts: string; source: string; table: string; date: string; detail: string }
export type IngestStatus = {
  sources: IngestSource[]; last_run: string | null; records: number; failed: number
  recent_failures: IngestFailure[]
}

// 擷取後台：涵蓋矩陣 / 漏抓清單 / heartbeat / 一鍵補抓工作（D-018）。
export type CellState = 'present' | 'missing' | 'failed' | 'none'
export type IngestDataset = {
  key: string; table: string; source: string; label: string; span: string; days: number; window_missing: number
}
export type IngestMatrix = {
  datasets: IngestDataset[]; dates: string[]; cells: Record<string, Record<string, CellState>>
  last_run: string | null; latest: string | null
}
export type IngestGap = {
  label: string; table: string; source: string; kind: 'missing' | 'failed'
  from: string; to: string; days: number; command: string; warn_ban: boolean; detail: string
}
export type IngestGaps = { gaps: IngestGap[]; runs: number; total_missing: number }
export type Heartbeat = {
  heartbeat: { day: string; git_hash: string; ts: string } | null
  latest: string | null; watchdog_ok: boolean; watchdog_reason: string
}
export type IngestJob = {
  id: string; label: string; command: string; status: 'running' | 'success' | 'failed'
  started: string; ended: string | null; exit_code: number | null; tail: string[]
}
export type IngestJobs = { jobs: IngestJob[]; busy: boolean }
// 可手動抓取的資料軌（白名單）：unit=date 收 YYYY-MM-DD、month 收 YYYY-MM。
export type IngestTrack = { table: string; source: string; label: string; unit: 'date' | 'month' }
export type IngestReq = { table: string; source: string; from: string; to: string; force?: boolean }
// 官方開休市日曆（market_holidays）：開市日數 + 休市清單 + 官方 vs 觀測核對。
export type HolidayRow = { date: string; weekday: string | null; name: string }
export type HolidayCalendar = {
  available: boolean
  years?: number[]
  sessions?: number
  first?: string | null
  last?: string | null
  closed?: HolidayRow[]
  cross_check?: { missing: string[]; unexpected: string[]; range: [string, string] | null }
}

// 個股中繼資料（中文名 / 產業別族群 / 市場別）+ 個股因子體檢卡。
export type StockMetaEntry = { name: string | null; industry: string | null; market: string | null }
export type StockMetaMap = Record<string, StockMetaEntry>
export type ScorecardFactor = {
  factor: string; zh: string; category: string; value: number | null
  z: number | null; pct: number | null; direction: number; signed_z: number | null
}
export type Scorecard = {
  sid: string; name: string | null; industry: string | null; market: string | null
  as_of: string | null; factors: ScorecardFactor[]
  composite: { signed_z_mean: number; pct: number; n_universe: number } | null
}

// 資金移動板塊：各產業別（族群）法人資金淨流入 + 報酬 + 成交值。
export type SectorFlow = {
  industry: string; n: number; net_yi: number; frgn_yi: number; trust_yi: number; amt_yi: number; ret_pct: number
}
export type SectorFlows = {
  as_of: string | null; window: number; sectors: SectorFlow[]
  dates: string[]; daily: Record<string, Record<string, number>>
}

const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '')
export const apiUrl = (path: string) => `${API_BASE}${path}`

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(apiUrl(path), { headers: { accept: 'application/json' } })
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`)
  return (await res.json()) as T
}

// POST（一鍵補抓）：失敗時盡量帶回後端 detail（409 進行中 / 400 白名單、D-015 護欄）。
async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = `${path} → HTTP ${res.status}`
    try {
      const j = (await res.json()) as { detail?: unknown }
      if (j.detail) detail = String(j.detail)
    } catch {
      /* 非 JSON 錯誤體：保留 HTTP 狀態訊息 */
    }
    throw new Error(detail)
  }
  return (await res.json()) as T
}

// 存活探針（毫秒級）：開頁以此判定 live，不等重端點。
export const fetchHealth = () => getJSON<{ status: string }>('/api/health')
export const fetchSnapshot = () => getJSON<Data>('/api/snapshot')
// 逐段（逐頁）端點：各頁切到才 fetch，重運算（因子/回測/容量）只在該頁被開啟時才算。
export const fetchMeta = () => getJSON<Meta>('/api/meta')
export const fetchMarket = () => getJSON<{ market: Candle[] }>('/api/market').then((r) => r.market)
export const fetchIndices = () => getJSON<{ indices: Indices }>('/api/indices').then((r) => r.indices)
export const fetchFactors = () => getJSON<{ factors: Fac[] }>('/api/factors').then((r) => r.factors)
export const fetchInstitutional = () =>
  getJSON<{ institutional: Inst[] }>('/api/institutional').then((r) => r.institutional)
export const fetchRevenue = () => getJSON<{ rev_month: string | null; revenue: Rev[] }>('/api/revenue')
export const fetchCapacity = () => getJSON<{ capacity: Capacity }>('/api/capacity').then((r) => r.capacity)
export const fetchStockList = () => getJSON<{ stocks: string[] }>('/api/stocks').then((r) => r.stocks)
// K 線改讀 PG 供應層 /ohlc（去重、與頁面其餘面板同源）；PG 不可達時端點自動降級資料湖。
export const fetchStockOhlc = (sid: string) =>
  getJSON<StockSeries<{ date: string; open: number; high: number; low: number; close: number; volume: number }>>(
    `/api/stocks/${encodeURIComponent(sid)}/ohlc?days=5000`,
  ).then((r) => r.rows.map((x) => ({ d: x.date, o: x.open, h: x.high, l: x.low, c: x.close, v: x.volume })))
export const fetchStockMeta = () => getJSON<{ meta: StockMetaMap }>('/api/stock-meta').then((r) => r.meta)
export const fetchStockFactors = (sid: string) =>
  getJSON<Scorecard>(`/api/stocks/${encodeURIComponent(sid)}/factors`)

// 個股 API v2（envelope: {sid, as_of, source, rows}）；只讀 PG，source 恆為 'pg'。
export type Src = 'pg'
export type StockOverview = {
  sid: string; name: string | null; industry: string | null; market: string | null
  as_of: string | null; source: Src; close: number | null; chg: number | null
  chg_pct: number | null; volume: number | null; pos_52w: number | null
  composite: { signed_z_mean: number; pct: number; n_universe: number } | null
}
export type InstRow = {
  date: string; foreign_net: number; trust_net: number; dealer_net: number
  cum_foreign: number; cum_trust: number
}
export type MarginRow = {
  date: string; margin_balance: number; margin_chg: number | null
  short_balance: number; short_chg: number | null; short_margin_ratio: number | null
}
export type RevenueRow = { month: string; revenue: number | null; yoy: number | null; mom: number | null }
export type StockSeries<T> = { sid: string; as_of: string | null; source: Src; rows: T[] }

const enc = encodeURIComponent
export const fetchStockOverview = (sid: string) =>
  getJSON<StockOverview>(`/api/stocks/${enc(sid)}/overview`)
export const fetchStockInstitutional = (sid: string, days = 120) =>
  getJSON<StockSeries<InstRow>>(`/api/stocks/${enc(sid)}/institutional?days=${days}`)
export const fetchStockMargin = (sid: string, days = 120) =>
  getJSON<StockSeries<MarginRow>>(`/api/stocks/${enc(sid)}/margin?days=${days}`)
export const fetchStockRevenue = (sid: string, months = 36) =>
  getJSON<StockSeries<RevenueRow>>(`/api/stocks/${enc(sid)}/revenue?months=${months}`)

export type BrokerRow = { broker: string; buy_shares: number; sell_shares: number; net_shares: number }
export type BrokerSummary = { date: string; buy_brokers: number; sell_brokers: number; broker_count_diff: number }
export type StockBrokers = {
  sid: string; as_of: string | null; source: Src; window_days: number
  top_buy: BrokerRow[]; top_sell: BrokerRow[]; summary: BrokerSummary[]
}
export const fetchStockBrokers = (sid: string, window = 5) =>
  getJSON<StockBrokers>(`/api/stocks/${enc(sid)}/brokers?window=${window}`)

// 主力（富邦DJ top-15±）每日淨買超（股）序列；K 線副圖日條＋累計線（累計由前端算）。
export type BrokerFlowPoint = { date: string; net: number }
export const fetchStockBrokerFlow = (sid: string, days = 250) =>
  getJSON<StockSeries<BrokerFlowPoint>>(`/api/stocks/${enc(sid)}/broker-flow?days=${days}`)

// 宇宙綜合因子排名前 N（最新物化日、pct 遞減）；個股因子頁「熱門」列。
export type CompositeTopRow = { stock_id: string; name: string | null; signed_z_mean: number; pct: number }
export type CompositeTop = { as_of: string | null; source: Src; rows: CompositeTopRow[] }
export const fetchCompositeTop = (n = 10) => getJSON<CompositeTop>(`/api/composite-top?n=${n}`)

// 單因子選股：該因子最新物化日、方向化 z（z×direction）遞減前 N；個股因子頁「因子選股」。
export type FactorTopRow = {
  stock_id: string; name: string | null; value: number; z: number; pct: number; signed_z: number
}
export type FactorOption = { factor: string; zh: string; direction: number }
export type FactorOrder = 'top' | 'bottom'
export type FactorTop = {
  factor: string; zh: string; direction: number; order: FactorOrder
  as_of: string | null; source: Src; rows: FactorTopRow[]; available: FactorOption[]
}
export const fetchFactorTop = (factor: string, n = 10, order: FactorOrder = 'top') =>
  getJSON<FactorTop>(`/api/factor-top?factor=${enc(factor)}&n=${n}&order=${order}`)

// 策略選股池（SPEC-007 §6）：研究高勝率條件的每日成員 + 連續在池天數；描述性、非交易訊號。
export type ScreenerMember = {
  stock_id: string; name: string | null; close: number | null; chg: number | null
  yoy: number | null; conc: number | null; accum_ratio: number | null; streak: number
}
export type ScreenerPool = {
  key: string; name: string; rule: string; note: string; total: number; members: ScreenerMember[]
}
export type Screener = {
  as_of: string | null; source: Src; snapshot: boolean; universe_n: number; pools: ScreenerPool[]
}
// date 給定＝回溯：後端先讀 screener_daily 快照（snapshot=true）、無快照即時回算。
export const fetchScreener = (n = 30, date?: string) =>
  getJSON<Screener>(`/api/screener?n=${n}${date ? `&date=${enc(date)}` : ''}`)

// P7/P8：因子歷史（每因子 z/pct 序列）與綜合分位趨勢（皆需 PG 物化歷史）。
export type FactorHistPoint = { date: string; value: number | null; z: number | null; pct: number | null }
export type FactorHistGroup = { factor: string; series: FactorHistPoint[] }
export type StockFactorHistory = { sid: string; as_of: string | null; source: Src; rows: FactorHistGroup[] }
export const fetchStockFactorHistory = (sid: string, factors: string[], days = 250) =>
  getJSON<StockFactorHistory>(
    `/api/stocks/${enc(sid)}/factors/history?factors=${encodeURIComponent(factors.join(','))}&days=${days}`,
  )
export type CompositePoint = { date: string; signed_z_mean: number; pct: number }
export const fetchStockCompositeHistory = (sid: string, days = 250) =>
  getJSON<StockSeries<CompositePoint>>(`/api/stocks/${enc(sid)}/composite-history?days=${days}`)
export const fetchIngestStatus = () => getJSON<IngestStatus>('/api/ingest-status')
export const fetchIngestMatrix = () => getJSON<IngestMatrix>('/api/ingest-matrix')
export const fetchIngestGaps = () => getJSON<IngestGaps>('/api/ingest-gaps')
export const fetchHeartbeat = () => getJSON<Heartbeat>('/api/heartbeat')
export const fetchIngestJobs = () => getJSON<IngestJobs>('/api/ingest-jobs')
export const fetchIngestTracks = () => getJSON<{ tracks: IngestTrack[] }>('/api/ingest-tracks').then((r) => r.tracks)
// 補抓／手動抓取共用同一端點（POST /api/ingest-jobs）：指令由後端白名單推導。
const postIngestJob = (req: IngestReq) =>
  postJSON<IngestJob>('/api/ingest-jobs', {
    table: req.table, source: req.source, from: req.from, to: req.to, force: req.force ?? false,
  })
export const startIngestJob = (g: IngestGap, force = false) =>
  postIngestJob({ table: g.table, source: g.source, from: g.from, to: g.to, force })
export const startIngestFetch = (req: IngestReq) => postIngestJob(req)
// 手動補跑每日 pipeline（排程錯過時的介面補跑）：省略 date → 後端以今日台北補跑；
// 與補抓共用同一工作佇列（回傳的工作出現在 /api/ingest-jobs 清單）。
export const startPipelineJob = (date?: string) =>
  postJSON<IngestJob>('/api/pipeline-jobs', { date: date ?? null })
export const fetchHolidayCalendar = () => getJSON<HolidayCalendar>('/api/holiday-calendar')
// 官方開休市日曆一鍵抓取（端點無年度參數；from/to 忽略，後端整年一抓）。
export const startHolidayFetch = () =>
  postIngestJob({ table: 'market_holidays', source: 'twse', from: '-', to: '-' })
export const fetchSectorFlows = (window = 20) =>
  getJSON<{ flows: SectorFlows }>(`/api/sector-flows?window=${window}`).then((r) => r.flows)
export type StrategyInfo = { id: string; name: string; description: string }
export const fetchStrategies = () => getJSON<StrategyInfo[]>('/api/strategies')
export const fetchBacktest = (start: string, end: string, strategy?: string) =>
  getJSON<Backtest>(
    `/api/backtest?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}` +
      (strategy ? `&strategy=${encodeURIComponent(strategy)}` : ''),
  )
