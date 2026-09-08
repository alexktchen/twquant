import { useEffect, useState, type JSX } from 'react'
import { apiUrl } from '../api'
import { TPanel } from '../stock/ui'
import type { InsightPack } from './InsightsPage'

const DIRECTION_LABEL: Record<string, string> = { support: '支持', contradict: '反對', neutral: '中性', gap: '缺口' }
const STANCE_LABEL: Record<string, string> = { support: '支持', neutral: '中性', conflict: '矛盾', insufficient_data: '資料不足' }
const BUCKET_LABEL: Record<string, string> = { select: '精選', watch: '觀察', exclude: '排除' }

/** One stock's saved insight packs. An absent analysis is stated, never generated here. */
export function StockInsightsPanel({ sid }: { sid: string }): JSX.Element {
  const [packs, setPacks] = useState<InsightPack[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    setPacks(null); setError(null)
    fetch(`${apiUrl('/api/platform/insights')}?stock=${encodeURIComponent(sid)}`, { headers: { accept: 'application/json' } })
      .then(response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json() })
      .then((body: { packs: InsightPack[] }) => alive && setPacks(body.packs), err => alive && setError(String(err)))
    return () => { alive = false }
  }, [sid])
  if (error) return <TPanel title="洞悉與證據"><div className="text-[13px] text-[var(--crit)]">讀取失敗：{error}（不代表這檔沒有洞悉）</div></TPanel>
  if (packs === null) return <TPanel title="洞悉與證據"><div className="text-[13px] text-[var(--muted)]">讀取洞悉證據包…</div></TPanel>
  if (packs.length === 0) return <TPanel title="洞悉與證據"><div className="text-[13px] text-[var(--muted)]">這檔股票尚未保存任何洞悉證據包。</div></TPanel>
  return <TPanel title="洞悉與證據" sub={`${packs.length} 份證據包`}>
    {packs.map(pack => <section key={pack.insight_run_id} className="platform-run-card">
      <header><div>
        <span className="platform-eyebrow">截點 {pack.as_of.slice(0, 10)}</span>
        {pack.analysis
          ? <p>{STANCE_LABEL[pack.analysis.stance ?? ''] ?? '—'}
              {pack.analysis.recommended_bucket && <span className="platform-suggestion">LLM 提議：{BUCKET_LABEL[pack.analysis.recommended_bucket]}</span>}
            </p>
          : <p>無分析：{pack.analysis_absent_reason ?? '未記錄原因'}</p>}
      </div></header>
      {pack.analysis && <p className="platform-insight-summary">{pack.analysis.summary}</p>}
      <div className="platform-table-wrap"><table className="platform-data-table">
        <thead><tr><th>方向</th><th>證據</th><th>可取得時間</th></tr></thead>
        <tbody>{pack.evidence.map(item => <tr key={item.evidence_id}>
          <td className={`platform-direction platform-direction-${item.direction}`}>{DIRECTION_LABEL[item.direction]}</td>
          {/* Evidence text is quoted data; it is rendered, never acted on. */}
          <td>{item.title}{item.availability === 'estimated' && <span className="platform-suggestion">可得時間為估計</span>}</td>
          <td>{item.available_at.slice(0, 16).replace('T', ' ')}</td>
        </tr>)}</tbody>
      </table></div>
    </section>)}
  </TPanel>
}
