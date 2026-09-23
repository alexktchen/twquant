import { useEffect, useState, type JSX } from 'react'
import { routeHref, type PlatformRoute } from './routes'
import { apiUrl } from '../api'
import { nameOf, useStockNames } from './stockNames'
import { Panel } from '../ui'

type Vault = { total_return: number; max_drawdown: number; benchmark_total_return: number; benchmark_max_drawdown: number }
type Research = { decision: string; window_start: string; window_end: string; cagr: number; max_drawdown: number; calmar: number }
type Target = { stock_id: string; target_weight: number; rank: number }
type Run = { code: string; config: string; label: string; note: string; status: 'ok' | 'flat' | 'failed'; detail: string | null; exposure: number; targets: Target[]; vault: Vault | null; research?: Research | null }
type Snapshot = { run_id: string; as_of: string; generated_at: string; mode: 'research'; strategies: Run[] }
type Response = { snapshots: Snapshot[]; issues: { run_id: string; detail: string }[] }

/** 一頁最多先顯示幾檔；其餘收合。名單長短由策略決定，這裡只管版面。 */
const VISIBLE_ROWS = 20

const pct = (v: number, digits = 1): string => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`

async function fetchTargets(date: string | null): Promise<Response> {
  const params = new URLSearchParams()
  if (date) params.set('date', date)
  const response = await fetch(`${apiUrl('/api/platform/strategy-targets')}?${params}`, { headers: { accept: 'application/json' } })
  if (!response.ok) throw new Error(`/api/platform/strategy-targets → HTTP ${response.status}`)
  return response.json() as Promise<Response>
}

function StockName({ stockId }: { stockId: string }) {
  const name = nameOf(useStockNames(), stockId)
  return name ? <span className="platform-stock-name">{name}</span> : null
}

/**
 * 金庫成績條：這支策略在「從沒被看過的半年」裡實際交出什麼。
 * 刻意放在名單上方、每一張卡都有——D-107 判定沒有任何一支可部署，名單不得脫離成績單獨出現。
 */
function Evidence({ vault, research }: { vault: Vault | null; research?: Research | null }) {
  if (!vault && research) return <>
    {/* D-147：研究期成績不是金庫期——整段期間研究者看過、也參與了設計，必須跟數字一起講清楚。 */}
    <div className="platform-stale-note">這支策略沒有金庫期成績。以下是研究期（{research.decision}，walk-forward 樣本外）的回測成績——那段期間研究者看過、也參與了設計，不是未見資料。</div>
    <dl className="platform-evidence">
      <div><dt>研究期年化</dt><dd>{pct(research.cagr)}<span>{research.window_start} ~ {research.window_end}</span></dd></div>
      <div><dt>研究期最深回撤</dt><dd>{pct(research.max_drawdown)}</dd></div>
      <div><dt>Calmar</dt><dd>{research.calmar.toFixed(2)}<span>年化 ÷ 最深回撤</span></dd></div>
    </dl>
  </>
  if (!vault) return <div className="platform-stale-note">這支策略沒有金庫期成績紀錄——無法判斷它在未見資料上的表現。</div>
  const worseReturn = vault.total_return < vault.benchmark_total_return
  const deeperDrawdown = vault.max_drawdown < vault.benchmark_max_drawdown
  const verdict = worseReturn && deeperDrawdown ? '報酬較低，回撤也較深' : worseReturn ? '報酬較低，回撤相近或較淺' : deeperDrawdown ? '報酬較高，但回撤較深' : '兩項都不輸'
  return <dl className="platform-evidence">
    <div><dt>金庫期報酬</dt><dd>{pct(vault.total_return)}<span>同期 0050 {pct(vault.benchmark_total_return)}</span></dd></div>
    <div><dt>金庫期最深回撤</dt><dd>{pct(vault.max_drawdown)}<span>同期 0050 {pct(vault.benchmark_max_drawdown)}</span></dd></div>
    <div><dt>對照結果</dt><dd className={worseReturn && deeperDrawdown ? 'platform-evidence-bad' : undefined}>{verdict}</dd></div>
  </dl>
}

function RunCard({ run, day }: { run: Run; day: string }) {
  const [expanded, setExpanded] = useState(false)
  const rows = expanded ? run.targets : run.targets.slice(0, VISIBLE_ROWS)
  const context = new URLSearchParams({ date: day })
  return <section className="platform-run-card">
    <header><div>
      <span className="platform-eyebrow">RESEARCH ONLY · 未通過部署裁決</span>
      <h2>{run.label}</h2><p>{run.code} · {run.config}</p>
    </div><a href={routeHref('backtests')}>查看回測紀錄 →</a></header>
    {run.note && <p className="platform-strategy-note">{run.note}</p>}
    <Evidence vault={run.vault} research={run.research} />
    {run.status === 'failed' && <div className="platform-query-state platform-query-error">這支策略今天算不出名單：{run.detail}<br />這是計算失敗，不是「今天空手」；其餘策略不受影響。</div>}
    {run.status === 'flat' && <div className="platform-query-state">{run.detail ?? '空手日'}——策略規則下今天沒有合格標的，這是合法結果。</div>}
    {run.status === 'ok' && <>
      <p className="platform-strategy-note">以 {day} 為訊號日的目標部位 {run.targets.length} 檔，合計曝險 {(run.exposure * 100).toFixed(1)}%。這些策略每月再平衡一次，名單是「若今天再平衡會持有什麼」，不是每天都該照著換股。</p>
      <div className="platform-table-wrap"><table className="platform-data-table">
        <thead><tr><th>順位</th><th>股票</th><th>目標權重</th></tr></thead>
        <tbody>{rows.map(t => <tr key={t.stock_id}>
          <td>{t.rank}</td>
          <td><a href={routeHref('stocks', t.stock_id, context)}>{t.stock_id}</a><StockName stockId={t.stock_id} /></td>
          <td>{(t.target_weight * 100).toFixed(2)}%</td>
        </tr>)}</tbody>
      </table></div>
      {run.targets.length > VISIBLE_ROWS && <button type="button" className="platform-link-button" onClick={() => setExpanded(v => !v)}>{expanded ? '收合' : `顯示全部 ${run.targets.length} 檔`}</button>}
    </>}
  </section>
}

/** 已登記策略的每日目標持股（D-112）。只讀已保存的快照；頁面不觸發任何策略計算。 */
export function StrategyTargetsPage({ route }: { route: PlatformRoute }): JSX.Element {
  const date = route.query.get('date')
  const [data, setData] = useState<Response | null>(null); const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true; setData(null); setError(null)
    fetchTargets(date).then(value => alive && setData(value), err => alive && setError(err instanceof Error ? err.message : String(err)))
    return () => { alive = false }
  }, [date])
  const title = '策略持股'
  if (error) return <Panel title={title}><div className="platform-query-state platform-query-error">策略持股快照無法讀取：{error}<br />這是讀取錯誤，不是「今天沒有名單」。</div></Panel>
  if (!data) return <Panel title={title}><div className="platform-query-state">正在讀取已保存的策略持股快照…</div></Panel>
  const snapshot = data.snapshots[0]
  if (!snapshot) return <Panel title={title}><div className="platform-query-state">{date ? `${date} 尚未產生策略持股快照。` : '尚未保存任何策略持股快照。'}<br />快照由每日排程的「策略持股」步驟產生，也可用 <code>twquant strategy-targets</code> 手動補跑。</div></Panel>
  const day = snapshot.as_of.slice(0, 10)
  const counts = { ok: 0, flat: 0, failed: 0 }
  for (const run of snapshot.strategies) counts[run.status] += 1
  return <div className="platform-recommendations">
    <Panel title={title} sub={`訊號日 ${day} · ${snapshot.strategies.length} 支策略（有名單 ${counts.ok}、空手 ${counts.flat}、失敗 ${counts.failed}）`}>
      <div className="platform-stale-note">
        <strong>這一頁是研究輸出，不是買賣建議。</strong>這 8 支策略在金庫期（2026-03-02 ~ 08-31，事前封存、從未用於調整）全部輸給同期的 0050，
        其中集中型的幾支在七月的下跌裡回撤是大盤的兩倍以上。每張卡片上方都附上它自己的成績，請連同名單一起看。
      </div>
      {snapshot.strategies.map(run => <RunCard key={run.code} run={run} day={day} />)}
    </Panel>
    {data.issues.length > 0 && <div className="platform-artifact-issues">有 {data.issues.length} 份策略持股快照無法讀取，已列為問題項而非丟棄。</div>}
  </div>
}
