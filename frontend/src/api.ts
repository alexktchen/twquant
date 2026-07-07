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

// 擷取後台（唯讀）：涵蓋矩陣 / 漏抓清單 / heartbeat。
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
export const fetchStockOhlc = (sid: string) =>
  getJSON<{ sid: string; ohlc: Candle[] }>(`/api/stocks/${encodeURIComponent(sid)}`).then((r) => r.ohlc)
export const fetchStockMeta = () => getJSON<{ meta: StockMetaMap }>('/api/stock-meta').then((r) => r.meta)
export const fetchStockFactors = (sid: string) =>
  getJSON<Scorecard>(`/api/stocks/${encodeURIComponent(sid)}/factors`)
export const fetchIngestStatus = () => getJSON<IngestStatus>('/api/ingest-status')
export const fetchIngestMatrix = () => getJSON<IngestMatrix>('/api/ingest-matrix')
export const fetchIngestGaps = () => getJSON<IngestGaps>('/api/ingest-gaps')
export const fetchHeartbeat = () => getJSON<Heartbeat>('/api/heartbeat')
export const fetchSectorFlows = (window = 20) =>
  getJSON<{ flows: SectorFlows }>(`/api/sector-flows?window=${window}`).then((r) => r.flows)
export const fetchBacktest = (start: string, end: string) =>
  getJSON<Backtest>(`/api/backtest?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`)
