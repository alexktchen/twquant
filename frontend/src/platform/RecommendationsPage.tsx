import { useEffect, useState, type JSX } from 'react'
import { routeHref, type PlatformRoute } from './routes'
import { apiUrl } from '../api'
import { Panel } from '../ui'

type Insight = {
  insight_run_id: string
  stance: 'support' | 'neutral' | 'conflict' | 'insufficient_data' | null
  recommended_bucket: 'select' | 'watch' | 'exclude' | null
  absent_reason: string | null
  support_n: number
  contradict_n: number
  gap_n: number
}
type Candidate = { stock_id: string; rank: number; score: number; bucket: string; reason_ref: string | null; insight_run_id: string | null; data_status: string; insight: Insight | null }

const STANCE_LABEL: Record<NonNullable<Insight['stance']>, string> = {
  support: '支持', neutral: '中性', conflict: '矛盾', insufficient_data: '資料不足',
}
const BUCKET_LABEL: Record<NonNullable<Insight['recommended_bucket']>, string> = {
  select: '精選', watch: '觀察', exclude: '排除',
}

/** The model's reading of the evidence. Its bucket is a suggestion, never the final class. */
function InsightCell({ insight }: { insight: Insight | null }) {
  if (insight === null) return <span className="platform-muted">尚未分析</span>
  if (insight.stance === null) {
    return <span className="platform-muted" title={insight.absent_reason ?? ''}>無分析（{insight.absent_reason ?? '未記錄原因'}）</span>
  }
  return <span>
    {STANCE_LABEL[insight.stance]}
    <span className="platform-evidence-counts"> 支持 {insight.support_n}／反對 {insight.contradict_n}／缺口 {insight.gap_n}</span>
    {insight.recommended_bucket && <span className="platform-suggestion">LLM 提議：{BUCKET_LABEL[insight.recommended_bucket]}</span>}
  </span>
}
type Run = { run_id: string; model_id: string; as_of: string; generated_at: string; published_at: string | null; mode: 'research' | 'reconstructed'; artifact_fingerprint: string; candidates: Candidate[]; candidate_total: number }
type Query = { date: string | null; model: string | null; run: string | null; bucket: string | null; q: string | null; page: number; page_size: number }
type Response = { runs: Run[]; issues: { run_id: string; detail: string }[]; query: Query }

/** Filter keys the daily list keeps in the URL so a shared link restores the same view. */
export const recommendationFilters = ['date', 'model', 'bucket', 'q', 'run'] as const

async function fetchRuns(route: PlatformRoute): Promise<Response> {
  const params = new URLSearchParams()
  for (const key of recommendationFilters) { const value = route.query.get(key); if (value) params.set(key, value) }
  const response = await fetch(`${apiUrl('/api/platform/recommendations')}?${params}`, { headers: { accept: 'application/json' } })
  if (!response.ok) throw new Error(`/api/platform/recommendations → HTTP ${response.status}`)
  return response.json() as Promise<Response>
}

function href(route: PlatformRoute, patch: Record<string, string | null>, page = route.page): string {
  const query = new URLSearchParams(route.query)
  for (const [key, value] of Object.entries(patch)) value ? query.set(key, value) : query.delete(key)
  return routeHref(page as 'overview' | 'recommendations', undefined, query)
}

/** Present saved ML recommendation runs without substituting factor rankings or demo data. */
export function RecommendationsPage({ route, dataAsOf }: { route: PlatformRoute; dataAsOf: string | null }): JSX.Element {
  const [data, setData] = useState<Response | null>(null); const [error, setError] = useState<string | null>(null)
  const identity = recommendationFilters.map(key => route.query.get(key) ?? '').join(':')
  useEffect(() => {
    let alive = true; setData(null); setError(null)
    fetchRuns(route).then(value => alive && setData(value), err => alive && setError(err instanceof Error ? err.message : String(err)))
    return () => { alive = false }
  }, [identity]) // Every request input is encoded in identity; route is read synchronously per run.
  const compact = route.page === 'overview'
  const title = compact ? '今日研究候選' : '每日 ML 選股'
  const filtered = Boolean(route.query.get('bucket') || route.query.get('q'))
  const controls = <Filters route={route} data={data} />
  if (error) return <Panel title={title}><div className="platform-query-state platform-query-error">推薦快照無法讀取：{error}<br />這是讀取錯誤，不是「今天沒有推薦」；頁面不會改用綜合因子排名或示範資料補位。</div></Panel>
  if (!data) return <Panel title={title}><div className="platform-query-state">正在讀取已保存的推薦快照…</div></Panel>
  if (data.runs.length === 0) return <Panel title={title}>{controls}<div className="platform-query-state">
    {route.query.get('date') ? `${route.query.get('date')} 尚未產生推薦快照。` : '尚未保存任何推薦快照。'}
    <br />尚未產生與讀取失敗是不同狀態；沒有快照時不會退回最近一期，也不會顯示示範候選。
  </div></Panel>
  return <div className="platform-recommendations">
    <Panel title={title} sub={`${data.runs.length} 個保存 run`}>{controls}
      {data.runs.map(run => <RunCard key={run.run_id} run={run} route={route} compact={compact} filtered={filtered} dataAsOf={dataAsOf} />)}
    </Panel>
    {data.issues.length > 0 && <div className="platform-artifact-issues">有 {data.issues.length} 份推薦快照無法讀取，已列為問題項而非丟棄。</div>}
  </div>
}

