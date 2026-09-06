import { useEffect, useState, type JSX } from 'react'
import { routeHref, type PlatformRoute } from './routes'
import { apiUrl } from '../api'
import { Panel } from '../ui'

type Candidate = { stock_id: string; rank: number; score: number; bucket: string; reason_ref: string | null; insight_run_id: string | null; data_status: string }
type Run = { run_id: string; model_id: string; as_of: string; generated_at: string; published_at: string | null; mode: 'research' | 'reconstructed'; artifact_fingerprint: string; candidates: Candidate[] }
type Response = { runs: Run[]; issues: { run_id: string; detail: string }[]; query: { date: string | null; model: string | null; run: string | null } }

async function fetchRuns(route: PlatformRoute): Promise<Response> {
  const params = new URLSearchParams()
  for (const key of ['date', 'model', 'run']) { const value = route.query.get(key); if (value) params.set(key, value) }
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
export function RecommendationsPage({ route }: { route: PlatformRoute }): JSX.Element {
  const [data, setData] = useState<Response | null>(null); const [error, setError] = useState<string | null>(null)
  const identity = `${route.page}:${route.query.get('date') ?? ''}:${route.query.get('model') ?? ''}:${route.query.get('run') ?? ''}`
  useEffect(() => { let alive = true; setData(null); setError(null); fetchRuns(route).then(value => alive && setData(value), err => alive && setError(err instanceof Error ? err.message : String(err))); return () => { alive = false } }, [identity])
  const title = route.page === 'overview' ? '今日研究候選' : '每日 ML 選股'
  if (error) return <Panel title={title}><div className="platform-query-state platform-query-error">推薦快照無法讀取：{error}</div></Panel>
  if (!data) return <Panel title={title}><div className="platform-query-state">正在讀取已保存的推薦快照…</div></Panel>
  if (data.runs.length === 0) return <Panel title={title}><div className="platform-query-state">{route.query.get('date') ? `${route.query.get('date')} 尚未產生推薦快照。` : '尚未保存推薦快照。'}</div></Panel>
  return <div className="platform-recommendations"><Panel title={title} sub={`${data.runs.length} 個保存 run`}><div className="platform-filter-row"><label>日期<input type="date" value={route.query.get('date') ?? ''} onChange={e => { window.location.hash = href(route, { date: e.target.value || null }) }} /></label><a href={href(route, {}, 'recommendations')}>展開完整清單 →</a></div>{data.runs.map(run => <RunCard key={run.run_id} run={run} route={route} compact={route.page === 'overview'} />)}</Panel>{data.issues.length > 0 && <div className="platform-artifact-issues">有 {data.issues.length} 份推薦快照無法讀取。</div>}</div>
}

function RunCard({ run, route, compact }: { run: Run; route: PlatformRoute; compact: boolean }) {
  const candidates = compact ? run.candidates.slice(0, 10) : run.candidates
  const context = new URLSearchParams(route.query); context.set('date', run.as_of.slice(0, 10)); context.set('model', run.model_id); context.set('run', run.run_id)
  return <section className="platform-run-card"><header><div><span className="platform-eyebrow">{run.mode === 'research' ? 'RESEARCH ONLY' : 'RECONSTRUCTED'}</span><h2>{run.model_id}</h2><p>{run.as_of.slice(0, 10)} · run {run.run_id}</p></div><a href={routeHref('models', undefined, context)}>查看來源模型 →</a></header>{run.candidates.length === 0 ? <div className="platform-query-state">此 run 已保存，合法地沒有候選股票。</div> : <div className="platform-table-wrap"><table className="platform-data-table"><thead><tr><th>排名</th><th>股票</th><th>原始 ML 分數</th><th>分組</th><th>洞悉</th></tr></thead><tbody>{candidates.map(candidate => <tr key={candidate.stock_id}><td>{candidate.rank}</td><td><a href={routeHref('stocks', candidate.stock_id, context)}>{candidate.stock_id}</a></td><td>{candidate.score.toFixed(4)}</td><td>{candidate.bucket}</td><td>{candidate.data_status === 'pending' ? '待分析' : candidate.insight_run_id ?? candidate.data_status}</td></tr>)}</tbody></table></div>}{compact && run.candidates.length > candidates.length && <a className="platform-more-link" href={routeHref('recommendations', undefined, context)}>查看其餘 {run.candidates.length - candidates.length} 檔 →</a>}</section>
}
