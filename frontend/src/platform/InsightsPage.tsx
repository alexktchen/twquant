import { useEffect, useState, type JSX } from 'react'
import { apiUrl } from '../api'
import { Panel } from '../ui'
import { routeHref, type PlatformRoute } from './routes'

export type InsightEvidence = {
  evidence_id: string
  title: string
  url: string
  published_at: string
  available_at: string
  captured_at: string
  stock_id: string | null
  industry: string | null
  direction: 'support' | 'contradict' | 'neutral' | 'gap'
  availability?: 'actual' | 'estimated'
  reason_ref: string | null
}

export type InsightPack = {
  insight_run_id: string
  as_of: string
  stock_id: string | null
  industry: string | null
  evidence: InsightEvidence[]
  analysis: {
    prompt_id: string
    prompt_hash: string
    analysis_schema_version: number
    model_version: string
    as_of: string
    summary: string
    cited_evidence_ids: string[]
    stance?: 'support' | 'neutral' | 'conflict' | 'insufficient_data' | null
    recommended_bucket?: 'select' | 'watch' | 'exclude' | null
  } | null
  analysis_absent_reason: string | null
}

type Response = { packs: InsightPack[]; issues: { insight_run_id: string; detail: string }[] }

const DIRECTION_LABEL: Record<InsightEvidence['direction'], string> = {
  support: '支持',
  contradict: '反對',
  neutral: '中性',
  gap: '缺口',
}

async function fetchPacks(stock: string | null): Promise<Response> {
  const params = new URLSearchParams()
  if (stock) params.set('stock', stock)
  const response = await fetch(`${apiUrl('/api/platform/insights')}?${params}`, { headers: { accept: 'application/json' } })
  if (!response.ok) throw new Error(`洞悉證據包讀取失敗（HTTP ${response.status}）`)
  return response.json() as Promise<Response>
}

function EvidenceTable({ pack }: { pack: InsightPack }) {
  const cited = new Set(pack.analysis?.cited_evidence_ids ?? [])
  return <div className="platform-table-wrap"><table className="platform-data-table">
    <thead><tr><th>方向</th><th>來源</th><th>可取得時間</th><th>被引用</th></tr></thead>
    <tbody>{pack.evidence.map(item => <tr key={item.evidence_id}>
      <td className={`platform-direction platform-direction-${item.direction}`}>{DIRECTION_LABEL[item.direction]}</td>
      {/* The source text is quoted data; the page renders it, never acts on it. */}
      <td><a href={item.url} target="_blank" rel="noopener noreferrer nofollow">{item.title}</a></td>
      <td>{item.available_at.slice(0, 16).replace('T', ' ')}</td>
      <td>{cited.has(item.evidence_id) ? '是' : '否'}</td>
    </tr>)}</tbody>
  </table></div>
}

function PackCard({ pack, route }: { pack: InsightPack; route: PlatformRoute }) {
  const gaps = pack.evidence.filter(item => item.direction === 'gap')
  return <section className="platform-run-card">
    <header><div>
      <span className="platform-eyebrow">{pack.analysis ? 'ANALYSIS SAVED' : 'EVIDENCE ONLY'}</span>
      <h2>{pack.stock_id ? <a href={routeHref('stocks', pack.stock_id, route.query)}>{pack.stock_id}</a> : pack.industry ?? '未指定標的'}</h2>
      <p>截點 {pack.as_of.slice(0, 16).replace('T', ' ')} · run {pack.insight_run_id}</p>
    </div></header>
    {pack.analysis ? <>
      <p className="platform-insight-summary">{pack.analysis.summary}</p>
      <div className="platform-context">
        <span>prompt {pack.analysis.prompt_id}（{pack.analysis.prompt_hash}）</span>
        <span>schema v{pack.analysis.analysis_schema_version}</span>
        <span>模型 {pack.analysis.model_version}</span>
        <span>引用 {pack.analysis.cited_evidence_ids.length} 則</span>
      </div>
    </> : <div className="platform-model-note"><strong>沒有分析</strong>
      <span>{pack.analysis_absent_reason ?? '未記錄缺席原因'}；本頁只顯示已保存的證據，不會補寫分析。</span></div>}
    {pack.evidence.length === 0
      ? <div className="platform-query-state">這個 run 沒有保存任何證據。</div>
      : <EvidenceTable pack={pack} />}
    {gaps.length > 0 && <p className="platform-source-note">
      有 {gaps.length} 項已知缺口列在上表，缺口不會被當成反面證據，也不會被省略。
    </p>}
  </section>
}

/** Read saved insight packs. An offline or missing analysis is shown as such, never generated. */
export function InsightsPage({ route }: { route: PlatformRoute }): JSX.Element {
  const stock = route.query.get('stock')
  const [data, setData] = useState<Response | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    setData(null); setError(null)
    fetchPacks(stock).then(
      value => alive && setData(value),
      err => alive && setError(err instanceof Error ? err.message : String(err)),
    )
    return () => { alive = false }
  }, [stock])
  if (error) return <Panel title="洞悉與證據"><div className="platform-query-state platform-query-error">{error}（讀取失敗，不是「沒有洞悉」）</div></Panel>
  if (!data) return <Panel title="洞悉與證據"><div className="platform-query-state">讀取洞悉證據包…</div></Panel>
  if (data.packs.length === 0) return <Panel title="洞悉與證據"><div className="platform-query-state">
    {stock ? `${stock} 尚未保存任何洞悉證據包。` : '尚未保存任何洞悉證據包。'}
    <br />頁面不會在沒有證據時生成分析。
  </div></Panel>
  return <div>
    <Panel title="洞悉與證據" sub={`${data.packs.length} 份證據包`}>
      {data.packs.map(pack => <PackCard key={pack.insight_run_id} pack={pack} route={route} />)}
    </Panel>
    {data.issues.length > 0 && <div className="platform-artifact-issues">有 {data.issues.length} 份證據包無法讀取，已列為問題項而非丟棄。</div>}
  </div>
}