function Filters({ route, data }: { route: PlatformRoute; data: Response | null }) {
  const buckets = [...new Set((data?.runs ?? []).flatMap(run => run.candidates.map(c => c.bucket)))].sort()
  const active = route.query.get('bucket')
  return <div className="platform-filter-row">
    <div className="platform-filter-group">
      <label>日期<input type="date" aria-label="推薦日期" value={route.query.get('date') ?? ''} onChange={e => { window.location.hash = href(route, { date: e.target.value || null }) }} /></label>
      <label>搜尋<input type="search" aria-label="搜尋股票代號" placeholder="股票代號" value={route.query.get('q') ?? ''} onChange={e => { window.location.hash = href(route, { q: e.target.value || null }) }} /></label>
      {buckets.length > 0 && <span className="platform-bucket-filter">分類
        <a href={href(route, { bucket: null })} aria-current={active ? undefined : 'true'}>全部</a>
        {buckets.map(bucket => <a key={bucket} href={href(route, { bucket })} aria-current={active === bucket ? 'true' : undefined}>{bucket}</a>)}
      </span>}
    </div>
    {route.page === 'overview' && <a href={href(route, {}, 'recommendations')}>展開完整清單 →</a>}
  </div>
}

function RunCard({ run, route, compact, filtered, dataAsOf }: { run: Run; route: PlatformRoute; compact: boolean; filtered: boolean; dataAsOf: string | null }) {
  const day = run.as_of.slice(0, 10)
  const stale = dataAsOf != null && day < dataAsOf
  const candidates = compact ? run.candidates.slice(0, 10) : run.candidates
  const context = new URLSearchParams(route.query); context.set('date', day); context.set('model', run.model_id); context.set('run', run.run_id)
  return <section className="platform-run-card">
    <header><div>
      <span className="platform-eyebrow">{run.mode === 'research' ? 'RESEARCH ONLY' : 'RECONSTRUCTED'}{run.published_at ? '' : ' · 未發布'}</span>
      <h2>{run.model_id}</h2><p>{day} · run {run.run_id}</p>
    </div><a href={routeHref('models', undefined, context)}>查看來源模型 →</a></header>
    {stale && <div className="platform-stale-note">快照過期：這個 run 的資料截點是 {day}，行情資料已到 {dataAsOf}。這是舊快照，不是今日推薦。</div>}
    {run.candidate_total === 0 ? <div className="platform-query-state">
      {filtered ? '目前的分類／搜尋條件下沒有符合的候選；清除條件可看回完整名單。' : '此 run 已保存，合法地沒有候選股票（零筆，不是讀取失敗）。'}
    </div> : <div className="platform-table-wrap"><table className="platform-data-table">
      <thead><tr><th>排名</th><th>股票</th><th>原始 ML 分數</th><th>分組</th><th>洞悉</th></tr></thead>
      <tbody>{candidates.map(candidate => <tr key={candidate.stock_id}>
        <td>{candidate.rank}</td>
        <td><a href={routeHref('stocks', candidate.stock_id, context)}>{candidate.stock_id}</a></td>
        <td>{candidate.score.toFixed(4)}</td><td>{candidate.bucket}</td>
        <td><InsightCell insight={candidate.insight} /></td>
      </tr>)}</tbody></table></div>}
    {compact && run.candidate_total > candidates.length && <a className="platform-more-link" href={routeHref('recommendations', undefined, context)}>查看其餘 {run.candidate_total - candidates.length} 檔 →</a>}
  </section>
}
